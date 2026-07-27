#!/usr/bin/env python3
import logging
logging.basicConfig(level=logging.INFO)

from lib import epd7in3e
from PIL import Image, ImageDraw, ImageFont

epd = epd7in3e.EPD()
logging.info("init and Clear")
epd.init()
epd.Clear()

Himage = Image.new('RGB', (epd.width, epd.height), epd.WHITE)
draw = ImageDraw.Draw(Himage)
font = ImageFont.load_default()
draw.text((20, 20), 'PhotoPainter display test', font=font, fill=epd.BLACK)
draw.rectangle((20, 60, 220, 160), outline=epd.RED, width=4)
draw.rectangle((240, 60, 440, 160), outline=epd.GREEN, width=4)
draw.rectangle((460, 60, 660, 160), outline=epd.BLUE, width=4)
draw.text((20, 180), 'If you can read this and see colored boxes, the panel works.', font=font, fill=epd.BLACK)

epd.display(epd.getbuffer(Himage))
logging.info("sleeping display")
epd.sleep()
