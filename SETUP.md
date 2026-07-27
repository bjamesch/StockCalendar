# RPi Zero PhotoPainter — Setup Log

Device: Raspberry Pi Zero 2 W
OS: Debian 13 (trixie), kernel `6.18.34+rpt-rpi-v8`
Set up: 2026-07-26

What this device does today: a 7.3" 6-color e-paper dashboard showing
**Erin's Savings** (the user's 8-year-old daughter's NT$3,000 cash + 10 TSMC
shares, tracked as a combined equity value with a ~30-day history chart),
a mini month calendar, and a 7-day Hsinchu weather forecast — refreshed
automatically, clock/date kept accurate offline via the onboard RTC chip.

This started as a plain offline calendar and evolved through several rounds
of live iteration; this doc reflects the **current** state, not the history.
See git-free change log at the bottom if you want the "why" behind design
choices.

Product reference: https://www.waveshare.com/wiki/RPi_Zero_PhotoPainter

## Hardware

7.3" E6 full-color e-paper panel, 800×480, 6 colors (black/white/red/yellow/
blue/green, no true grey, no partial-refresh mode — every update is a full
~15-30s flash/flicker cycle). Pin mapping used by this board (differs from
Waveshare's generic e-paper demos — **PWR pin is BCM27**, not the usual
BCM18):

| Signal | BCM  | Physical pin |
|--------|------|--------------|
| VCC    | 3.3V | -            |
| GND    | GND  | -            |
| DIN    | MOSI | 19           |
| CLK    | SCLK | 23           |
| CS     | CE0  | 24           |
| DC     | 25   | 22           |
| RST    | 17   | 11           |
| BUSY   | 24   | 18           |
| PWR    | 27   | 13           |

RTC chip: **DS3231** on I2C address `0x68`. The panel is physically mounted
180° from the orientation the driver assumes, so the composed image is
rotated before every push (`PANEL_ROTATION_DEGREES` in `dashboard.py`).

## System setup

- **SPI + I2C + RTC overlay**: enabled via `raspi-config nonint do_spi 0` /
  `do_i2c 0`, plus `dtoverlay=i2c-rtc,ds3231` appended to
  `/boot/firmware/config.txt`. Verified: `/dev/spidev0.0`, `/dev/i2c-1`,
  `/dev/rtc0` present; `timedatectl` shows the RTC and system clock in sync.
  Your user account needs to already be in the `spi`/`i2c`/`gpio` groups,
  so none of the scripts here ever need `sudo`.
- **Passwordless sudo**: the user's explicit choice (offered a narrower
  scoped alternative first) — `/etc/sudoers.d/<username>-nopasswd` grants
  `<username> ALL=(ALL) NOPASSWD:ALL`. Security tradeoff worth remembering:
  any process running as this user gets instant root, no confirmation. To
  undo: `sudo rm /etc/sudoers.d/<username>-nopasswd`.
- **WiFi**: a saved NetworkManager profile for the home WiFi network's SSID,
  autoconnect enabled, priority 10 — added so the device joins automatically
  once brought home, without needing to be near the network it was
  originally configured on.
- **Tailscale**: installed via the official apt repo (trixie), running as
  `tailscaled` (enabled at boot), joined to the user's tailnet under a
  device name of their choosing — reachable at its Tailscale IP or
  `ssh <username>@<tailscale-device-name>` from any other device on the
  tailnet regardless of which physical network the Pi is on.
- **Fonts**: Quicksand Regular / Bold (`fonts-quicksand`, installed via apt,
  `/usr/share/fonts/truetype/quicksand/`) — switched from the original
  DejaVu Sans partway through for a rounder, friendlier look (see "Theme"
  under Dashboard layout below). Verified glyph coverage for everything the
  dashboard actually renders (digits, currency symbols, °, weekday
  letters), but like DejaVu, Quicksand has **no CJK glyphs** — any Chinese
  text (e.g. TWSE's stock name field "台積電") renders as a blank box on
  this panel. All panel text is kept to ASCII (English) for this reason —
  `stock_source.py`'s `name` field is fetched but never actually rendered
  on the panel.

## Project layout — `~/photopainter-calendar/`

```
lib/
  epd7in3e.py, epdconfig.py   # Waveshare e-paper driver (PWR_PIN=27 already fixed)
  INA219.py                    # Waveshare's vendor driver for the onboard battery fuel gauge
dashboard.py                  # main entrypoint: fetch data, compose image, push to panel
stock_source.py               # live/intraday TSMC (2330) quote — TWSE MIS endpoint
stock_history.py              # ~30 trading days of daily closes — TWSE STOCK_DAY endpoint
weather_source.py             # Hsinchu current + 7-day forecast — CWA (Taiwan gov't)
battery_source.py             # PhotoPainter battery % — local I2C read, no network
test_display.py               # standalone hardware sanity check (colored boxes + text)
check_google_finance.py       # standalone, on-demand price cross-check — NOT in the cron pipeline
cwa_api_key.txt                 # secret: CWA Open Data API key (chmod 600, not in git)
.last_clear_date               # state: last date a full epd.Clear() ran
.chart_mode                     # state: which of the two Erin's-panel charts to show next
stock_today.json               # state: today's intraday quote/sample cache
stock_intraday_last.json       # state: last trading day's intraday samples (for the fallback case)
stock_history.json             # state: cached ~30-day daily closes
weather_today.json             # state: cached daily weather
refresh.log                    # cron output, log-rotated weekly (4 weeks kept)
```

## Data sources (stock APIs are free/keyless but unofficial/undocumented and
could change without notice; weather is Taiwan's official CWA API, which
needs a free registered key. Every fetch is wrapped to fall back to cached
data rather than crash the cron job)

- **Stock (live)**: `mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=tse_2330.tw&json=1`
  — TWSE's public realtime quote endpoint. Used for the current price shown
  in the hero number and the up/down status. TWSE reports `"-"` for the
  last-matched-trade field (`z`) during brief gaps between prints, even
  while actively quoting (bid/ask ladders and `prev_close` still valid) —
  `fetch_tsmc_quote()` treats that as "no fresh tick this poll" rather than
  a hard failure, so `prev_close`/name still update and `last_price` simply
  holds at whatever was last known instead of the whole fetch being
  discarded. If TWSE has no fresh price this tick (or fails outright),
  `get_stock_state()` automatically falls back to
  `fetch_tsmc_quote_yahoo()` — Yahoo Finance's chart endpoint
  (`query1.finance.yahoo.com/v8/finance/chart/2330.TW`), also unofficial
  but a clean documented-shape JSON API (the same endpoint the popular
  `yfinance` library uses) rather than scraped HTML. Same ticker/currency
  as the primary source (2330.TW, TWD) — deliberately not the US ADR
  ("TSM"), which trades in USD at a different ratio and would silently
  show the wrong number. The fallback's quote is used wholesale (price +
  prev_close + name together), never mixed field-by-field with a partial
  TWSE result on the same tick. To manually cross-check both feeds against
  a third independent source when something looks stuck, run
  `check_google_finance.py` (standalone, not part of the cron pipeline —
  scrapes an embedded price tuple from Google Finance's page;
  undocumented/fragile by nature, for a one-off sanity check only, not
  something to depend on unattended).
- **Stock (history)**: `www.twse.com.tw/exchangeReport/STOCK_DAY?response=json&date=YYYYMMDD&stockNo=2330`
  — returns one calendar month of daily closes per request; `stock_history.py`
  fetches the current + previous month and keeps the most recent ~30
  calendar days. Dates come back in the ROC calendar (`115/07/01` = 2026-07-01,
  ROC year + 1911 = Gregorian year) and are converted on parse.
- **Weather**: `opendata.cwa.gov.tw/api/v1/rest/datastore/F-D0047-055` — CWA's
  (Taiwan Central Weather Administration) official 1-week/12-hourly township
  forecast, filtered to `locationName=東區` (Hsinchu City's East District, used
  as the city's representative location). Requires the API key in
  `cwa_api_key.txt` (register free at opendata.cwa.gov.tw). "Current"
  conditions are taken from the dataset's nearest upcoming 12-hour period
  (this dataset has no true live observation) — current temp is that
  period's average temperature, not an instantaneous reading. Precipitation
  probability (`rain_pct`) is only forecast by CWA ~3-4 days out; later days
  report `-` and are treated as 0% here rather than shown as unknown.
  CWA's weather-phenomenon codes (1-42) are mapped to short English labels
  in `CWA_LABELS` (`weather_source.py`), built from CWA's own
  "預報產品天氣描述代碼表" reference doc.
  **Cert quirk**: CWA's cert chain is missing a Subject Key Identifier on an
  intermediate cert, which OpenSSL's strict X.509 mode rejects (Python's
  `ssl` module enables it by default on this system's OpenSSL 3.5; `curl`
  doesn't, which is why it works there). `weather_source.py` uses a custom
  `SSLContext` with just that one strict-mode flag cleared — the chain is
  still verified against the trusted CA store otherwise.
- **Battery**: the PhotoPainter's onboard INA219 fuel gauge, read over I2C
  (bus 1, address `0x43`) via `lib/INA219.py` (Waveshare's vendor driver,
  copied unmodified from their `RPi_Zero_PhotoPainter` demo package).
  `battery_source.py` reads the bus (load-side) voltage and converts it to a
  percentage assuming a single Li-ion/LiPo cell, 3.0V = empty / 4.2V = full
  (same formula as Waveshare's own demo). It's a cheap local read with no
  network involved, so — unlike weather/stock — it's read fresh every tick
  rather than cached, and shown as small text in the top-right corner of
  the panel. Requires `raspi-config` → Interface Options → I2C enabled;
  if the chip can't be read (e.g. running on a bare Pi without the
  PhotoPainter battery board), it's just omitted rather than shown as an
  error.

**Correctness details baked into the code, worth knowing if you touch it:**
- **Taiwan color convention**: red = price up, green = price down (opposite
  of US markets). Used for the "Went up/down today" status text.
- **Holiday/weekend detection**: intraday samples are only recorded when the
  API's own returned trade date equals today's date (not just a weekday+time
  check) — so a market holiday correctly shows "market closed" instead of a
  fake flat line.
- **Erin's equity** = `NT$3,000 (fixed) + 10 × TSMC price`. No allowance/
  deposit tracking — if that cash amount ever needs to change, it's the
  `ERIN_CASH_NT` constant at the top of `dashboard.py`.

## Dashboard layout (800×480, no header — the mini calendar already shows
the date, so a separate date/time banner was removed as redundant)

- **Main area** (left, large): "Erin's Savings" panel — title, plain-language
  subtitle ("NT$3,000 saved + 10 TSMC shares"), a price label line, hero NT$
  equity figure, plain-English up/down status (no percentages — written for
  an 8-year-old) with a small hand-drawn mood-cat next to it
  (happy/sad/neutral, `draw_mood_cat()`), market-closed note when
  applicable, and a ~30-day value chart below. The price label switches
  wording depending on whether the market is genuinely open right now
  (`is_live` in `draw_erin_panel()`, requires a fresh price *and* it being
  an actual trading day *and* `stock_source.in_trading_window(now)`):
  "TSMC now at NT$X" while live, vs. "TSMC closed at NT$X" once trading
  hours end (09:00-13:30 Taipei) — using that same session's final price,
  not yesterday's — or when there's no live price at all yet, in which case
  it falls back to `stock_history.py`'s cached daily closes (available even
  before today's live price arrives). The chart (and the price label)
  render even when today's live price hasn't come in yet — only the hero
  number/status/mood-cat are skipped in that case, in favor of a small
  "Waiting for today's price..." note. Chart style (`draw_history_chart()`):
  soft dotted gridlines and labeled tick values on both axes (5 NT$ levels,
  up to 5 dates, no separate axis-title words since the tick values already
  carry "NT$"/date formatting), a stippled blue dot-texture fill under the
  line (no true pastel/alpha fill is possible on this 6-color panel, so
  this is a light "hill" texture rather than a solid wash — same technique
  as the dotted gridlines, just applied vertically), and the line itself
  colored per segment (red = up, green = down, same Taiwan convention as
  the status text/mood-cat) rather than one uniform color. The most recent
  point gets a bigger black-outlined highlight dot ("you are here"), plus a
  small hand-drawn star above it (`_draw_star()`, same polygon technique as
  the weather icons/mood-cat) when it's a new high for the shown window
  (both charts share this via `_draw_value_line()`, the common plotting
  core — see below).

  **Two charts, alternating every redraw**: the panel swaps between the
  30-day history chart above and a second chart, today's intraday
  movement (`draw_intraday_chart()`), flipping on every single physical
  panel redraw (tracked in `.chart_mode`, flipped only when a redraw
  actually happens — data-fetch-only ticks don't advance it). The intraday
  chart's x-axis is **fixed to the 09:00-13:30 trading window** rather than
  auto-scaled to whatever's been collected so far, so the line visibly
  grows rightward across the session as ticks accumulate rather than
  always stretching to fill the plot. Built from `stock_today.json`'s
  `samples` list (`[["HH:MM", price], ...]`, appended on every trading-hour
  tick — this had been silently collected all along but never rendered
  until now). Before market open, or on a non-trading day, there's no data
  for today yet — `stock_source.load_intraday_chart_data()` falls back to
  the last trading day that has samples (persisted to
  `stock_intraday_last.json` at the moment each day rolls over, since
  `stock_today.json` itself resets fresh daily) rather than showing an
  empty chart; a small `(MM-DD)` label appears (top-left, positioned there
  specifically because the highlighted latest-point dot/star always lands
  at the plot's right edge by construction, so top-left is the one corner
  guaranteed not to collide with it) whenever the shown day isn't today, so
  it doesn't read as live when it isn't. **Y-axis is the raw TSMC share
  price** (e.g. "NT$2,350"), not the Erin's-savings equity value the
  history chart uses — this chart is deliberately about the stock's own
  intraday movement, not the combined savings figure. Since that's just
  the equity transformation (`ERIN_CASH_NT + price × ERIN_TSMC_SHARES`)
  removed, and it's linear, the line's shape/red-green coloring/highlight
  logic are all identical either way — only the axis numbers changed.
- **Sidebar** (right, slim): mini month-grid calendar (today highlighted
  green with rounded corners, Sat blue / Sun red, same convention as the
  original calendar-only version) stacked above a 7-day Hsinchu forecast
  (each day colored the same Sat/Sun convention, with a small hand-drawn
  weather-condition icon per day — `draw_weather_icon()`, mapped from CWA's
  weather codes via `weather_source.icon_category()`). Both sections are
  wrapped in a soft rounded-rect "card" border (`draw_card()`) instead of
  the plain straight divider lines used originally.
- **Theme**: everything renders in Quicksand (`fonts-quicksand`, installed
  via apt) instead of DejaVu Sans — a rounder, friendlier font chosen
  specifically to make the panel feel warmer for an 8-year-old, while still
  covering every glyph the dashboard actually uses (verified: digits,
  currency symbols, °, weekday letters). The panel's 6-color ACeP hardware
  (black/white/yellow/red/blue/green only) means "warmer" comes from
  rounded shapes and the mascot rather than color choices — pastels aren't
  possible on this display.
- **Battery indicator**: small "Batt X%" text, top-right corner, from the
  onboard INA219 fuel gauge (`battery_source.py`) — see
  [BATTERY_OPTIMIZATION.md](BATTERY_OPTIMIZATION.md).

## Cron / refresh cadence

Replace `YOUR_USERNAME` below with your actual Linux username (cron's
command field doesn't reliably expand `~`, so this needs an absolute path):

```
*/10 9-12 * * 1-5 /usr/bin/python3 /home/YOUR_USERNAME/photopainter-calendar/dashboard.py >> /home/YOUR_USERNAME/photopainter-calendar/refresh.log 2>&1
0,10,20,30 13 * * 1-5 /usr/bin/python3 /home/YOUR_USERNAME/photopainter-calendar/dashboard.py >> /home/YOUR_USERNAME/photopainter-calendar/refresh.log 2>&1
*/30 0-8,14-23 * * 1-5 /usr/bin/python3 /home/YOUR_USERNAME/photopainter-calendar/dashboard.py >> /home/YOUR_USERNAME/photopainter-calendar/refresh.log 2>&1
*/30 * * * 0,6 /usr/bin/python3 /home/YOUR_USERNAME/photopainter-calendar/dashboard.py >> /home/YOUR_USERNAME/photopainter-calendar/refresh.log 2>&1
```

Four non-overlapping rules, split so the refresh rate itself tracks Taipei
trading hours (09:00-13:30, Mon-Fri) rather than relying on a single
uniform interval:
- Rule 1 covers 9:00-12:50 in 10-minute steps.
- Rule 2 covers the remainder of trading hours, 13:00/13:10/13:20/13:30 —
  cron's hour field can't express "until 13:30", so this is spelled out
  explicitly as fixed minutes within hour 13 (there's no `*/10 13` because
  that would overshoot to 13:40/13:50, past the 13:30 close).
- Rule 3 covers the rest of weekday hours (0-8, 14-23) every 30 minutes.
- Rule 4 covers weekends (cron day-of-week 0=Sun, 6=Sat) every 30 minutes,
  all hours.

Verified by simulating a full week minute-by-minute against all four rules
(`python3` one-liner, not checked in) — 140 trading-hour ticks/week, 286
off-hour ticks/week, zero double-fires or gaps. System timezone is
confirmed set to `Asia/Taipei`, so cron's local-time scheduling lines up
directly with `stock_source.py`'s trading window — no `CRON_TZ` override
needed.

The previous single-interval schedule (`*/30` uniformly, chosen as a
battery-life optimization — see
[BATTERY_OPTIMIZATION.md](BATTERY_OPTIMIZATION.md)) undersold trading-hours
freshness to save power off-hours. This restores full 10-minute freshness
specifically when it matters (the stock market is actually open) while
keeping the coarser 30-minute cadence — and its battery savings — for the
other ~20 hours of the day.

All the "should we actually touch the panel this tick" logic lives in
`should_redraw()` inside `dashboard.py` — the panel is only physically
redrawn during Taipei trading hours (09:00-13:30, Mon-Fri, matching the
cron tick for a fresh sample each time), on the :00/:30 marks otherwise, or
on the first tick of a new day. Data fetching and state-file updates happen
on every tick regardless — only the slow, flickery physical redraw is
gated. `epd.Clear()` (a second full-panel flash that resets pigment
particles and prevents ghosting) runs once per calendar day, not every
redraw. `stock_source.py`'s TWSE fetch is additionally skipped entirely
outside the trading window (also covered in BATTERY_OPTIMIZATION.md).

**Note for future-you**: this cron line was briefly broken (pointing at a
renamed/deleted script) between when `calendar_display.py` was renamed to
`dashboard.py` and when this was caught — if the panel ever looks stuck,
`crontab -l` and `tail refresh.log` are the first things to check.

## Verification performed

- Manual renders pushed to the physical panel and visually confirmed after
  every layout change in this session (orientation, chart axes, colors,
  font sizes, weekend coloring, etc.).
- Sanity-checked the hero equity number by hand against `3000 + price × 10`.
- Simulated a total network failure (monkey-patched `urllib.request.urlopen`)
  and confirmed all three data sources (`stock_source`, `stock_history`,
  `weather_source`) log a warning and fall back to cached state instead of
  crashing.
- Confirmed the "market closed" path renders correctly (exercised naturally
  since this was built on a Sunday).

## Possible follow-ups (not done, just noted)

- No error handling for RTC drift over long unpowered periods — if the coin
  cell backup battery ever dies, the clock resets and needs re-syncing
  (network briefly for NTP, or set manually with `date`).
- No systemd watchdog/retry if a cron run fails outright (e.g. SPI transient
  error) — check `refresh.log` if the display seems stuck.
- True sleep/wake between refreshes (Pi fully powered off, not just idle)
  needs an add-on RTC power-cutoff board — this hardware has no way to do
  it on its own. Full investigation and recommended options in
  [BATTERY_OPTIMIZATION.md](BATTERY_OPTIMIZATION.md).

## Change log (the "why" behind how the current state was reached)

Roughly chronological. Everything above describes *what* the dashboard
does today; this is *why* it got there, for whenever a design choice looks
puzzling out of context.

1. **Initial build**: plain offline calendar → grew into the full
   dashboard (Erin's Savings + calendar + weather), stock/weather/RTC/SPI
   wiring, initial layout.
2. **Weather source switch**: Open-Meteo (generic, keyless) → Taiwan's
   official CWA API, for a more authoritative/localized Hsinchu forecast —
   traded keylessness for a free registered API key and had to work around
   a cert chain quirk (missing Subject Key Identifier) along the way.
3. **Battery indicator added**: discovered the onboard INA219 fuel gauge
   was physically present but unused; wired it into the corner of the
   panel.
4. **First battery-optimization pass**: cron `*/10` → `*/30`, disabled
   unused services (Bluetooth/Avahi/serial console), trimmed
   `config.txt` peripherals — see BATTERY_OPTIMIZATION.md.
5. **"Cuter" pass for Erin**: switched DejaVu → Quicksand, added the
   hand-drawn mood-cat mascot, rounded the calendar's today-cell and
   wrapped sidebar sections in soft card borders — replacing the original
   plain/technical styling.
6. **Price display fixes**: the "closed at" price used to disappear
   whenever today's live price wasn't available yet; changed to fall back
   to cached daily-close history so it's always shown. Later refined further
   to distinguish "TSMC now at" (genuinely live, mid-trading-hours) from
   "TSMC closed at" (after 13:30, or no live price at all) rather than
   always saying "closed" even while the market was actively open.
7. **Chart made friendlier**: stippled area fill under the line (no true
   pastel fill is possible on this 6-color panel, so this is a dot-texture
   "hill" instead), line colored red/green per segment instead of one
   uniform blue, today's point highlighted bigger with a new-high star.
8. **Reliability fixes after a stuck TWSE feed**: found TWSE's live quote
   endpoint can report `"-"` for the last-trade price for extended periods
   even while the market is genuinely active — fixed the code to stop
   discarding `prev_close`/name when that happens, then added an automatic
   Yahoo Finance fallback for when TWSE has nothing fresh at all. Also
   caught and fixed a real incident along the way: an earlier mocked unit
   test had written fake price data directly into the production state
   file, which then rendered on the physical panel for hours before being
   noticed and cleared.
9. **Cron schedule rewrite**: uniform `*/30` → four rules tracking Taipei
   trading hours directly (10-minute ticks during 09:00-13:30 Mon-Fri,
   30-minute ticks otherwise) — restores freshness when it matters without
   giving up the off-hours battery savings from the first optimization
   pass.
10. **Second battery-optimization pass**: after confirming this hardware
    has no sleep state at all (`/sys/power/state` empty), went deeper on
    idle-power reduction within the always-on model — disabled Raspberry
    Pi Connect, removed the GPU/display driver entirely (confirmed safe
    since the e-paper panel is pure SPI), disabled the ACT status LED, and
    switched the system journal to volatile (RAM-only) storage. Full
    detail in BATTERY_OPTIMIZATION.md.
11. **Intraday chart added, alternating with the history chart**: the
    `samples` intraday data had been collected every trading day since
    early in the session but never rendered (see follow-up note below,
    now resolved) — added `draw_intraday_chart()` (fixed 09:00-13:30
    x-axis, falls back to the last trading day with data via a new
    `stock_intraday_last.json` persisted at each day's rollover) and a
    `.chart_mode` toggle so the panel alternates between it and the
    30-day history chart on every redraw. Refactored the shared stippled-
    fill/colored-segments/highlight-dot-and-star plotting logic out of
    `draw_history_chart()` into `_draw_value_line()` so both charts use
    the same code rather than duplicating it.
