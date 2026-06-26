#!/usr/bin/env python3
"""Geocoding + timezone helpers (OpenStreetMap / Nominatim + timezonefinder).

Resolves a place name to coordinates and the base (non-DST) UTC offset so a
birth chart can be built from a city name. No API key required; nothing here
depends on any astrology service.
"""
from __future__ import annotations

import threading
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from timezonefinder import TimezoneFinder

# Nominatim's usage policy requires an identifying User-Agent.
_NOMINATIM_HEADERS = {"User-Agent": "KundaliMCP/1.0 (self-hosted)"}

# Loading TimezoneFinder's data is expensive; share one instance behind a lock
# since the server may resolve places from multiple threads.
_tf = TimezoneFinder()
_tf_lock = threading.Lock()


def tz_for(lat: float, lon: float):
    """Return (base_utc_offset_hours, iana_tz_id) for a coordinate.

    The offset is *standard* (non-DST) time, which is what most Vedic chart
    inputs expect alongside a separate DST flag.
    """
    try:
        with _tf_lock:
            tzid = _tf.timezone_at(lat=lat, lng=lon)
        if not tzid:
            return None, None
        now = datetime.now(ZoneInfo(tzid))
        base = now.utcoffset() - now.dst()
        offset = base.total_seconds() / 3600.0
        return (int(offset) if offset == int(offset) else round(offset, 2)), tzid
    except Exception:
        return None, None


def geocode(query: str, limit: int = 6):
    """Search OpenStreetMap/Nominatim and enrich each match with timezone.

    Returns a list of dicts: name, display_name, lat, lon, tz_offset, tzid.
    """
    query = (query or "").strip()
    if len(query) < 3:
        return []
    r = requests.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": query, "format": "jsonv2", "addressdetails": 1,
                "limit": limit, "accept-language": "en"},
        headers=_NOMINATIM_HEADERS, timeout=15,
    )
    r.raise_for_status()
    out = []
    for item in r.json():
        try:
            lat, lon = float(item["lat"]), float(item["lon"])
        except (KeyError, ValueError):
            continue
        offset, tzid = tz_for(lat, lon)
        addr = item.get("address", {})
        name = (addr.get("city") or addr.get("town") or addr.get("village")
                or addr.get("municipality") or addr.get("county")
                or addr.get("state") or item.get("name")
                or item.get("display_name", "").split(",")[0])
        out.append({
            "name": name, "display_name": item.get("display_name", ""),
            "lat": lat, "lon": lon, "tz_offset": offset, "tzid": tzid,
        })
    return out
