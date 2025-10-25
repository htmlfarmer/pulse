"""Simple Nominatim REST helper (fallback) inspired by watch/geolocation.py
This is a lightweight alternative to geopy when you want a straightforward
HTTP lookup.
"""
import logging
from typing import Optional, Tuple
import requests


def nominatim_lookup(q: str, user_agent: Optional[str] = None, timeout: int = 8) -> Optional[Tuple[float, float, str]]:
    headers = {"User-Agent": user_agent or "pulse/1.0 (+https://github.com/htmlfarmer/pulse)"}
    params = {"format": "json", "q": q, "limit": 1, "addressdetails": 1}
    try:
        r = requests.get("https://nominatim.openstreetmap.org/search", params=params, headers=headers, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        if data:
            item = data[0]
            lat = float(item.get("lat"))
            lon = float(item.get("lon"))
            display_name = item.get("display_name", "")
            return lat, lon, display_name
    except requests.exceptions.RequestException as e:
        logging.warning(f"Nominatim request failed for '{q}': {e}")
    return None
