# Battery optimization — investigation and changes log

Companion to [SETUP.md](SETUP.md). Covers what was tried to extend runtime
between charges, what was actually changed, what was ruled out and why, and
the real next step if longer runtime is still wanted.

## Hardware, confirmed

This board is Waveshare's **RPi Zero PhotoPainter** (not the RP2040-native
"PhotoPainter" — a different product with different power-management
hardware; easy to confuse since the names/wiki pages are similar).

Confirmed via spec sheet + direct inspection of the running device (`i2cdetect`
on both I2C buses, `dmesg`, `gpioinfo`):
- **SBC**: Raspberry Pi Zero 2 W, Debian 13 (trixie)
- **RTC**: DS3231 at I2C `0x68` — timekeeping only, see "Why not sleep" below
- **Fuel gauge**: INA219 at I2C `0x43` (`lib/INA219.py`, `battery_source.py`)
- **Battery**: 3.7V 1500mAh Li-ion
- **Charge/boost**: ETA6003 (charger) + SCT12A0DHKR (5V boost) — no
  scheduling/switching capability, just charge and step-up
- No AXP2101, no PCF85063, no auto-wake switch anywhere on either I2C bus
  (`i2cdetect -y 1` and `-y 2` both checked) — that circuit belongs to the
  unrelated RP2040-native PhotoPainter board, not this one.

## Changes made (all live and confirmed surviving reboots)

### First pass
1. **`stock_source.py`** — `get_stock_state()` now skips the TWSE network
   fetch entirely when `not in_trading_window(now)`, returning cached state
   instead of hitting the network on every tick around the clock. Previously
   fetched unconditionally 24/7 even though the price can't move outside
   trading hours.
2. **Services disabled** (`systemctl disable --now`): `bluetooth.service`,
   `avahi-daemon.service` + `avahi-daemon.socket`, `serial-getty@ttyS0.service`.
   None are used by this project (I2C/SPI + external HTTPS only; Tailscale
   is the remote-access path, not mDNS).
3. **`/boot/firmware/config.txt`** (backup at `config.txt.bak-20260726`):
   - `dtparam=audio=on` → `off` (unused onboard codec)
   - `camera_auto_detect=1` → `0`, `display_auto_detect=1` → `0` (no camera
     or DSI display attached — panel is driven over SPI)
   - `enable_uart=1` → `0` (serial console unused)
   - added `dtoverlay=disable-bt` (cuts Bluetooth radio at the firmware
     level, not just the service)
4. **Cron**: `*/10 * * * *` → `*/30 * * * *` uniformly (later superseded —
   see "Cron schedule" below).

### Second pass — deeper idle-power reduction
Prompted by confirming (see "Why real sleep/wake isn't possible today")
that this hardware has no sleep state at all, so every watt not spent on
something essential matters more than it would on a device that could
otherwise nap between refreshes. Verified each item directly on the device
before changing it, rather than assuming:
5. **`rpi-connect.service` disabled** (`systemctl --user -M <username>@
   disable --now`) — a *user*-level systemd unit (Raspberry Pi Connect,
   separate from Tailscale/SSH) confirmed running and confirmed unused.
   Tailscale/SSH access re-verified working immediately after.
6. **`dtoverlay=vc4-kms-v3d` and `max_framebuffers=2` removed entirely**
   from `config.txt` — reverses the original "deliberately not touched"
   call from the first pass. Re-examined after confirming
   `lib/epdconfig.py` is pure `spidev` with zero DRM/framebuffer
   dependency (grepped the driver code directly) — the e-paper panel
   cannot be affected by removing the GPU driver. Verified with a real
   end-to-end dashboard push *after* rebooting with the driver gone, not
   just reasoned about from the code. The one real trade-off: no HDMI
   output at all anymore without re-adding the overlay first (`/dev/dri`
   no longer exists) — accepted, since this device has never had a
   monitor plugged in.
7. **ACT status LED disabled**: added `dtparam=act_led_trigger=none` to
   `config.txt` (was `actpwr`, blinking on every disk access).
8. **journald switched to volatile storage**: `Storage=volatile` in
   `/etc/systemd/journald.conf` (was persistent/disk-backed — confirmed
   `/var/log/journal` existed). System journal now lives in RAM only
   (`/run/log/journal`), cleared on reboot. Doesn't affect
   `refresh.log` — that's the cron job's own output file, unrelated to
   journald.

Deliberately **not** touched, checked and confirmed fine as-is rather than
assumed: CPU governor (already `ondemand` on all 4 cores — verified via
`scaling_governor`, not capped/pinned), and `zram` swap (RAM-based, not
SD-card-backed, and genuinely needed — this device runs tight on memory,
~26MB free of 415MB total in one snapshot — disabling it would risk OOM
for no power benefit).

## Cron schedule

Rewritten since the first pass — no longer a single uniform interval.
Refresh rate now tracks Taipei trading hours directly (10-minute ticks
09:00-13:30 Mon-Fri, 30-minute ticks the rest of the time). Full detail —
the exact four cron rules, why cron's hour field needed splitting across
two of them, and the full-week simulation that verified zero gaps/overlaps
— lives in [SETUP.md](SETUP.md)'s "Cron / refresh cadence" section rather
than duplicated here, since it's as much a data-freshness decision as a
battery one.

## Current draw — measured, with important caveats

INA219 readings on battery (charger unplugged) averaged **~230mA** at
~4.0V (range 176–400mA, bursty). Against the 1500mAh battery that's roughly
**6-8 hours** of runtime.

**Caveats, worth remembering**:
- These readings were all taken while a live interactive Claude Code
  session (`ccd-cli` + `rpi-connectd` + a remote server process) was
  running directly on this Pi — visible in `ps aux` at ~12% CPU / ~200MB
  resident just for the CLI process. That's real continuous overhead on
  top of `dashboard.py`'s normal unattended workload, and there's no way
  to measure "just the dashboard, nothing else connected" while the thing
  doing the measuring is itself a process running on the device. So
  **6-8 hours is likely pessimistic** for true unattended operation — how
  much better wasn't nailed down (the user chose not to chase a clean
  logged measurement at the time).
- This measurement also **predates the entire second pass** (rpi-connect
  disabled, GPU driver removed, ACT LED off, journald volatile) — real
  unattended draw today is very likely lower than this number reflects,
  on top of the session-overhead caveat above. Not re-measured after the
  second pass for the same reason as the first: doing so accurately would
  need a clean run with no active remote session connected.

If a clean number is ever wanted: add a small cron job (e.g. every 5 min)
that appends an INA219 reading to a CSV, let it run for a stretch with no
active remote session connected, then review the log.

## Why real sleep/wake isn't possible today

Checked four independent ways, all agreeing:
1. `i2cdetect` on both buses: no AXP2101, no PCF85063.
2. `dmesg`: zero mentions of either chip.
3. `gpioinfo`: no GPIO line claimed by an RTC interrupt signal — the DS3231's
   INT/SQW pin isn't wired to anything.
4. `/sys/power/state` is **empty** — this kernel has no suspend states
   registered at all, so even OS-level sleep isn't available, separate from
   the wake-hardware question.

Bottom line: the DS3231 can raise an alarm in principle, but nothing on this
board listens for it, and there's no MOSFET/relay to physically cut Pi power
even if something did. Software alone cannot get this Pi below its idle
power floor.

## The real next step: an RTC power-cutoff board

Not purchased/installed — this is what would actually get runtime from
hours to days. Two realistic options, both fitting the Pi Zero 2 W form
factor:

- **Witty Pi 4 L3V7** — sits on the 40-pin GPIO header (needs a cheap tall
  stacking header so the e-paper HAT can still connect on top — a real-world
  Pi Zero → Witty Pi 4 L3V7 → Waveshare e-paper stack is documented
  elsewhere, so this is known to work). Natively accepts a single-cell
  3.7V LiPo, so there's a real chance the existing 1500mAh battery can be
  reused rather than replaced (connector compatibility not yet checked).
  **Recommended fit for this build.**
- **PiSugar 3** — connects via pogo pins on the back of the Pi, doesn't
  touch the GPIO header at all (simpler install, e-paper HAT totally
  undisturbed). Comes with its own 1200mAh battery and its own I2C fuel
  gauge, though — would likely mean retiring the existing battery/INA219
  rather than running both.

Neither option's physical fit inside the wooden PhotoPainter housing has
been verified — worth measuring clearance before ordering either.

### Software model change this would require

Cutoff boards work by fully powering the Pi off between cycles, so the
cron-loop model goes away entirely:

- Replace the cron job with a **systemd service run once at boot**
  (`After=network-online.target` so WiFi is up before any fetch is
  attempted)
- `dashboard.py` would run its existing fetch-and-render logic
  unconditionally every wake (the `should_redraw()` minute-matching gate
  becomes unnecessary — every wake already *is* the trigger)
- After `epd.sleep()`, trigger a clean shutdown via whichever hook the
  chosen board documents (both Witty Pi and PiSugar have a "safe to cut
  power" signal the Pi sends before the board actually removes power)
- The board's own RTC schedules the next wake

Real-world overhead per cycle: Pi Zero 2 W boot + WiFi association + fetch/
render + the e-paper panel's own ~20-second refresh, roughly 60-90 seconds
total awake time. At a 30-minute cadence that's still a ~95%+ reduction in
awake time versus staying powered on continuously.

Not written yet — draft this (systemd unit + shutdown-hook script) once
hardware is actually chosen and in hand, rather than against untested
assumptions about a specific board's exact shutdown-signal mechanism.
