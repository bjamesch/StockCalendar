#!/usr/bin/env python3
"""Tiny phone-accessible web form for editing Erin's cash-saved and TSMC
share count -- the same numbers dashboard.py reads from erin_savings.json.
Runs as the erin-web-edit systemd service; open http://<pi-ip>:8080 from a
phone on the same wifi network.

Deliberately stdlib-only (http.server), matching dashboard.py's own
no-extra-dependencies style, and deliberately no login -- LAN-only tool.

Saving shells out to `dashboard.py --force` in the background rather than
importing dashboard.py directly, so this process never touches the e-paper
panel's GPIO/SPI pins itself -- dashboard.py's own lock file already
handles concurrent-run safety for that.
"""
import json
import os
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

PROJECT_DIR = Path(__file__).resolve().parent
SAVINGS_FILE = PROJECT_DIR / "erin_savings.json"
DASHBOARD_SCRIPT = PROJECT_DIR / "dashboard.py"
DEFAULT_CASH_NT = 22431
DEFAULT_TSMC_SHARES = 10
PORT = 8080

PAGE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Erin's Savings</title>
<style>
  body {{ font-family: sans-serif; max-width: 420px; margin: 40px auto; padding: 0 16px; }}
  h1 {{ font-size: 1.4em; }}
  label {{ display: block; margin-top: 20px; font-weight: bold; }}
  input {{ font-size: 1.3em; width: 100%; padding: 10px; margin-top: 6px;
           box-sizing: border-box; }}
  button {{ font-size: 1.2em; padding: 12px; margin-top: 28px; width: 100%; }}
  .msg {{ margin-top: 16px; padding: 10px; border-radius: 6px; }}
  .ok {{ background: #dff6dd; }}
  .err {{ background: #fbdada; }}
</style>
</head>
<body>
<h1>Erin's Savings</h1>
{message}
<form method="POST">
  <label for="cash_nt">Cash saved (NT$)</label>
  <input type="number" min="0" step="1" name="cash_nt" id="cash_nt" value="{cash_nt}" required>

  <label for="tsmc_shares">TSMC shares</label>
  <input type="number" min="0" step="1" name="tsmc_shares" id="tsmc_shares" value="{tsmc_shares}" required>

  <button type="submit">Save &amp; refresh display</button>
</form>
</body>
</html>
"""


def load_savings():
    if SAVINGS_FILE.exists():
        data = json.loads(SAVINGS_FILE.read_text())
        return data["cash_nt"], data["tsmc_shares"]
    return DEFAULT_CASH_NT, DEFAULT_TSMC_SHARES


def save_savings(cash_nt, tsmc_shares):
    # Write to a temp file and rename over the target so a power loss
    # mid-write (this is a battery-powered device) can't leave a half
    # -written, unparsable JSON file behind.
    tmp = SAVINGS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"cash_nt": cash_nt, "tsmc_shares": tsmc_shares}))
    os.replace(tmp, SAVINGS_FILE)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # keep stdout/journal quiet for routine requests

    def _send_page(self, message, cash_nt, tsmc_shares, status=200):
        body = PAGE.format(message=message, cash_nt=cash_nt, tsmc_shares=tsmc_shares).encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path != "/":
            self.send_response(404)
            self.end_headers()
            return
        cash_nt, tsmc_shares = load_savings()
        self._send_page("", cash_nt, tsmc_shares)

    def do_POST(self):
        if self.path != "/":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0))
        fields = parse_qs(self.rfile.read(length).decode())
        cash_nt, tsmc_shares = load_savings()  # fallback values if parsing fails
        try:
            new_cash_nt = int(fields["cash_nt"][0])
            new_tsmc_shares = int(fields["tsmc_shares"][0])
            if new_cash_nt < 0 or new_tsmc_shares < 0:
                raise ValueError("must not be negative")
        except (KeyError, ValueError, IndexError):
            self._send_page(
                '<div class="msg err">Please enter valid, non-negative whole numbers.</div>',
                cash_nt, tsmc_shares, status=400,
            )
            return

        save_savings(new_cash_nt, new_tsmc_shares)
        subprocess.Popen(["/usr/bin/python3", str(DASHBOARD_SCRIPT), "--force"],
                          cwd=str(PROJECT_DIR))
        self._send_page(
            '<div class="msg ok">Saved. Display refresh triggered '
            '(may take up to ~30s to show on the panel).</div>',
            new_cash_nt, new_tsmc_shares,
        )


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Serving on 0.0.0.0:{PORT}")
    server.serve_forever()
