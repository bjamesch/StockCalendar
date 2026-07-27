"""Hsinchu weather via Taiwan's Central Weather Administration (CWA) Open
Data API. Requires a free CWA API key (opendata.cwa.gov.tw) saved in
cwa_api_key.txt next to this file. Cached once per day so the 10-minute
dashboard refresh doesn't hit the API every tick."""
import json
import logging
import ssl
import urllib.parse
import urllib.request
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

STATE_FILE = Path(__file__).resolve().parent / "weather_today.json"
API_KEY_FILE = Path(__file__).resolve().parent / "cwa_api_key.txt"

# CWA's own cert chain is missing a Subject Key Identifier extension on an
# intermediate, which OpenSSL's default X509_STRICT mode rejects (curl
# doesn't enable that mode, which is why it works there but not here). This
# only relaxes that one RFC5280-strictness check - the chain is still
# verified against the trusted CA store.
_SSL_CONTEXT = ssl.create_default_context()
_SSL_CONTEXT.verify_flags &= ~ssl.VERIFY_X509_STRICT

# F-D0047-055: "臺灣各縣市鄉鎮未來1週逐12小時天氣預報" (next-1-week, 12-hourly,
# per-township forecast) filtered to Hsinchu City's East District (its
# central, most representative township).
DATASET_ID = "F-D0047-055"
LOCATION_NAME = "東區"

FORECAST_DAYS = 7

BASE_URL = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/{DATASET_ID}"

# CWA's official weather-phenomenon codes (1-42) collapsed to one short
# English label per code - see opendata.cwa.gov.tw's "預報產品天氣描述代碼表"
# for the full code -> text mapping (each code covers several Chinese
# descriptions; this picks one representative label per code).
CWA_LABELS = {
    1: "Clear", 2: "Mostly clear", 3: "Partly clear", 4: "Partly cloudy",
    5: "Mostly cloudy", 6: "Mostly cloudy", 7: "Cloudy",
    8: "Partly cloudy, showers", 9: "Mostly cloudy, rain", 10: "Mostly cloudy, rain",
    11: "Rainy", 12: "Mostly cloudy, rain", 13: "Mostly cloudy, rain", 14: "Rainy",
    15: "Partly cloudy, thundershowers", 16: "Partly cloudy, thundershowers",
    17: "Mostly cloudy, thundershowers", 18: "Cloudy, thundershowers",
    19: "Clear, afternoon showers", 20: "Partly cloudy, afternoon showers",
    21: "Clear, afternoon thundershowers", 22: "Partly cloudy, afternoon thundershowers",
    23: "Rain or snow", 24: "Clear, fog", 25: "Mostly clear, fog",
    26: "Partly clear, fog", 27: "Partly cloudy, fog", 28: "Cloudy, fog",
    29: "Partly cloudy, local rain", 30: "Mostly cloudy, local rain",
    31: "Partly cloudy, fog and rain", 32: "Mostly cloudy, fog and rain",
    33: "Partly cloudy, local thundershowers", 34: "Partly cloudy, local thundershowers",
    35: "Partly cloudy, thundershowers and fog", 36: "Mostly cloudy, thundershowers and fog",
    37: "Cloudy, local rain/snow and fog", 38: "Occasional rain, fog",
    39: "Rain, fog", 41: "Showers or thunderstorms, fog", 42: "Snow",
}


def weather_label(code):
    return CWA_LABELS.get(code, "Unknown")


# Collapses the 42 CWA codes into a handful of buckets for the dashboard's
# small per-day condition icon (sun / partly-cloudy / cloudy / rain / storm
# / snow). Any code not listed below is a rain variant (8-14, 19, 20, 29-32,
# 38, 39) - the drawn shape doesn't need to distinguish them further.
_ICON_CATEGORIES = {
    "sun": {1},
    "partly": {2, 3, 4},
    "cloud": {5, 6, 7, 24, 25, 26, 27, 28},
    "storm": {15, 16, 17, 18, 21, 22, 33, 34, 35, 36, 41},
    "snow": {23, 37, 42},
}


def icon_category(code):
    for category, codes in _ICON_CATEGORIES.items():
        if code in codes:
            return category
    return "rain"


def _api_key():
    return API_KEY_FILE.read_text().strip()


def _element_periods(elements, name):
    return next(e for e in elements if e["ElementName"] == name)["Time"]


def _parse_pop(raw):
    # CWA only forecasts precipitation probability roughly 3-4 days out;
    # later periods report "-" instead of a number.
    return None if raw == "-" else float(raw)


def fetch_hsinchu_weather():
    url = BASE_URL + "?" + urllib.parse.urlencode({
        "Authorization": _api_key(),
        "locationName": LOCATION_NAME,
    })
    req = urllib.request.Request(url, headers={"User-Agent": "photopainter-dashboard/1.0"})
    with urllib.request.urlopen(req, timeout=15, context=_SSL_CONTEXT) as resp:
        data = json.loads(resp.read())

    elements = data["records"]["Locations"][0]["Location"][0]["WeatherElement"]
    temp_periods = _element_periods(elements, "平均溫度")
    hi_periods = _element_periods(elements, "最高溫度")
    lo_periods = _element_periods(elements, "最低溫度")
    pop_periods = _element_periods(elements, "12小時降雨機率")
    wx_periods = _element_periods(elements, "天氣現象")

    current_temp = float(temp_periods[0]["ElementValue"][0]["Temperature"])
    current_code = int(wx_periods[0]["ElementValue"][0]["WeatherCode"])

    days = OrderedDict()
    for hi_p, lo_p, pop_p, wx_p in zip(hi_periods, lo_periods, pop_periods, wx_periods):
        date_str = hi_p["StartTime"][:10]
        is_daytime = hi_p["StartTime"][11:13] == "06"
        hi = float(hi_p["ElementValue"][0]["MaxTemperature"])
        lo = float(lo_p["ElementValue"][0]["MinTemperature"])
        pop = _parse_pop(pop_p["ElementValue"][0]["ProbabilityOfPrecipitation"])
        code = int(wx_p["ElementValue"][0]["WeatherCode"])

        if date_str not in days:
            days[date_str] = {"hi": hi, "lo": lo, "rain_pct": pop, "code": code}
        else:
            day = days[date_str]
            day["hi"] = max(day["hi"], hi)
            day["lo"] = min(day["lo"], lo)
            if pop is not None:
                day["rain_pct"] = pop if day["rain_pct"] is None else max(day["rain_pct"], pop)
            if is_daytime:
                day["code"] = code  # prefer the daytime description over overnight

    result_days = []
    for date_str, vals in list(days.items())[:FORECAST_DAYS]:
        weekday = datetime.strptime(date_str, "%Y-%m-%d").strftime("%a")
        vals["rain_pct"] = vals["rain_pct"] if vals["rain_pct"] is not None else 0.0
        result_days.append({"date": date_str, "weekday": weekday, **vals})

    return {
        "current_temp": current_temp,
        "current_code": current_code,
        "days": result_days,
    }


def load_weather(today_str):
    """Return today's weather, using the daily cache when possible.

    Fetches fresh data once per calendar day. On fetch failure, falls back
    to a stale cache if one exists, or None if there's nothing cached yet —
    callers must handle None (show an "unavailable" placeholder, never crash).
    """
    cached = None
    if STATE_FILE.exists():
        try:
            cached = json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            cached = None

    if cached and cached.get("date") == today_str:
        return cached["weather"]

    try:
        weather = fetch_hsinchu_weather()
    except Exception:
        logging.warning("Weather fetch failed, falling back to cache", exc_info=True)
        return cached["weather"] if cached else None

    STATE_FILE.write_text(json.dumps({"date": today_str, "weather": weather}))
    return weather
