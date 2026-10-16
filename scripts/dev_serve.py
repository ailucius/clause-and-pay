"""Run the app locally: python scripts/dev_serve.py [--key-file .env] [--port 8000]

--key-file loads ONLY the GEMINI_API_KEY / GOOGLE_API_KEY line (and, when present,
PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET, or their PAYPAL_SANDBOX_* names) from a
dotenv file into this process.
"""
import argparse, os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
WANTED = {"GEMINI_API_KEY": "GEMINI_API_KEY", "GOOGLE_API_KEY": "GEMINI_API_KEY",
          "PAYPAL_CLIENT_ID": "PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET": "PAYPAL_CLIENT_SECRET",
          # the names a local secrets file may use for the sandbox app
          "PAYPAL_SANDBOX_CLIENT_ID": "PAYPAL_CLIENT_ID", "PAYPAL_SANDBOX_SECRET": "PAYPAL_CLIENT_SECRET"}

ap = argparse.ArgumentParser()
ap.add_argument("--key-file", action="append", default=[])
ap.add_argument("--port", type=int, default=8000)
a = ap.parse_args()
for f in a.key_file:
    for line in Path(f).read_text().splitlines():
        name, sep, value = line.strip().removeprefix("export ").partition("=")
        name = name.strip()
        if sep and name in WANTED and value.strip() and WANTED[name] not in os.environ:
            os.environ[WANTED[name]] = value.strip().strip('"').strip("'")

import uvicorn  # noqa: E402
uvicorn.run("app.server:app", host="127.0.0.1", port=a.port, log_level="warning")
