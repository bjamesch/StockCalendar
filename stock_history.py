"""Daily closing-price history for TSMC (2330.TW), used for the ~30-day
value trend chart. Live/intraday price still comes from stock_source.py --
this module is only for the longer-range chart.

Source: TWSE's public per-stock daily-report endpoint (unofficial, keyless).
It returns one calendar month of daily OHLC per request, so a ~30-day window
needs the current month plus the previous month, merged."""
import json
import logging
import urllib.request
from datetime import timedelta
from pathlib import Path

STATE_FILE = Path(__file__).resolve().parent / "stock_history.json"

TICKER = "2330"
URL_TMPL = "https://www.twse.com.tw/exchangeReport/STOCK_DAY?response=json&date={date}&stockNo=" + TICKER

HISTORY_DAYS = 30


def _fetch_month(first_of_month):
    date_str = first_of_month.strftime("%Y%m%d")
    req = urllib.request.Request(
        URL_TMPL.format(date=date_str),
        headers={"User-Agent": "photopainter-dashboard/1.0"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())

    if data.get("stat") != "OK":
        return []

    rows = []
    for row in data["data"]:
        roc_year, month, day = row[0].split("/")
        greg_year = int(roc_year) + 1911
        close = float(row[6].replace(",", ""))
        rows.append({"date": f"{greg_year:04d}-{int(month):02d}-{int(day):02d}", "close": close})
    return rows


def fetch_recent_history(now, days=HISTORY_DAYS):
    this_month_first = now.replace(day=1)
    prev_month_last = this_month_first - timedelta(days=1)
    prev_month_first = prev_month_last.replace(day=1)

    rows = _fetch_month(prev_month_first) + _fetch_month(this_month_first)
    cutoff = (now - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = [r for r in rows if r["date"] >= cutoff]
    rows.sort(key=lambda r: r["date"])
    return rows


def load_history(today_str, now):
    """Cached once per day. On failure, falls back to a stale cache, or an
    empty list if there's nothing cached yet -- callers must handle that
    (show a placeholder, never crash)."""
    cached = None
    if STATE_FILE.exists():
        try:
            cached = json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            cached = None

    if cached and cached.get("date") == today_str:
        return cached["rows"]

    try:
        rows = fetch_recent_history(now)
    except Exception:
        logging.warning("Stock history fetch failed, falling back to cache", exc_info=True)
        return cached["rows"] if cached else []

    STATE_FILE.write_text(json.dumps({"date": today_str, "rows": rows}))
    return rows
