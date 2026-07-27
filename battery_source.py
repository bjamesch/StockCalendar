"""PhotoPainter battery level via its onboard INA219 fuel gauge (I2C addr
0x43, bus 1). Unlike weather/stock, this is a cheap local I2C read with no
network involved, so it's read fresh every tick rather than cached."""
import logging

from lib.INA219 import INA219

I2C_ADDR = 0x43

# INA219 measures bus (load-side) voltage; percent is derived assuming a
# single Li-ion/LiPo cell where ~3.0V is empty and ~4.2V is full (the same
# formula Waveshare's own PhotoPainter battery demo uses).
EMPTY_V = 3.0
FULL_V = 4.2


def get_battery_percent():
    """Returns battery percentage (0-100), or None if the fuel gauge can't
    be read (e.g. running off a Pi without the PhotoPainter battery board)."""
    try:
        ina = INA219(addr=I2C_ADDR)
        voltage = ina.getBusVoltage_V()
    except Exception:
        logging.warning("Battery read failed", exc_info=True)
        return None

    percent = (voltage - EMPTY_V) / (FULL_V - EMPTY_V) * 100
    return max(0.0, min(100.0, percent))
