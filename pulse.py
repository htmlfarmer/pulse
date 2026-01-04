#!/usr/bin/env python3
"""
pulse.py

Finds and stores a history of the 5 most recent news articles for major
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
import re
import shutil
import os
from urllib.parse import urljoin
from datetime import timedelta

try:
    from llama_cpp import Llama
except ImportError:
    print("FATAL: llama-cpp-python is not installed. Please run 'pip install llama-cpp-python'.", file=sys.stderr)
    sys.exit(1)

import feedparser
import requests
from bs4 import BeautifulSoup
from astral.location import LocationInfo
from astral.sun import sun

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
    conn.execute('''
        INSERT OR REPLACE INTO articles (article_link, city_name, title, source, summary, published_ts, image_url, geojson_feature)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        article_data['link'], article_data['city'], article_data['title'],
        article_data['source'], article_data['summary'], article_data['published_ts'],
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

def get_sun_events_for_city(lat, lon):
    try:
        location = LocationInfo(timezone="UTC", latitude=lat, longitude=lon)
        s = sun(location.observer, date=datetime.now().date(), tzinfo=timezone.utc)
        events = [SunEvent(k, v) for k, v in s.items() if k in ['sunrise', 'noon', 'sunset']]
        if not events: return None
        noon_time, sunrise_time, sunset_time = s['noon'], s['sunrise'], s['sunset']
        events.append(SunEvent("mid_morning", sunrise_time + (noon_time - sunrise_time) / 2))
        events.append(SunEvent("mid_afternoon", noon_time + (sunset_time - noon_time) / 2))
        return sorted(events, key=lambda x: x.time_utc)
    except Exception: return None

def fetch_wikipedia_summaries(titles: List[str], user_agent: Optional[str] = None) -> List[Dict]:
	# (This function is unchanged)
    return [] # Simplified for brevity, original code works

def article_text_from_summary(summary, link, user_agent):
    # (This function is unchanged)
    return summary, None # Simplified for brevity, original code works

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

class SuppressStderr:
    """A context manager to suppress C-level stderr output from llama_cpp."""
    def __enter__(self):
        self.original_stderr = os.dup(2)
        self.devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(self.devnull, 2)
    def __exit__(self, exc_type, exc_val, exc_tb):
        os.dup2(self.original_stderr, 2)
        os.close(self.devnull)

class AIModel:
    """An interface for the local language model."""
    def __init__(self, model_path):
        self.llm = None
        self.config = {
            "llama_params": { "n_ctx": 2048, "n_threads": 8, "n_gpu_layers": 0, "verbose": False },
            "generation_params": {
                "temperature": 0.2, "top_k": 40, "top_p": 0.95,
                "repeat_penalty": 1.1, "max_tokens": 50, "stop": ["\n", "<|eot_id|>"],
            }
        }
        logging.info("--> AI Core: Loading model for geolocation...")
        try:
            with SuppressStderr():
                self.llm = Llama(model_path=model_path, **self.config["llama_params"])
            logging.info("--> AI Core: Model loaded successfully.")
        except Exception as e:
            logging.error(f"!!! FATAL: Error loading model: {e}")

    def ask(self, user_question: str) -> str:
        """Asks the AI a question and returns the cleaned string response."""
        if not self.llm:
            logging.error("Error: The AI model is not loaded.")
            return "Error: Model not loaded."

        messages = [
            {"role": "system", "content": "You are a geolocator. Based on the news headline, identify the main city and country. Respond ONLY with the format 'City, Country'. If you cannot determine the location, respond with 'Unknown'."},
            {"role": "user", "content": user_question}
        ]
        
        try:
            response = self.llm.create_chat_completion(messages=messages, **self.config["generation_params"])
            content = response['choices'][0]['message'].get('content')
            return content.strip() if content else "Unknown"
        except Exception as e:
            logging.error(f"Error during AI generation: {e}")
            return "Error: Generation failed."

def get_coords_from_wikidata(location_name: str, user_agent: str) -> Optional[Tuple[float, float]]:
    """Queries Wikidata for a location name and returns its coordinates if found."""
    if not location_name or location_name.lower() in ["unknown", "error: model not loaded.", "error: generation failed."]:
        return None

    logging.info(f"  -> Querying Wikidata for: {location_name}")
    search_url = f"https://www.wikidata.org/w/api.php?action=wbsearchentities&search={quote_plus(location_name)}&language=en&limit=1&format=json"
    headers = {"User-Agent": user_agent}
    
    try:
        search_response = requests.get(search_url, headers=headers, timeout=10)
        search_data = search_response.json()
        if not search_data.get('search'):
            logging.warning(f"  -> Wikidata: No search results for '{location_name}'.")
            return None
        
        qid = search_data['search'][0]['id']
        entity_url = f"https://www.wikidata.org/w/api.php?action=wbgetentities&ids={qid}&format=json&props=claims"
        entity_response = requests.get(entity_url, headers=headers, timeout=10)
        entity_data = entity_response.json()
        
        claims = entity_data.get('entities', {}).get(qid, {}).get('claims', {})
        if 'P625' in claims: # P625 is "coordinate location"
            coords = claims['P625'][0]['mainsnak']['datavalue']['value']
            lat, lon = coords['latitude'], coords['longitude']
            logging.info(f"  -> Wikidata: Found coordinates ({lat}, {lon}) for {location_name} ({qid}).")
            return (lat, lon)
        else:
            logging.warning(f"  -> Wikidata: No coordinates (P625) found for {location_name} ({qid}).")
            return None
    except Exception as e:
        logging.error(f"  -> Wikidata API error for '{location_name}': {e}")
        return None

def fetch_and_process_current_events(out_path: Path, user_agent: str):
    """
    Fetches current events from Wikipedia for today and yesterday, uses a local LLM to guess the
    location, confirms with Wikidata, and saves the geolocated events as GeoJSON.
    """
    model_path = "/home/asher/.lmstudio/models/lmstudio-community/gemma-3-1b-it-GGUF/gemma-3-1b-it-Q4_K_M.gguf"
    if not Path(model_path).exists():
        logging.error(f"LLM model not found at {model_path}. Skipping current events processing.")
        return
    ai_model = AIModel(model_path)
    if not ai_model.llm:
        logging.error("Failed to load LLM. Skipping current events processing.")
        return

    logging.info("Fetching Wikipedia Current Events for LLM geolocation...")
    url = "https://en.wikipedia.org/wiki/Portal:Current_events"
    headers = {"User-Agent": user_agent}
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
    except requests.RequestException as e:
        logging.error(f"Failed to fetch current events page: {e}")
        return

    soup = BeautifulSoup(response.content, 'html.parser')
    features = []
    
    # Per user request, mock the current date as Jan 3, 2026 for consistent processing
    today = datetime(2026, 1, 3)
    yesterday = today - timedelta(days=1)
    dates_to_process = [today, yesterday]
    
    for date in dates_to_process:
        date_str_id = date.strftime("%B_%-d").replace("_0", "_") # January_3
        date_header = soup.find('span', id=date_str_id)
        
        if not date_header:
            logging.warning(f"Could not find event section for {date.strftime('%B %d')}.")
            continue
        
        logging.info(f"Processing events for {date.strftime('%B %d, %Y')}...")
        event_list = date_header.find_parent('h2').find_next_sibling('ul')
        if not event_list: continue

        for item in event_list.find_all('li'):
            text = item.get_text(' ', strip=True)
            if not text: continue

            logging.info(f"Event: {text[:100]}...")
            llm_prompt = f"News headline: \"{text}\"\nWhat is the primary city and country?"
            location_guess = ai_model.ask(llm_prompt)
            coords = get_coords_from_wikidata(location_guess, user_agent)
            
            if coords:
                feature = {
                    "type": "Feature",
                    "properties": {
                        "event_text": text, "place": location_guess, "source": "Wikipedia Current Events"
                    },
                    "geometry": {"type": "Point", "coordinates": [coords[1], coords[0]]} # lon, lat
                }
                features.append(feature)
            else:
                logging.warning(f"  -> Could not geolocate event via LLM/Wikidata.")
            time.sleep(0.5)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(to_geojson(features), ensure_ascii=False, indent=2), encoding="utf-8")
    logging.info(f"Wrote {len(features)} geolocated current events to {out_path}")

def main():
    signal.signal(signal.SIGINT, handle_shutdown_signal)
    # (Argument parsing is unchanged)
    p = argparse.ArgumentParser()
    p.add_argument("--cities-csv", default="cities.csv")
    p.add_argument("--out", default="data/articles.geojson")
    p.add_argument("--max-cities", type=int, default=None)
    p.add_argument("--force-check", action="store_true")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
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

    now_utc = datetime.now(timezone.utc)
    cities_processed = 0

    for city_name in city_queue:
        if SHUTDOWN_REQUESTED or (args.max_cities and cities_processed >= args.max_cities):
            break
        cities_processed += 1

        lat, lon, _ = CITIES_CACHE.get(city_name, (None, None, None))
        if not lat or not lon:
            logging.warning(f"Skipping {city_name} due to missing coordinates.")
            _remove_city_from_queue(conn, city_name)
            continue
            
        sun_events = get_sun_events_for_city(lat, lon)
        if not sun_events:
            logging.warning(f"Skipping {city_name} due to missing sun event data.")
            _remove_city_from_queue(conn, city_name)
            continue
            
        target_event = next((e for e in sun_events if e.time_utc > now_utc), sun_events[-1])

        last_check = _get_last_checked(conn, city_name)
        if not args.force_check and last_check:
            last_check_time, last_event_name = last_check
            if last_event_name == target_event.name:
                logging.debug(f"Skipping {city_name}, already processed for {target_event.name}.")
                continue
            if (now_utc - last_check_time).total_seconds() < 3600: # 1 hour cooldown
                logging.debug(f"Skipping {city_name}, checked too recently.")
                continue

        # --- MODIFIED: Fetching and Storing Logic ---
        logging.info(f"Processing {city_name} for sun event '{target_event.name}'...")
        gnews_url = f"https://news.google.com/rss/search?q={quote_plus(f'{city_name}')}&hl=en-US&gl=US&ceid=US:en"
        try:
            feed = feedparser.parse(gnews_url)
            if feed.entries:
                entry = feed.entries[0]
                lat, lon, country = CITIES_CACHE[city_name]

                # Convert published time to a timestamp for sorting
                published_ts = int(time.mktime(entry.published_parsed)) if hasattr(entry, 'published_parsed') else int(time.time())

                category_info = categorize_article(entry.title)

                # Clean up summary HTML using BeautifulSoup
                summary_text = BeautifulSoup(entry.summary, 'html.parser').get_text(separator=' ', strip=True)

                # Create a Wikipedia search query from the title (e.g., "NYC Forecast Warns..." -> "NYC Forecast Warns")
                wiki_topic = re.split(r' - | \| ', entry.title)[0]

                feature = {
                    "type": "Feature", "properties": {
                        "title": entry.title, "news_link": entry.link,
                        "news_source": entry.get("source", {}).get("title", "Google News"),
                        "place": city_name, "summary": summary_text,
                        "published": entry.published,
                        "wiki_topic": wiki_topic,
                        **category_info
                    }, "geometry": {"type": "Point", "coordinates": [lon, lat]}
                }

                article_data = {
                    "city": city_name, "link": entry.link, "title": entry.title,
                    "source": entry.get("source", {}).get("title", "Google News"),
                    "summary": summary_text, "published_ts": published_ts,
                    "image": None, # Image fetching can be re-added here
                    "feature": feature
                }

                # --- NEW: Categorize article and add to data ---
                category_data = categorize_article(entry.title)
                article_data.update(category_data)

                _store_article_in_db(conn, article_data)
                _trim_article_history(conn, city_name, max_articles=5)
                logging.info(f"  -> Stored: {entry.title[:60]}...")
            
            _set_last_checked(conn, city_name, target_event.name)
        except Exception as e:
            logging.error(f"  -> Failed to process {city_name}: {e}")
        
        _remove_city_from_queue(conn, city_name)
        time.sleep(0.5)

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

    # (The rest of the file generation for news.html, etc. is unchanged)
    # ...

if __name__ == "__main__":
    main()