"""Check the tracking status of a FedEx shipment via the FedEx REST APIs.

Two calls are involved:
  1. POST /oauth/token                 -> OAuth2 client-credentials bearer token
  2. POST /track/v1/trackingnumbers    -> Track API (tracking by number)

Credentials are read from the environment rather than hard-coded:
  FEDEX_API_KEY, FEDEX_API_SECRET, and optionally FEDEX_BASE_URL
  (defaults to the sandbox, https://apis-sandbox.fedex.com).

Usage:
  export FEDEX_API_KEY=...  FEDEX_API_SECRET=...
  python fedex_tracking.py 123456789012
  python fedex_tracking.py --raw 123456789012   # full FedEx response
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any

import requests

SANDBOX_URL = "https://apis-sandbox.fedex.com"
PRODUCTION_URL = "https://apis.fedex.com"

# Simple in-process token cache keyed by (base_url, api_key). FedEx tokens
# live ~60 minutes; re-requesting one per tracking call is wasteful and can
# trip rate limits.
_token_cache: dict[tuple[str, str], tuple[str, float]] = {}


class FedExError(RuntimeError):
    """Raised when the FedEx API returns an error or an unexpected payload."""


def _post(url: str, **kwargs: Any) -> requests.Response:
    try:
        return requests.post(url, **kwargs)
    except requests.RequestException as exc:
        raise FedExError(f"Network error calling {url}: {exc}") from exc


def _get_access_token(base_url: str, api_key: str, api_secret: str, timeout: float) -> str:
    cache_key = (base_url, api_key)
    cached = _token_cache.get(cache_key)
    if cached and cached[1] > time.time():
        return cached[0]

    resp = _post(
        f"{base_url}/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": api_key,
            "client_secret": api_secret,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=timeout,
    )
    if resp.status_code != 200:
        raise FedExError(f"OAuth token request failed ({resp.status_code}): {resp.text}")

    body = resp.json()
    token = body["access_token"]
    # Refresh 60 s before the stated expiry to avoid edge-of-window failures.
    expires_in = int(body.get("expires_in", 3600))
    _token_cache[cache_key] = (token, time.time() + max(expires_in - 60, 0))
    return token


def track_shipment(
    tracking_number: str,
    *,
    api_key: str | None = None,
    api_secret: str | None = None,
    base_url: str | None = None,
    include_detailed_scans: bool = True,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Return a normalised summary of a FedEx shipment's tracking status.

    Returns a dict with keys:
      tracking_number, status_code, status, description, delivered,
      last_updated, location, estimated_delivery, events (list), raw.
    The ``raw`` key holds the full ``completeTrackResults`` entry for callers
    that need fields not surfaced here.

    Raises FedExError on HTTP errors, API-level errors (e.g. tracking number
    not found), or malformed responses.
    """
    tracking_number = tracking_number.strip().replace(" ", "")
    if not tracking_number.isalnum():
        raise FedExError(f"Invalid tracking number: {tracking_number!r}")

    api_key = api_key or os.environ.get("FEDEX_API_KEY")
    api_secret = api_secret or os.environ.get("FEDEX_API_SECRET")
    base_url = (base_url or os.environ.get("FEDEX_BASE_URL") or SANDBOX_URL).rstrip("/")
    if not api_key or not api_secret:
        raise FedExError("FEDEX_API_KEY and FEDEX_API_SECRET must be set")

    token = _get_access_token(base_url, api_key, api_secret, timeout)

    payload = {
        "includeDetailedScans": include_detailed_scans,
        "trackingInfo": [{"trackingNumberInfo": {"trackingNumber": tracking_number}}],
    }
    resp = _post(
        f"{base_url}/track/v1/trackingnumbers",
        json=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-locale": "en_US",
        },
        timeout=timeout,
    )
    if resp.status_code == 401:
        # Token revoked/expired early: drop it so the next call re-auths.
        _token_cache.pop((base_url, api_key), None)
    if resp.status_code != 200:
        raise FedExError(f"Track request failed ({resp.status_code}): {resp.text}")

    body = resp.json()
    try:
        complete = body["output"]["completeTrackResults"][0]
        result = complete["trackResults"][0]
    except (KeyError, IndexError, TypeError) as exc:
        raise FedExError(f"Unexpected Track API response: {body}") from exc

    # FedEx returns HTTP 200 with a per-package error for unknown numbers etc.
    if "error" in result:
        err = result["error"]
        raise FedExError(f"{err.get('code')}: {err.get('message')}")

    latest = result.get("latestStatusDetail", {}) or {}
    scan_loc = latest.get("scanLocation", {}) or {}
    events = [
        {
            "timestamp": ev.get("date"),
            "event_type": ev.get("eventType"),
            "description": ev.get("eventDescription"),
            "city": (ev.get("scanLocation") or {}).get("city"),
            "state": (ev.get("scanLocation") or {}).get("stateOrProvinceCode"),
            "country": (ev.get("scanLocation") or {}).get("countryCode"),
        }
        for ev in result.get("scanEvents", []) or []
    ]

    def _date(kind: str) -> str | None:
        for d in result.get("dateAndTimes", []) or []:
            if d.get("type") == kind:
                return d.get("dateTime")
        return None

    est = result.get("estimatedDeliveryTimeWindow", {}).get("window", {}) or {}
    estimated = est.get("ends") or est.get("begins") or _date("ESTIMATED_DELIVERY")

    return {
        "tracking_number": complete.get("trackingNumber", tracking_number),
        "status_code": latest.get("code"),
        "status": latest.get("statusByLocale"),
        "description": latest.get("description"),
        "delivered": latest.get("code") == "DL",
        "last_updated": events[0]["timestamp"] if events else None,
        "location": ", ".join(
            p for p in (scan_loc.get("city"), scan_loc.get("stateOrProvinceCode"),
                        scan_loc.get("countryCode")) if p
        ) or None,
        "estimated_delivery": estimated,
        "actual_delivery": _date("ACTUAL_DELIVERY"),
        "events": events,
        "raw": complete,
    }


if __name__ == "__main__":
    import json

    args = sys.argv[1:]
    raw = "--raw" in args
    args = [a for a in args if a != "--raw"]
    if len(args) != 1:
        sys.exit(f"usage: {sys.argv[0]} [--raw] <tracking-number>")
    try:
        info = track_shipment(args[0])
    except FedExError as e:
        sys.exit(f"error: {e}")
    # --raw prints FedEx's complete response; default prints the summary.
    print(json.dumps(info["raw"] if raw else {k: v for k, v in info.items() if k != "raw"}, indent=2))
