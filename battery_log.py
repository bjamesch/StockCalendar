"""Appends one INA219 battery reading to battery_log.csv, for tracking real
unattended battery draw over time (see BATTERY_OPTIMIZATION.md's "Current
draw" section). Run periodically via cron, separate from dashboard.py's own
per-tick battery read (battery_source.py), so the log's cadence doesn't
depend on the dashboard's variable refresh schedule."""
import csv
import logging
from datetime import datetime
from pathlib import Path

from lib.INA219 import INA219
from battery_source import EMPTY_V, FULL_V, I2C_ADDR

LOG_FILE = Path(__file__).resolve().parent / "battery_log.csv"
FIELDS = ["timestamp", "voltage_v", "current_ma", "power_w", "percent"]


def main():
    try:
        ina = INA219(addr=I2C_ADDR)
        voltage = ina.getBusVoltage_V()
        current_ma = ina.getCurrent_mA()
        power_w = ina.getPower_W()
    except Exception:
        logging.warning("Battery read failed", exc_info=True)
        return

    percent = max(0.0, min(100.0, (voltage - EMPTY_V) / (FULL_V - EMPTY_V) * 100))

    is_new_file = not LOG_FILE.exists()
    with LOG_FILE.open("a", newline="") as f:
        writer = csv.writer(f)
        if is_new_file:
            writer.writerow(FIELDS)
        writer.writerow([
            datetime.now().isoformat(timespec="seconds"),
            round(voltage, 3),
            round(current_ma, 1),
            round(power_w, 3),
            round(percent, 1),
        ])


if __name__ == "__main__":
    main()
