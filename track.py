import sys
import os
import json

from dotenv import load_dotenv

load_dotenv()


from fedex_tracking import track_shipment, PRODUCTION_URL



tracking_number = sys.argv[1] if (len(sys.argv) > 1 and sys.argv[1] != '--raw') else os.environ.get("DEFAULT_TRACKING") #"879965153633"
info = track_shipment(tracking_number, base_url=PRODUCTION_URL)
full = info["raw"]["trackResults"][0]        # one package's complete record
print(json.dumps(full, indent=2))
#print(full["serviceDetail"]["description"])  # pull out any individual field


