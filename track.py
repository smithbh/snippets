"""Print the full FedEx tracking record for a shipment (production API).

Usage:
  python track.py <tracking-number>
  python track.py                     # uses DEFAULT_TRACKING from .env

Configuration (in a local .env file, see .env.example):
  FEDEX_API_KEY, FEDEX_API_SECRET    required
  DEFAULT_TRACKING                   optional; used when no number is given
"""

import json
import os
import sys

from dotenv import load_dotenv

from fedex_tracking import FedExError, PRODUCTION_URL, track_shipment

load_dotenv()

# --raw is accepted for symmetry with fedex_tracking.py; this script always
# prints the full record, so it is simply ignored rather than mistaken for
# the tracking number.
args = [a for a in sys.argv[1:] if a != "--raw"]
tracking_number = args[0] if args else os.environ.get("DEFAULT_TRACKING", "").strip()

if not tracking_number:
    sys.exit(
        "error: no tracking number given.\n"
        "Pass one as an argument (python track.py <tracking-number>) or set\n"
        "DEFAULT_TRACKING in your local .env file (see .env.example)."
    )

try:
    info = track_shipment(tracking_number, base_url=PRODUCTION_URL)
except FedExError as e:
    sys.exit(f"error: {e}")

full = info["raw"]["trackResults"][0]  # one package's complete record
print(json.dumps(full, indent=2))
# print(full["serviceDetail"]["description"])  # pull out any individual field
