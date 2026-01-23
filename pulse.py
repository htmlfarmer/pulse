#!/usr/bin/env python3
"""
pulse.py

Finds and stores a history of the most recent news articles for major
cities, mapping them based on solar cycles with clustering for usability.
"""
import argparse
import json
import time
import logging
from pathlib import Path
from typing import List, Dict, Tuple, Optional, NamedTuple
import csv
from urllib.parse import quote_plus
from datetime import datetime, timezone
import random
import signal
import sys
import sqlite3
import os
import re

try:
    from llama_cpp import Llama
except ImportError:
    Llama = None
    logging.warning("llama-cpp-python not installed; LLM features will be disabled.")
import requests
from bs4 import BeautifulSoup
# Wikipedia helpers are implemented in a separate module
from wikipedia import fetch_and_process_current_events, get_coords_from_wikidata, RemoteLLMClient, AIModel, SuppressStderr

# --- Global flag for graceful shutdown ---
SHUTDOWN_REQUESTED = False

def handle_shutdown_signal(signum, frame):
    global SHUTDOWN_REQUESTED
    if SHUTDOWN_REQUESTED:
        print("\nForce quitting immediately.", file=sys.stderr)
        sys.exit(1)
    SHUTDOWN_REQUESTED = True
    print("\nGraceful shutdown initiated. Saving results...", file=sys.stderr)

USER_AGENT = "pulse/1.0 (+https://github.com/htmlfarmer/pulse)"
CITIES_CACHE = {}

class SunEvent(NamedTuple):
    name: str
    time_utc: datetime

def load_cities_csv(file_path: Path):
    # (This function is unchanged)
    global CITIES_CACHE
    if not file_path.exists(): sys.exit(f"FATAL: Cities file not found at {file_path}")
    try:
        with file_path.open(mode='r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                city_name = row.get('city', '').strip()
                if city_name:
                    try: CITIES_CACHE[city_name] = (float(row['lat']), float(row['lng']), row.get('country', ''))
                    except (ValueError, KeyError): continue
            logging.info(f"Loaded {len(CITIES_CACHE)} cities from {file_path}")
    except Exception as e: sys.exit(f"Failed to load cities from {file_path}: {e}")


def _normalize_number_commas(s: Optional[str]) -> Optional[str]:
    """Fix LLM-inserted spaces after commas in numeric groupings.

    Examples: '1, 600' -> '1,600', '2, 000, 000' -> '2,000,000'.
    """
    if s is None:
        return s
    try:
        out = s
        # Iteratively collapse patterns like ", 600" into ",600" until stable
        while True:
            new = re.sub(r'(?<=\d),\s+(?=\d{3}\b)', ',', out)
            if new == out:
                break
            out = new
        return out
    except Exception:
        return s

# --- MODIFIED: Database functions now handle article history ---
def _init_db(conn: sqlite3.Connection):
    """Initializes all necessary tables in the database."""
    conn.execute('''
        CREATE TABLE IF NOT EXISTS last_checked (
            city TEXT PRIMARY KEY, last_check_ts INTEGER, last_event TEXT
        )''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS city_queue (
            city_name TEXT PRIMARY KEY, process_order INTEGER
        )''')
    # NEW TABLE for storing historical articles
    conn.execute('''
        CREATE TABLE IF NOT EXISTS articles (
            article_link TEXT PRIMARY KEY,
            city_name TEXT NOT NULL,
            title TEXT,
            source TEXT,
            summary TEXT,
            published_ts INTEGER,
            image_url TEXT,
            geojson_feature TEXT NOT NULL
        )''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_city_name_published ON articles (city_name, published_ts DESC)')
    conn.commit()

def _store_article_in_db(conn, article_data):
    """Inserts or replaces an article in the database."""
    # Normalize numeric thousands-grouping in summaries before storing
    def _maybe_norm(s):
        try:
            return _normalize_number_commas(s)
        except Exception:
            return s

    conn.execute('''
        INSERT OR REPLACE INTO articles (article_link, city_name, title, source, summary, published_ts, image_url, geojson_feature)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        article_data['link'], article_data['city'], article_data['title'],
        article_data['source'], _maybe_norm(article_data.get('summary')), article_data['published_ts'],
        article_data['image'], json.dumps(article_data['feature'])
    ))
    conn.commit()

def _trim_article_history(conn, city_name, max_articles=5):
    """Keeps only the N most recent articles for a city."""
    conn.execute('''
        DELETE FROM articles WHERE article_link IN (
            SELECT article_link FROM articles
            WHERE city_name = ?
            ORDER BY published_ts DESC
            LIMIT -1 OFFSET ?
        )
    ''', (city_name, max_articles))
    conn.commit()

def _get_all_features_from_db(conn) -> List[Dict]:
    """Retrieves all stored GeoJSON features from the database."""
    cur = conn.execute('SELECT geojson_feature FROM articles')
    return [json.loads(row[0]) for row in cur.fetchall()]

# (Other helper functions like _get_last_checked, _populate_city_queue, etc. are unchanged)
def _get_last_checked(conn, city):
    cur = conn.execute('SELECT last_check_ts, last_event FROM last_checked WHERE city = ?', (city,))
    row = cur.fetchone()
    return (datetime.fromtimestamp(row[0], tz=timezone.utc), row[1]) if row else None

def _set_last_checked(conn, city, event):
    now_ts = int(time.time())
    conn.execute('REPLACE INTO last_checked(city, last_check_ts, last_event) VALUES (?, ?, ?)', (city, now_ts, event))
    conn.commit()

def _get_city_queue(conn) -> List[str]:
    cur = conn.execute('SELECT city_name FROM city_queue ORDER BY process_order')
    return [row[0] for row in cur.fetchall()]

def _populate_city_queue(conn, cities: List[str]):
    conn.execute('DELETE FROM city_queue')
    shuffled_cities = list(cities)
    random.shuffle(shuffled_cities)
    conn.executemany('INSERT INTO city_queue (city_name, process_order) VALUES (?, ?)',
                     [(city, i) for i, city in enumerate(shuffled_cities)])
    conn.commit()
    logging.info(f"Created a new randomized queue of {len(shuffled_cities)} cities.")

def _remove_city_from_queue(conn, city_name):
    conn.execute('DELETE FROM city_queue WHERE city_name = ?', (city_name,))
    conn.commit()

def to_geojson(features: List[Dict]) -> Dict:
	return {"type": "FeatureCollection", "features": features}

def categorize_article(title: str) -> Dict[str, str]:
    """Analyzes an article title to assign a category, icon, and color."""
    title_lower = title.lower()
    categories = {
        'Alert': {'keywords': ['earthquake', 'quake', 'emergency', 'alert'], 'icon': 'exclamation-triangle', 'color': 'red'},
        'Weather': {'keywords': ['weather', 'snow', 'rain', 'sun', 'storm', 'forecast', 'temperature', 'hurricane'], 'icon': 'cloud', 'color': 'blue'},
        'Sports': {'keywords': ['sports', 'game', 'match', 'team', 'player', 'draws', 'football', 'soccer', 'nba', 'olympics'], 'icon': 'futbol-o', 'color': 'green'},
        'Politics': {'keywords': ["robinson's", 'election', 'government', 'senate', 'congress', 'political', 'mayor'], 'icon': 'bank', 'color': 'darkred'},
        'Business': {'keywords': ['business', 'economy', 'market', 'stocks', 'finance', 'shares'], 'icon': 'line-chart', 'color': 'purple'},
        'Technology': {'keywords': ['tech', 'apple', 'google', 'microsoft', 'software', 'hardware', 'ai'], 'icon': 'cogs', 'color': 'orange'},
    }
    for category, data in categories.items():
        if any(keyword in title_lower for keyword in data['keywords']):
            return {"category": category, "icon": data['icon'], "markerColor": data['color']}
    # Default category if no keywords match
    return {"category": "News", "icon": "info-circle", "markerColor": "gray"}


# --- Local LLM Integration & Advanced Geolocation ---

# SuppressStderr moved to `wikipedia.py` (used by Wikipedia's local LLM fallback).

# AIModel moved to `wikipedia.py` (keeps LLM loading/ask behavior used for geolocation there).

# RemoteLLMClient functionality moved to `wikipedia.py`

# `get_coords_from_wikidata` moved to `wikipedia.py`. Use the imported version from `wikipedia` module.

# The full `fetch_and_process_current_events` implementation now lives in `wikipedia.py`.
# This file imports and re-uses `fetch_and_process_current_events` directly from that module.
# Any remaining inline Wikipedia-specific logic was removed to avoid duplication.

# Entire Wikipedia function body removed — implementation resides in `wikipedia.py` now.


def main():
    signal.signal(signal.SIGINT, handle_shutdown_signal)
    # (Argument parsing is unchanged)
    p = argparse.ArgumentParser()
    p.add_argument("--cities-csv", default="cities.csv")
    p.add_argument("--out", default="data/articles.geojson")
    p.add_argument("--max-cities", type=int, default=None)
    p.add_argument("--force-check", action="store_true")
    p.add_argument("--llm-server", default=None, help="Override LLM server URL (e.g. http://host:5005/ask)")
    p.add_argument("--llm-provider", default=None, help="Hint provider to remote LLM server (e.g. 'local' or 'gemini-3-flash-preview'). Defaults to 'gemini-2.5-flash-lite'.")
    p.add_argument("--allow-local-llm", action='store_true', help="Allow loading a local GGUF model as fallback if the remote server is unavailable")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

    # Honor command-line overrides for LLM server and provider to make testing easier
    if args.llm_server:
        os.environ['LLM_SERVER_URL'] = args.llm_server
        logging.info(f"LLM server overridden to {args.llm_server}")
    if args.llm_provider:
        os.environ['LLM_SERVER_PROVIDER'] = args.llm_provider
        logging.info(f"LLM server provider override set to {args.llm_provider}")
    if args.allow_local_llm:
        os.environ['ALLOW_LOCAL_LLM'] = '1'
        logging.info('Local GGUF model fallback explicitly ENABLED for this run.')
    load_cities_csv(Path(args.cities_csv))
    user_agent = USER_AGENT

    db_path = Path('.cache') / 'pulse_state.sqlite'
    db_path.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=10)
    _init_db(conn)

    city_queue = _get_city_queue(conn)
    if not city_queue:
        _populate_city_queue(conn, CITIES_CACHE.keys())
        city_queue = _get_city_queue(conn)
    logging.info(f"Starting run with {len(city_queue)} cities in queue.")

    # (Removed sun event logic. If you want to process cities, add your new logic here.)

    # --- MODIFIED: Finalization Step ---
    logging.info("Run finished. Generating output files from database...")
    all_features = _get_all_features_from_db(conn)

    # --- Generate live news data file ---
    live_news_out_path = Path('data/live_news.json')
    try:
        cur = conn.execute('SELECT geojson_feature, published_ts FROM articles ORDER BY published_ts DESC LIMIT 15')
        features = []
        max_ts = 0
        rows = cur.fetchall()
        # Reverse so oldest are first, for chronological display on client
        for row in reversed(rows):
            feature_obj = json.loads(row[0])
            ts = int(row[1])
            feature_obj['properties']['published_ts'] = ts
            features.append(feature_obj)
            if ts > max_ts:
                max_ts = ts
        
        live_data = {
            'type': 'FeatureCollection',
            'features': features,
            'latest': max_ts
        }
        live_news_out_path.write_text(json.dumps(live_data, ensure_ascii=False), encoding="utf-8")
        logging.info(f"Wrote {len(features)} recent articles to {live_news_out_path}")
    except Exception as e:
        logging.error(f"Failed to generate live news file: {e}")

    conn.close() # Close DB connection

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(to_geojson(all_features), ensure_ascii=False, indent=2), encoding="utf-8")
    logging.info(f"Wrote {len(all_features)} total historical features to {out_path}")

    # --- NEW: Process Wikipedia Current Events ---
    current_events_out_path = Path('data/current_events.geojson')
    fetch_and_process_current_events(current_events_out_path, user_agent)

if __name__ == "__main__":
    main()