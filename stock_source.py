"""TSMC (2330.TW) intraday price tracking via the Taiwan Stock Exchange's
public realtime-quote endpoint (unofficial, but the only free/keyless source
of intraday data - TWSE's official OpenAPI is end-of-day only).

Samples are only recorded during the actual Taipei trading window AND when
the API's own returned trade date matches today - that second check is what
keeps weekends/market holidays from producing a fake flat intraday line.

If TWSE has no fresh price this tick, falls back to Yahoo Finance's chart
endpoint (also unofficial, but a clean documented-shape JSON API rather
than scraped HTML, and the same yfinance library many other projects rely
on uses this same endpoint) -- see fetch_tsmc_quote_yahoo()."""
import json
import logging
import urllib.request
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

STATE_FILE = Path(__file__).resolve().parent / "stock_today.json"
LAST_INTRADAY_FILE = Path(__file__).resolve().parent / "stock_intraday_last.json"

TICKER = "tse_2330.tw"
URL = f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch={TICKER}&json=1&delay=0"

YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/2330.TW"

TRADING_START = time(9, 0)
TRADING_END = time(13, 30)


def in_trading_window(now):
    return now.weekday() < 5 and TRADING_START <= now.time() <= TRADING_END


def next_trading_day_name(now):
    """Weekday name of the next trading day after `now` (Mon-Fri) -- e.g.
    'Monday' from a Saturday or Sunday. Used for the "waiting for X's
    price" label so it doesn't say "today" on a day with no trading."""
    d = now
    while True:
        d += timedelta(days=1)
        if d.weekday() < 5:
            return d.strftime("%A")


def fetch_tsmc_quote():
    req = urllib.request.Request(URL, headers={"User-Agent": "photopainter-dashboard/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())

    row = data["msgArray"][0]
    trade_date = f"{row['d'][0:4]}-{row['d'][4:6]}-{row['d'][6:8]}"
    # TWSE reports "-" for the last-matched-trade field ("z") during brief
    # gaps between prints, even while the market is actively quoting (bid/
    # ask ladders still populated, prev_close still valid) - treat that as
    # "no fresh tick this poll", not a hard failure that discards everything.
    price = None if row["z"] == "-" else float(row["z"])
    return {
        "price": price,
        "prev_close": float(row["y"]),
        "name": row["n"],
        "trade_date": trade_date,
        "trade_time": row["t"],
    }


def fetch_tsmc_quote_yahoo():
    """Fallback source for when TWSE has no fresh price this tick. Same
    ticker/currency/exchange as the primary source (2330.TW, TWD) -- not
    the US ADR ("TSM", a different security at a different ratio/currency
    that would silently show the wrong number)."""
    req = urllib.request.Request(YAHOO_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())

    meta = data["chart"]["result"][0]["meta"]
    trade_dt = datetime.fromtimestamp(meta["regularMarketTime"],
                                       tz=timezone(timedelta(seconds=meta["gmtoffset"])))
    return {
        "price": meta["regularMarketPrice"],
        "prev_close": meta["previousClose"],
        "name": meta.get("longName", "TSMC"),
        "trade_date": trade_dt.strftime("%Y-%m-%d"),
        "trade_time": trade_dt.strftime("%H:%M:%S"),
    }


def _fresh_state(today_str):
    return {"date": today_str, "prev_close": None, "name": None,
            "last_price": None, "last_quote_date": None, "samples": []}


def _load_trace(today_str):
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            state = None
        if state and state.get("date") == today_str:
            return state
        # Rolling over to a new day -- preserve the outgoing day's intraday
        # samples (if any) before they're discarded, so the intraday chart
        # still has something to show on days with no data yet/at all
        # (pre-market, weekends, holidays). See load_intraday_chart_data().
        if state and state.get("samples"):
            LAST_INTRADAY_FILE.write_text(json.dumps({
                "date": state["date"], "samples": state["samples"],
            }))
    return _fresh_state(today_str)


def load_intraday_chart_data(state):
    """Returns (date_str, samples) for the intraday chart: today's samples
    if any exist yet, otherwise the last trading day that has saved
    samples, otherwise (None, []) (only possible on a brand new install
    with nothing recorded yet)."""
    if state.get("samples"):
        return state["date"], state["samples"]
    if LAST_INTRADAY_FILE.exists():
        try:
            saved = json.loads(LAST_INTRADAY_FILE.read_text())
            return saved["date"], saved["samples"]
        except (json.JSONDecodeError, OSError, KeyError):
            pass
    return None, []


def get_stock_state(now):
    """Load/roll-over today's trace, fetch the latest quote, append a new
    sample if we're in the trading window on an actual trading day, and
    persist. Outside the trading window the price cannot have changed, so
    the fetch is skipped entirely and the cached state is returned as-is
    (same fallback shape as a failed fetch) rather than hitting the network
    every tick around the clock. On fetch failure, returns the state as
    loaded from cache (possibly with no price data at all on a fresh
    install with no network) -- callers must handle missing fields."""
    today_str = now.strftime("%Y-%m-%d")
    state = _load_trace(today_str)

    if not in_trading_window(now):
        return state

    quote = None
    try:
        quote = fetch_tsmc_quote()
    except Exception:
        logging.warning("TWSE stock quote fetch failed", exc_info=True)

    # TWSE succeeded but has no fresh trade this poll ("z" was "-"), or
    # failed outright -- try Yahoo before giving up. Use its quote wholesale
    # (price + prev_close + name together) rather than mixing fields from
    # two different sources on the same tick.
    if quote is None or quote["price"] is None:
        try:
            yahoo_quote = fetch_tsmc_quote_yahoo()
        except Exception:
            logging.warning("Yahoo fallback quote fetch failed", exc_info=True)
            yahoo_quote = None
        if yahoo_quote is not None and yahoo_quote["price"] is not None:
            quote = yahoo_quote

    if quote is None:
        logging.warning("Both TWSE and Yahoo fetches failed, using cached state")
        return state

    # prev_close/name are always trustworthy when a fetch succeeded;
    # last_price only updates when a fresh trade was actually reported,
    # otherwise the previous cached price (if any) is left alone rather
    # than being wiped out.
    state["prev_close"] = quote["prev_close"]
    state["name"] = quote["name"]
    if quote["price"] is not None:
        state["last_price"] = quote["price"]
        state["last_quote_date"] = quote["trade_date"]
        if quote["trade_date"] == today_str and in_trading_window(now):
            state["samples"].append([now.strftime("%H:%M"), quote["price"]])

    STATE_FILE.write_text(json.dumps(state))
    return state
