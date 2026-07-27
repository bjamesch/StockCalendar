#!/usr/bin/env python3
"""Standalone, on-demand check of TSMC's price via Google Finance -- NOT
part of the cron pipeline and not wired into dashboard.py/stock_source.py.

Google Finance has no public API; this scrapes a timestamped price tuple
embedded in the page's initial-state JSON blob (used to render their
intraday chart). That's undocumented internal structure Google could
change or break at any time without notice, so this is only meant for a
manual point-in-time check (e.g. to sanity-check stock_source.py's TWSE
feed when it looks stuck) -- not something to depend on unattended.

Usage: python3 check_google_finance.py
"""
import re
import urllib.request
from datetime import datetime

URL = "https://www.google.com/finance/quote/2330:TPE"

# Matches the embedded [[year,month,day,hour,minute,null,null,[utc_offset_
# seconds]],[price,point_change,fraction_change,...] tuples Google Finance
# uses to feed its intraday/historical chart -- both today's minute-by-
# minute ticks and past trading days' closes appear in this same shape.
TUPLE_RE = re.compile(
    r"\[\[(\d{4}),(\d{1,2}),(\d{1,2}),(\d{1,2}),(\d{1,2}),null,null,\[-?\d+\]\],"
    r"\[([\d.]+),(-?[\d.]+),(-?[\d.]+)"
)


def fetch_latest_price():
    req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        html = resp.read().decode("utf-8", errors="replace")

    points = []
    for year, month, day, hour, minute, price, point_change, frac_change in TUPLE_RE.findall(html):
        ts = datetime(int(year), int(month), int(day), int(hour), int(minute))
        points.append((ts, float(price), float(point_change), float(frac_change)))

    if not points:
        raise RuntimeError("No price data found -- Google Finance's page structure may have changed")

    return max(points, key=lambda p: p[0])


if __name__ == "__main__":
    ts, price, point_change, frac_change = fetch_latest_price()
    prev_close = price - point_change
    direction = "up" if point_change > 0 else ("down" if point_change < 0 else "flat")
    print(f"TSMC (2330.TW) as of {ts:%Y-%m-%d %H:%M} (Taipei time)")
    print(f"  Price:       NT${price:,.2f}")
    print(f"  Prev close:  NT${prev_close:,.2f}")
    print(f"  Change:      {direction} NT${abs(point_change):,.2f} ({frac_change:+.2%})")
