#!/usr/bin/env python3
"""Renders the PhotoPainter dashboard: TSMC (2330) price/value/30-day trend
as the main focus, with a mini calendar and Hsinchu weather stacked in a
slim right sidebar. Time comes from the system clock, kept accurate offline
by the DS3231 hardware RTC (config.txt: dtoverlay=i2c-rtc,ds3231).

Stock and weather are network-dependent (unlike the RTC-backed clock), so
every fetch degrades gracefully to cached/placeholder data on failure - see
stock_source.py / stock_history.py / weather_source.py. Battery percentage
(top-right corner) comes from the PhotoPainter's onboard INA219 fuel gauge
over I2C - see battery_source.py; it's simply omitted if unreadable."""
import calendar
import fcntl
import logging
import math
import sys
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import epd7in3e
import battery_source
import stock_source
import stock_history
import weather_source

__version__ = "1.1.0"
__version_date__ = "2026-07-29"

FONT_DIR = Path("/usr/share/fonts/truetype/quicksand")
FONT_BOLD = FONT_DIR / "Quicksand-Bold.ttf"
FONT_REG = FONT_DIR / "Quicksand-Regular.ttf"

# epd.display() already does a full-panel waveform refresh on its own (this
# panel has no partial-refresh mode), so calling epd.Clear() before every
# update would double the flicker/wear for no benefit. We still want an
# occasional full white flash to reset pigment particles and avoid ghosting
# from the layout redrawing in near-identical positions every time, so
# Clear() only runs once per calendar day, tracked here.
LAST_CLEAR_FILE = Path(__file__).resolve().parent / ".last_clear_date"

# Which of the two Erin's-panel charts (30-day history vs. today's
# intraday) to show -- flips on every actual panel redraw, see
# toggle_chart_mode().
CHART_MODE_FILE = Path(__file__).resolve().parent / ".chart_mode"

# Guards against two runs (e.g. an overlapping cron tick, or a manual test
# run) fighting over the same GPIO/SPI pins, which crashes with a "GPIO busy"
# error rather than failing gracefully.
LOCK_FILE = Path(__file__).resolve().parent / ".dashboard.lock"

WEEKDAY_LABELS = ["M", "T", "W", "T", "F", "S", "S"]

# The panel is physically mounted 180 degrees from the orientation the driver
# assumes, so the fully-composed image is rotated before being pushed.
PANEL_ROTATION_DEGREES = 180

ERIN_CASH_NT = 3431
ERIN_TSMC_SHARES = 10

RED = (255, 0, 0)
BLUE = (0, 0, 255)
GREEN = (0, 255, 0)
BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
YELLOW = (255, 200, 0)

WEATHER_ICON_SIZE = 20

logging.basicConfig(level=logging.INFO)


def should_clear_today(now):
    today = now.strftime("%Y-%m-%d")
    last = LAST_CLEAR_FILE.read_text().strip() if LAST_CLEAR_FILE.exists() else None
    return last != today, today


def load_fonts():
    return {
        # Erin's Savings panel (main, large)
        "panel_title": ImageFont.truetype(str(FONT_BOLD), 28),
        "panel_subtitle": ImageFont.truetype(str(FONT_REG), 20),
        "value_hero": ImageFont.truetype(str(FONT_BOLD), 44),
        "status_line": ImageFont.truetype(str(FONT_BOLD), 24),
        "stock_note": ImageFont.truetype(str(FONT_REG), 20),
        "note": ImageFont.truetype(str(FONT_REG), 16),
        "chart_label": ImageFont.truetype(str(FONT_REG), 14),
        # Sidebar (mini calendar + weather, small)
        "mini_weekday": ImageFont.truetype(str(FONT_BOLD), 16),
        "mini_day": ImageFont.truetype(str(FONT_REG), 13),
        "mini_day_today": ImageFont.truetype(str(FONT_BOLD), 14),
        "sidebar_label": ImageFont.truetype(str(FONT_BOLD), 18),
        "sidebar_temp": ImageFont.truetype(str(FONT_BOLD), 18),
        "sidebar_small": ImageFont.truetype(str(FONT_REG), 15),
        "battery": ImageFont.truetype(str(FONT_REG), 13),
    }


def draw_calendar(draw, box, now, f_weekday, f_day, f_day_today):
    x0, y0, x1, y1 = box
    top_pad = 10  # breathing room between the card outline and the weekday row
    col_w = (x1 - x0) / 7
    weekday_row_h = 22
    row_top = y0 + top_pad + weekday_row_h
    grid_bottom = y1

    for i, label in enumerate(WEEKDAY_LABELS):
        x = x0 + i * col_w
        color = RED if i == 6 else (BLUE if i == 5 else BLACK)
        tw = draw.textlength(label, font=f_weekday)
        draw.text((x + col_w / 2 - tw / 2, y0 + top_pad), label, font=f_weekday, fill=color)

    cal = calendar.Calendar(firstweekday=0)
    weeks = cal.monthdayscalendar(now.year, now.month)
    row_h = (grid_bottom - row_top) / len(weeks)

    for r, week in enumerate(weeks):
        y = row_top + r * row_h
        for c, day in enumerate(week):
            if day == 0:
                continue
            x = x0 + c * col_w
            is_today = day == now.day
            if is_today:
                draw.rounded_rectangle((x + 1, y + 1, x + col_w - 1, y + row_h - 1), radius=6, fill=GREEN)
                fill, font = (255, 255, 255), f_day_today
            else:
                fill = RED if c == 6 else (BLUE if c == 5 else BLACK)
                font = f_day
            text = str(day)
            tw = draw.textlength(text, font=font)
            draw.text((x + col_w / 2 - tw / 2, y + row_h / 2 - font.size / 2),
                      text, font=font, fill=fill)


def _draw_cloud(draw, x, y, size, fill=WHITE):
    # Two overlapping circles read as a simple cloud silhouette at this size.
    draw.ellipse((x, y + size * 0.3, x + size * 0.75, y + size), fill=fill, outline=BLACK)
    draw.ellipse((x + size * 0.3, y, x + size, y + size * 0.7), fill=fill, outline=BLACK)


def _draw_sun(draw, x, y, size, fill=YELLOW):
    cx, cy, r = x + size / 2, y + size / 2, size * 0.28
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=fill)
    for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0), (0.7, -0.7), (0.7, 0.7), (-0.7, -0.7), (-0.7, 0.7)):
        draw.line((cx + dx * r * 1.3, cy + dy * r * 1.3, cx + dx * r * 1.9, cy + dy * r * 1.9),
                  fill=fill, width=2)


def draw_weather_icon(draw, x, y, size, category):
    """Draws a small icon representing a day's overall weather condition."""
    if category == "sun":
        _draw_sun(draw, x, y, size)
    elif category == "partly":
        _draw_sun(draw, x, y, size * 0.7)
        _draw_cloud(draw, x + size * 0.25, y + size * 0.3, size * 0.75)
    elif category == "cloud":
        _draw_cloud(draw, x, y, size)
    elif category == "rain":
        _draw_cloud(draw, x, y, size * 0.8)
        r = size * 0.09
        for dx in (0.15, 0.48, 0.81):
            cx, cy = x + size * dx, y + size * 0.88
            draw.ellipse((cx - r, cy - r * 1.6, cx + r, cy + r * 1.6), fill=BLUE)
    elif category == "storm":
        _draw_cloud(draw, x, y, size * 0.8)
        cx = x + size * 0.5
        draw.polygon([(cx + size * 0.12, y + size * 0.55), (cx - size * 0.18, y + size * 0.85),
                      (cx + size * 0.02, y + size * 0.85), (cx - size * 0.2, y + size * 1.15),
                      (cx + size * 0.28, y + size * 0.75), (cx + size * 0.06, y + size * 0.75)],
                     fill=YELLOW)
    elif category == "snow":
        _draw_cloud(draw, x, y, size * 0.8)
        r = size * 0.11
        for dx in (0.15, 0.48, 0.81):
            cx, cy = x + size * dx, y + size * 0.88
            draw.polygon([(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)], fill=BLUE)


def draw_weather_panel(draw, box, weather, f_label, f_temp, f_small):
    x0, y0, x1, y1 = box
    if not weather:
        draw.text((x0, y0), "Weather unavailable", font=f_small, fill=BLACK)
        return

    # Fixed-width columns instead of one space-separated string -- gives
    # deliberate breathing room between weekday/temps/rain%, and keeps
    # everything aligned regardless of how wide each value renders. Column
    # widths are chosen so the table spans nearly the full card width,
    # leaving only a calendar-card-sized ~6-10px margin once centered,
    # rather than a large leftover gap on one side.
    weekday_w = WEATHER_ICON_SIZE + 8 + 50
    hilo_w = 72
    rain_w = draw.textlength("100%", font=f_small)
    content_w = weekday_w + hilo_w + rain_w
    x0 = x0 + max(0, int((x1 - x0 - content_w) // 2))

    # Same idea vertically: tighten the line spacing slightly and center
    # the resulting block in the box, so there's even top/bottom padding
    # instead of the content running flush to (or past) the card edges.
    row_h = 24
    header_h = 26
    content_h = header_h * 2 + row_h * len(weather["days"])
    y = y0 + max(0, int((y1 - y0 - content_h) // 2))

    draw.text((x0, y), "Hsinchu", font=f_label, fill=BLACK)
    y += header_h

    cur_label = weather_source.weather_label(weather["current_code"])
    draw.text((x0, y), f'{weather["current_temp"]:.0f}°C {cur_label}', font=f_temp, fill=BLACK)
    y += header_h

    weekday_col_x = x0 + WEATHER_ICON_SIZE + 8
    hilo_col_x = weekday_col_x + 50
    rain_col_x = hilo_col_x + 72

    for day in weather["days"]:
        # Same weekend color convention as the mini calendar: Sat blue, Sun red.
        color = BLUE if day["weekday"] == "Sat" else (RED if day["weekday"] == "Sun" else BLACK)
        draw_weather_icon(draw, x0, y - 1, WEATHER_ICON_SIZE, weather_source.icon_category(day["code"]))
        draw.text((weekday_col_x, y), day["weekday"], font=f_small, fill=color)
        draw.text((hilo_col_x, y), f'{day["hi"]:.0f}/{day["lo"]:.0f}', font=f_small, fill=color)
        draw.text((rain_col_x, y), f'{day["rain_pct"]:.0f}%', font=f_small, fill=color)
        y += row_h


def _draw_star(draw, cx, cy, r, fill=YELLOW):
    """Small 5-point star, same hand-drawn-icon technique as the weather
    icons / mood-cat -- no external assets."""
    points = []
    for i in range(10):
        angle = -math.pi / 2 + i * math.pi / 5
        radius = r if i % 2 == 0 else r * 0.45
        points.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    draw.polygon(points, fill=fill, outline=BLACK)


def _draw_value_line(draw, pts, values, plot_left, plot_right, plot_bottom):
    """Shared plotting core for both charts: a stippled area fill under the
    curve, per-segment red/green coloring (Taiwan up/down convention), plain
    dots for earlier points, and a highlighted outlined dot + conditional
    new-high star for the most recent point. Shared by draw_history_chart()
    and draw_intraday_chart() since this logic is identical between them --
    only axis/label/data-source specifics differ per caller. Requires at
    least 2 points; callers are responsible for their own "not enough data
    yet" fallback."""
    hi = max(values)

    def curve_y_at(x):
        # Linear interpolation of the curve's y at a given x -- used to
        # trace the stippled area fill below the line.
        for (x1_, y1_), (x2_, y2_) in zip(pts, pts[1:]):
            if x1_ <= x <= x2_:
                if x2_ == x1_:
                    return y1_
                t = (x - x1_) / (x2_ - x1_)
                return y1_ + t * (y2_ - y1_)
        return pts[-1][1]

    # Stippled area fill under the curve, drawn first so the gridlines and
    # line/dots render crisply on top of it. No true pastel/alpha fill is
    # possible on this 6-color panel, so this is a light dot-texture "hill"
    # rather than a solid wash, matching the dotted-gridline style already
    # used elsewhere in this chart.
    fill_step_x, fill_step_y = 6, 7
    gx = plot_left
    while gx < plot_right:
        gy = curve_y_at(gx) + fill_step_y
        while gy < plot_bottom:
            draw.point((gx, gy), fill=BLUE)
            gy += fill_step_y
        gx += fill_step_x

    # Line: colored per segment, red = up / green = down (same Taiwan
    # convention as the status text and mood-cat elsewhere on the panel).
    # Trade-off: splitting into per-segment lines loses the smooth
    # joint="curve" blending a single multi-point polyline gets, but
    # segments are short and only 3px wide, so joints still read cleanly.
    for i in range(len(pts) - 1):
        seg_color = RED if values[i + 1] > values[i] else (GREEN if values[i + 1] < values[i] else BLACK)
        draw.line([pts[i], pts[i + 1]], fill=seg_color, width=3)

    dot_r = 4
    for (px_, py_) in pts[:-1]:
        draw.ellipse((px_ - dot_r, py_ - dot_r, px_ + dot_r, py_ + dot_r), fill=BLUE)

    # Most recent point gets a bigger, outlined highlight dot so it reads
    # as "you are here," plus a small star above it if it's a new high for
    # the shown window (only the latest point, not every historical high,
    # to avoid cluttering the chart with stars).
    latest_x, latest_y = pts[-1]
    latest_color = RED if values[-1] > values[-2] else (GREEN if values[-1] < values[-2] else BLACK)
    latest_r = 7
    draw.ellipse((latest_x - latest_r, latest_y - latest_r, latest_x + latest_r, latest_y + latest_r),
                 fill=latest_color, outline=BLACK, width=2)
    if values[-1] == hi:
        _draw_star(draw, latest_x, latest_y - latest_r - 10, 8)


def draw_history_chart(draw, box, rows, share_count, f_label, base_cash=0):
    x0_outer, y0, x1, y1 = box
    if len(rows) < 2:
        draw.text((x0_outer, y0 + (y1 - y0) / 2 - 8), "History unavailable", font=f_label, fill=BLACK)
        return

    values = [base_cash + r["close"] * share_count for r in rows]
    lo, hi = min(values), max(values)
    if hi == lo:
        hi = lo + 1
    row_h = 15

    n_y_ticks = 5
    y_labels = [f"NT${hi - k * (hi - lo) / (n_y_ticks - 1):,.0f}" for k in range(n_y_ticks)]
    y_label_w = max(draw.textlength(t, font=f_label) for t in y_labels) + 6

    plot_left = x0_outer + y_label_w
    plot_right = x1
    # Top: half a row of headroom so the topmost value label isn't clipped.
    plot_top = y0 + row_h // 2
    # Bottom: one row for date tick labels.
    plot_bottom = y1 - row_h

    def py(v):
        return plot_bottom - (v - lo) / (hi - lo) * (plot_bottom - plot_top)

    def px(i):
        return plot_left + i * (plot_right - plot_left) / (len(rows) - 1)

    pts = [(px(i), py(v)) for i, v in enumerate(values)]

    # Y-axis: a labeled, dotted gridline at each of the 5 tick values.
    for k in range(n_y_ticks):
        gy = plot_top + k * (plot_bottom - plot_top) / (n_y_ticks - 1)
        gx = plot_left
        while gx < plot_right:
            draw.line((gx, gy, min(gx + 3, plot_right), gy), fill=BLACK, width=1)
            gx += 9
        label = y_labels[k]
        draw.text((x0_outer, gy - f_label.size / 2), label, font=f_label, fill=BLACK)

    # X-axis: a labeled, dotted gridline at up to 5 evenly-spaced dates.
    n_x_ticks = min(5, len(rows))
    tick_indices = sorted({round(i * (len(rows) - 1) / (n_x_ticks - 1)) for i in range(n_x_ticks)})
    tick_y = plot_bottom + row_h
    for i in tick_indices:
        gx = px(i)
        gy = plot_top
        while gy < plot_bottom:
            draw.line((gx, gy, gx, min(gy + 3, plot_bottom)), fill=BLACK, width=1)
            gy += 9
        date_label = rows[i]["date"][5:]
        dl_w = draw.textlength(date_label, font=f_label)
        draw.text((min(max(gx - dl_w / 2, plot_left), plot_right - dl_w), tick_y),
                  date_label, font=f_label, fill=BLACK)

    _draw_value_line(draw, pts, values, plot_left, plot_right, plot_bottom)


TRADING_START_MIN = 9 * 60
TRADING_END_MIN = 13 * 60 + 30


def draw_intraday_chart(draw, box, date_str, samples, today_str, f_label):
    """Today's intraday movement (or the last trading day with data, if
    today has none yet -- see stock_source.load_intraday_chart_data()),
    fixed to the 09:00-13:30 trading window rather than auto-scaled to
    whatever's been collected so far, so the line visibly grows rightward
    across the session as ticks accumulate. Y-axis is the raw TSMC share
    price (not the Erin's-savings equity value the history chart uses) --
    this chart is about the stock's own movement during the day."""
    x0_outer, y0, x1, y1 = box
    if len(samples) < 2:
        draw.text((x0_outer, y0 + (y1 - y0) / 2 - 8), "Not enough trading data yet",
                  font=f_label, fill=BLACK)
        return

    values = [price for _, price in samples]
    lo, hi = min(values), max(values)
    if hi == lo:
        hi = lo + 1
    row_h = 15

    n_y_ticks = 5
    y_labels = [f"NT${hi - k * (hi - lo) / (n_y_ticks - 1):,.0f}" for k in range(n_y_ticks)]
    y_label_w = max(draw.textlength(t, font=f_label) for t in y_labels) + 6

    plot_left = x0_outer + y_label_w
    plot_right = x1
    plot_top = y0 + row_h // 2
    plot_bottom = y1 - row_h

    def minutes_of(hhmm):
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m)

    def px(hhmm):
        frac = (minutes_of(hhmm) - TRADING_START_MIN) / (TRADING_END_MIN - TRADING_START_MIN)
        return plot_left + frac * (plot_right - plot_left)

    def py(v):
        return plot_bottom - (v - lo) / (hi - lo) * (plot_bottom - plot_top)

    pts = [(px(t), py(v)) for (t, _), v in zip(samples, values)]

    # Y-axis: same style as the history chart.
    for k in range(n_y_ticks):
        gy = plot_top + k * (plot_bottom - plot_top) / (n_y_ticks - 1)
        gx = plot_left
        while gx < plot_right:
            draw.line((gx, gy, min(gx + 3, plot_right), gy), fill=BLACK, width=1)
            gx += 9
        draw.text((x0_outer, gy - f_label.size / 2), y_labels[k], font=f_label, fill=BLACK)

    # X-axis: fixed hourly ticks across the trading window regardless of
    # how much data has arrived yet -- unlike the history chart, these
    # aren't derived from the data.
    tick_y = plot_bottom + row_h
    for t in ("09:00", "10:00", "11:00", "12:00", "13:00"):
        gx = px(t)
        gy = plot_top
        while gy < plot_bottom:
            draw.line((gx, gy, gx, min(gy + 3, plot_bottom)), fill=BLACK, width=1)
            gy += 9
        dl_w = draw.textlength(t, font=f_label)
        draw.text((min(max(gx - dl_w / 2, plot_left), plot_right - dl_w), tick_y),
                  t, font=f_label, fill=BLACK)

    _draw_value_line(draw, pts, values, plot_left, plot_right, plot_bottom)

    # If this isn't today's data (fell back to the last trading day with
    # samples), label which day it actually is so it doesn't read as live.
    # Placed top-left, not top-right: the highlighted latest-point dot (and
    # its conditional star) is always at the plot's right edge by
    # construction, so top-left is the one corner guaranteed not to collide
    # with it regardless of where that point falls vertically.
    if date_str != today_str:
        label = f"({date_str[5:]})"
        draw.text((plot_left + 4, plot_top), label, font=f_label, fill=BLACK)


def draw_mood_cat(draw, x, y, size, mood):
    """Draws a small cat face reacting to the stock's mood ("happy",
    "sad", or "neutral"), same hand-drawn-icon style as draw_weather_icon."""
    cx, cy = x + size / 2, y + size / 2
    r = size * 0.38

    # Ears: solid triangles peeking above the head, for a bit of graphic
    # punch/recognizability at small size (matches how the weather icons
    # use solid accents against otherwise outline-only shapes).
    ear_w = size * 0.22
    draw.polygon([(cx - r * 0.9, cy - r * 0.5), (cx - r * 0.55, cy - r * 1.1), (cx - r * 0.2, cy - r * 0.6)],
                 fill=BLACK)
    draw.polygon([(cx + r * 0.9, cy - r * 0.5), (cx + r * 0.55, cy - r * 1.1), (cx + r * 0.2, cy - r * 0.6)],
                 fill=BLACK)

    # Head.
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=WHITE, outline=BLACK, width=2)

    # Whiskers.
    for side in (-1, 1):
        for dy in (-0.05, 0.1):
            draw.line((cx + side * r * 0.7, cy + r * dy, cx + side * r * 1.3, cy + r * (dy - 0.05)),
                      fill=BLACK, width=2)

    # Eyes.
    eye_dx, eye_dy = r * 0.4, r * 0.1
    eye_r = max(1.5, size * 0.045)
    for dx in (-eye_dx, eye_dx):
        draw.ellipse((cx + dx - eye_r, cy - eye_dy - eye_r, cx + dx + eye_r, cy - eye_dy + eye_r), fill=BLACK)

    # Mouth: mood-dependent shallow curve.
    mouth_w, mouth_depth = r * 0.55, r * 0.3
    mouth_y = cy + r * 0.35
    box = (cx - mouth_w, mouth_y - mouth_depth, cx + mouth_w, mouth_y + mouth_depth)
    if mood == "happy":
        draw.arc(box, start=0, end=180, fill=BLACK, width=2)
    elif mood == "sad":
        draw.arc(box, start=180, end=360, fill=BLACK, width=2)
    else:
        draw.line((cx - mouth_w, mouth_y, cx + mouth_w, mouth_y), fill=BLACK, width=2)


def draw_card(draw, box, padding=6, radius=10, pad_top=None):
    """Soft rounded-rect outline around a content box, for a gentler 'card'
    feel than a stark divider line."""
    x0, y0, x1, y1 = box
    pt = padding if pad_top is None else pad_top
    draw.rounded_rectangle((x0 - padding, y0 - pt, x1 + padding, y1 + padding),
                            radius=radius, outline=BLACK, width=1)


def draw_erin_panel(draw, box, stock, history_rows, now, fonts, chart_mode="history"):
    x0, y0, x1, y1 = box
    y = y0

    draw.text((x0, y), "Erin's Savings", font=fonts["panel_title"], fill=BLACK)
    y += 40

    draw.text((x0, y), f"NT${ERIN_CASH_NT:,} saved + {ERIN_TSMC_SHARES} TSMC shares",
              font=fonts["panel_subtitle"], fill=BLACK)
    y += 34

    # Prefer the live feed's prev_close (guaranteed consistent with `price`,
    # both from the same fetch) but fall back to the cached daily-history's
    # most recent close, which stays available even before today's live
    # price has arrived (e.g. pre-market).
    last_close = history_rows[-1]["close"] if history_rows else None
    prev_close = (stock.get("prev_close") if stock else None) or last_close

    price = stock.get("last_price") if stock else None
    today_str = now.strftime("%Y-%m-%d")
    is_trading_day = bool(stock) and stock.get("last_quote_date") == today_str
    # Only call it "now at" while the market is genuinely still open for a
    # trading day we have a fresh price for -- past 13:30 (or on a non-
    # trading day) that same price IS the close, so it's labeled that way.
    is_live = price is not None and is_trading_day and stock_source.in_trading_window(now)

    if is_live:
        draw.text((x0, y), f"TSMC now at NT${price:,.0f}", font=fonts["stock_note"], fill=BLACK)
        y += 32
    else:
        closed_price = price if (price is not None and is_trading_day) else prev_close
        if closed_price is not None:
            draw.text((x0, y), f"TSMC closed at NT${closed_price:,.0f}", font=fonts["stock_note"], fill=BLACK)
            y += 32

    if price is None:
        # No live price yet (e.g. before market open, or a fetch outage) --
        # the chart below only needs history_rows, not today's price, so it
        # still renders; only the "today" section is skipped. On a weekend
        # "today" will never get a price, so name the next trading day
        # instead of saying "today".
        day_label = "today" if now.weekday() < 5 else stock_source.next_trading_day_name(now)
        draw.text((x0, y), f"Waiting for {day_label}'s price...", font=fonts["stock_note"], fill=BLACK)
        y += 34
    else:
        equity = ERIN_CASH_NT + price * ERIN_TSMC_SHARES
        draw.text((x0, y), f"NT${equity:,.0f}", font=fonts["value_hero"], fill=BLACK)
        y += 58

        if prev_close:
            equity_change = (price - prev_close) * ERIN_TSMC_SHARES
            # Taiwan convention: red = up, green = down (opposite of US markets)
            if equity_change > 0:
                status, color, mood = f"Went up NT${equity_change:,.0f} since close", RED, "happy"
            elif equity_change < 0:
                status, color, mood = f"Went down NT${abs(equity_change):,.0f} since close", GREEN, "sad"
            else:
                status, color, mood = "Same as yesterday!", BLACK, "neutral"
            draw.text((x0, y), status, font=fonts["status_line"], fill=color)
            status_w = draw.textlength(status, font=fonts["status_line"])
            draw_mood_cat(draw, x0 + status_w + 10, y - 4, 30, mood)
            y += 36

        if not is_trading_day:
            draw.text((x0, y), "The stock market is closed today.", font=fonts["note"], fill=BLACK)
            y += 26

    # Consistent breathing room between the text block above and the chart,
    # regardless of which lines were shown.
    y += 16

    chart_box = (x0, y, x1, y1)
    if chart_mode == "intraday":
        intraday_date, intraday_samples = stock_source.load_intraday_chart_data(stock or {})
        draw_intraday_chart(draw, chart_box, intraday_date, intraday_samples, today_str,
                            fonts["chart_label"])
    else:
        draw_history_chart(draw, chart_box, history_rows, ERIN_TSMC_SHARES, fonts["chart_label"],
                            base_cash=ERIN_CASH_NT)


def build_image(width, height, now, stock, history_rows, weather, battery_percent, chart_mode="history"):
    img = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    fonts = load_fonts()
    margin = 20

    content_top = 20

    if battery_percent is not None:
        text = f"Batt {battery_percent:.0f}%"
        tw = draw.textlength(text, font=fonts["battery"])
        draw.text((width - margin - tw, 4), text, font=fonts["battery"], fill=BLACK)

    erin_box = (margin, content_top, 566, height - margin)
    draw_erin_panel(draw, erin_box, stock, history_rows, now, fonts, chart_mode)

    sidebar_x0, sidebar_x1 = 588, width - margin
    calendar_box = (sidebar_x0, content_top, sidebar_x1, 208)
    # Less padding on top specifically -- that's where the battery % text
    # sits, and a full-padding card border would cut through it.
    draw_card(draw, calendar_box, pad_top=0)
    draw_calendar(draw, calendar_box, now, fonts["mini_weekday"], fonts["mini_day"], fonts["mini_day_today"])

    weather_box = (sidebar_x0, 226, sidebar_x1, height - margin)
    draw_card(draw, weather_box)
    draw_weather_panel(draw, weather_box, weather, fonts["sidebar_label"],
                        fonts["sidebar_temp"], fonts["sidebar_small"])

    if PANEL_ROTATION_DEGREES:
        img = img.rotate(PANEL_ROTATION_DEGREES)

    return img


def should_redraw(now, is_new_day):
    return is_new_day or stock_source.in_trading_window(now) or now.minute in (0, 30)


def toggle_chart_mode():
    """Flips which of the two Erin's-panel charts to show. Only called when
    a real panel redraw is about to happen (see main()) -- data-fetch-only
    ticks that skip the physical redraw don't advance the toggle."""
    current = CHART_MODE_FILE.read_text().strip() if CHART_MODE_FILE.exists() else "history"
    new_mode = "intraday" if current == "history" else "history"
    CHART_MODE_FILE.write_text(new_mode)
    return new_mode


def main():
    lock_fh = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        logging.info("Another dashboard run is already in progress; skipping this tick")
        return

    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    is_new_day, _ = should_clear_today(now)

    weather = weather_source.load_weather(today_str)
    stock = stock_source.get_stock_state(now)
    history_rows = stock_history.load_history(today_str, now)
    battery_percent = battery_source.get_battery_percent()

    if not should_redraw(now, is_new_day):
        logging.info("Data updated; skipping panel redraw this tick")
        return

    chart_mode = toggle_chart_mode()
    logging.info("Rendering dashboard for %s (chart_mode=%s)", now.isoformat(), chart_mode)
    epd = epd7in3e.EPD()
    img = build_image(epd.width, epd.height, now, stock, history_rows, weather, battery_percent, chart_mode)

    epd.init()
    if is_new_day:
        logging.info("Daily Clear() (first redraw of %s)", today_str)
        epd.Clear()
        LAST_CLEAR_FILE.write_text(today_str)
    else:
        logging.info("Skipping Clear(); already cleared today")
    epd.display(epd.getbuffer(img))
    logging.info("sleeping display")
    epd.sleep()


if __name__ == "__main__":
    main()
