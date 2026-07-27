# PhotoPainter Dashboard

A battery-powered 7.3" e-paper dashboard built on Waveshare's
[RPi Zero PhotoPainter](https://www.waveshare.com/wiki/RPi_Zero_PhotoPainter)
— a Raspberry Pi Zero 2 W driving a 6-color ACeP display. Refreshes
automatically via cron, keeps time offline through an onboard RTC, and
degrades gracefully to cached data whenever any of its network sources are
unreachable.

## What it shows

- **A savings tracker** — a fixed cash amount plus a stock holding, tracked
  as a combined value with an alternating chart (30-day history / today's
  intraday movement), a small hand-drawn mood-cat that reacts to the day's
  move, and plain-language status text instead of raw percentages.
- **A 7-day weather forecast** with hand-drawn condition icons, sourced
  from Taiwan's official CWA Open Data API.
- **A mini month calendar**, today highlighted.
- **A battery percentage readout**, read live from the onboard INA219 fuel
  gauge.

Everything is deliberately styled to be simple and readable at a glance —
a rounded font, soft card borders, and a calm color palette rather than a
dense data-heavy layout.

## Hardware

- Raspberry Pi Zero 2 W
- Waveshare 7.3" ACeP e-paper panel (800×480, 6 colors, no partial refresh)
- DS3231 RTC (offline timekeeping)
- INA219 fuel gauge (battery monitoring)
- Full board reference: [Waveshare RPi Zero PhotoPainter wiki](https://www.waveshare.com/wiki/RPi_Zero_PhotoPainter)

## Setup

You'll need your own:
- CWA Open Data API key (free registration at
  [opendata.cwa.gov.tw](https://opendata.cwa.gov.tw)) — save it to
  `cwa_api_key.txt` next to `dashboard.py` (this file is gitignored,
  never committed)
- A cron entry to run `dashboard.py` on your own schedule (see
  [SETUP.md](SETUP.md)'s "Cron / refresh cadence" section for the
  trading-hours-aware schedule this project actually uses)

Full setup log, architecture notes, and every design decision's rationale:
see **[SETUP.md](SETUP.md)**. Battery-life investigation and optimization
work: see **[BATTERY_OPTIMIZATION.md](BATTERY_OPTIMIZATION.md)**.

## Project layout

```
dashboard.py           # main entrypoint: fetch data, compose image, push to panel
stock_source.py        # live/intraday stock quote (TWSE + Yahoo Finance fallback)
stock_history.py       # ~30 trading days of daily closes
weather_source.py      # current + 7-day forecast (Taiwan CWA)
battery_source.py      # battery % via onboard INA219 fuel gauge
check_google_finance.py  # standalone on-demand price cross-check, not in the cron pipeline
test_display.py        # standalone hardware sanity check
lib/                   # Waveshare e-paper driver + INA219 vendor driver
```

This project is specific to the TSMC (2330.TW)/NT$/Taipei-trading-hours
use case it was originally built for, but the structure (a data source per
`*_source.py` module, each with its own graceful-degradation/caching, and
a single `dashboard.py` that composes and pushes the final image) should
adapt readily to a different stock, currency, or region.
