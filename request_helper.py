"""Small HTTP helper adapted from watch/request.py but using requests.
Provides a polite User-Agent and simple error handling.
"""
import logging
from typing import Optional
import requests


def request_text(url: str, user_agent: Optional[str] = None, timeout: int = 10) -> Optional[str]:
    headers = {"User-Agent": user_agent or "pulse/1.0 (+https://github.com/htmlfarmer/pulse)"}
    try:
        r = requests.get(url, headers=headers, timeout=timeout)
        r.raise_for_status()
        return r.text
    except requests.exceptions.HTTPError as e:
        logging.warning(f"HTTP error fetching {url}: {e}")
    except requests.exceptions.RequestException as e:
        logging.warning(f"Request error fetching {url}: {e}")
    return None
