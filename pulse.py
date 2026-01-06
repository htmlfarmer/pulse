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

try:
    from llama_cpp import Llama
except ImportError:
    print("FATAL: llama-cpp-python is not installed. Please run 'pip install llama-cpp-python'.", file=sys.stderr)
    sys.exit(1)
import requests
from bs4 import BeautifulSoup

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
                "repeat_penalty": 1.1, "max_tokens": 50, "stop": ["<|eot_id|>"] # ["\n", "<|eot_id|>"]
            }
        }
        self.default_system_prompt = "You are a helpful assistant. Keep your answers concise."
        logging.info("--> AI Core: Loading model for geolocation...")
        try:
            with SuppressStderr():
                self.llm = Llama(model_path=model_path, **self.config["llama_params"])
            if self.llm is not None:
                logging.info("--> AI Core: Model loaded successfully.")
            else:
                logging.error("!!! FATAL: AI Model not loaded (llm is None).")
        except Exception as e:
            logging.error(f"!!! FATAL: Error loading model: {e}")
            os.system('notify-send "AI Model Error" "Could not load the language model. Check terminal." -i error')

    def ask(self, user_question: str, system_prompt: str = None) -> str:
        """Asks the AI a question and returns the cleaned string response. Allows custom system prompt."""
        if not self.llm:
            logging.error("Error: The AI model is not loaded.")
            return "Error: Model not loaded."

        if system_prompt is None:
            system_prompt = "You are a geolocator. Based on the news headline, identify the main city and country. Respond ONLY with the format 'City, Country'. If you cannot determine the location, respond with 'Unknown'."

        messages = [
            {"role": "system", "content": system_prompt},
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

    logging.info("Fetching full Wikipedia Current Events page for LLM extraction...")
    url = "https://en.wikipedia.org/wiki/Portal:Current_events"
    headers = {"User-Agent": user_agent}
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
    except requests.RequestException as e:
        logging.error(f"Failed to fetch current events page: {e}")
        return


    # Extract only the day/date sections and their news items
    soup = BeautifulSoup(response.content, 'html.parser')
    for tag in soup(['script', 'style', 'header', 'footer', 'nav', 'aside']):
        tag.decompose()

    # Find all h3 headers that are dates (e.g., 'January 5, 2026 (Monday)')
    import re as _re
    # Find all date sections and extract news items from each, stopping at .current-events-more
    date_id_re = _re.compile(r'^\d{4}_[A-Z][a-z]+_\d{1,2}$')
    news_items = []
    more_link_found = False
    for div in soup.find_all('div', id=True):
        if 'current-events-more' in div.get('class', []):
            more_link_found = True
            break
        if date_id_re.match(div['id']):
            content_div = div.find('div', class_='current-events-content description')
            if content_div:
                current_category = None
                for elem in content_div.children:
                    if getattr(elem, 'name', None) == 'p':
                        # If <p> contains only a category header (bold or matches known set), set as current_category
                        b = elem.find('b')
                        ptext = elem.get_text(' ', strip=True)
                        # Heuristic: if <p> is just a category header (bold and short, or matches known set)
                        if b and len(ptext) < 40 and (b.get_text(strip=True) == ptext):
                            current_category = ptext
                        elif ptext:
                            # Prepend category if available
                            if current_category:
                                news_items.append(f"{current_category}: {ptext}")
                            else:
                                news_items.append(ptext)
                    elif getattr(elem, 'name', None) == 'ul':
                        for li in elem.find_all('li', recursive=False):
                            litext = li.get_text(' ', strip=True)
                            if litext:
                                if current_category:
                                    news_items.append(f"{current_category}: {litext}")
                                else:
                                    news_items.append(litext)
    # If no .current-events-more found, just process all date sections found



    # Erase the output file at the start of each run
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text('', encoding="utf-8")

    # Process each news item one at a time with the LLM
    features = []
    import json as _json
    if not news_items:
        logging.warning("No news items found for the current date section.")
    # --- Minimal direct LLM test ---
    for idx, news_item in enumerate(news_items):
        single_prompt = (
            "You are a news geolocator. Given the following news item, extract a JSON object with: "
            "'title', 'summary', 'place' (city, country), 'lat', 'lng' (for the city country and place), and 'event_text' (the full story text). "
            "Be as detailed as possible in the summary and event_text fields. "
            "If you do not know the latitude or longitude, estimate them based on the place. "
            "Respond ONLY with a JSON object, no extra text. "
            f"News Item: {news_item}"
        )
        system_prompt = (
            "You are a helpful assistant that extracts a single news story as a JSON object with 'title', 'summary', 'place', 'lat', 'lng', and 'event_text'. "
            "Be as detailed as possible in the summary and event_text fields. "
            "If you do not know the latitude or longitude, estimate them based on the place. "
            "Do not include any extra commentary or explanation."
        )
        ai_model.config["generation_params"]["max_tokens"] = 2048
        llm_response = ai_model.ask(single_prompt, system_prompt=system_prompt)
        # Remove code block markers and join lines if needed
        cleaned = llm_response.strip()
        # Extract only the content between ```json and the next code block end (```)
        if '```json' in cleaned:
            start = cleaned.find('```json') + 7
            end = cleaned.find('```', start)
            if end == -1:
                json_str = cleaned[start:].strip()
            else:
                json_str = cleaned[start:end].strip()
            cleaned = json_str
        elif '```' in cleaned:
            start = cleaned.find('```') + 3
            end = cleaned.find('```', start)
            if end == -1:
                json_str = cleaned[start:].strip()
            else:
                json_str = cleaned[start:end].strip()
            cleaned = json_str
        # Otherwise, use the whole cleaned string
        # Optionally, join lines if the output is split
        try:
            story = _json.loads(cleaned)
            if not isinstance(story, dict):
                raise ValueError("LLM did not return a dict")
        except Exception as e:
            logging.warning(f"LLM did not return valid JSON for item {idx}: {e}\nResponse: {llm_response[:300]}")
            continue
        title = story.get('title') or ''
        summary = story.get('summary') or ''


        # Support both string and dict for 'place', and allow lat/lng inside place or at top level
        place = story.get('place')
        city = country = ''
        if isinstance(place, dict):
            city = place.get('city', '')
            country = place.get('country', '')
            place_str = f"{city}, {country}".strip(', ')
            lat = place.get('lat', story.get('lat'))
            lng = place.get('lng', story.get('lng'))
        elif isinstance(place, str):
            place_str = place
            lat = story.get('lat')
            lng = story.get('lng')
        else:
            place_str = ''
            lat = story.get('lat')
            lng = story.get('lng')

        event_text = story.get('event_text') or ''
        try:
            lat = float(lat)
            lng = float(lng)
        except (TypeError, ValueError):
            lat = lng = None

        coords = get_coords_from_wikidata(place_str, user_agent) if place_str else None
        lat_wiki = lng_wiki = None
        if coords:
            lat_wiki, lng_wiki = coords[0], coords[1]

        # If both LLM and Wikidata coords exist, compare them
        def is_close(a, b, tol=1.0):
            try:
                return abs(float(a) - float(b)) <= tol
            except Exception:
                return False

        use_llm = False
        if lat is not None and lng is not None:
            if lat_wiki is not None and lng_wiki is not None:
                # If both exist, check if LLM is close to Wikidata
                if is_close(lat, lat_wiki, tol=2.0) and is_close(lng, lng_wiki, tol=2.0):
                    use_llm = True
                else:
                    use_llm = False
            else:
                use_llm = True
        elif lat_wiki is not None and lng_wiki is not None:
            lat, lng = lat_wiki, lng_wiki
            use_llm = False

        if lat is not None and lng is not None:
            feature = {
                "type": "Feature",
                "properties": {
                    "title": title,
                    "summary": summary,
                    "place": place,
                    "lat": lat,
                    "lng": lng,
                    "event_text": event_text,
                    "source": "Wikipedia Current Events",
                    "geolocation_source": "llm"
                },
                "geometry": {"type": "Point", "coordinates": [lng, lat]}
            }
            features.append(feature)
        else:
            logging.warning(f"Could not geolocate story: {title} / {place_str}")
        time.sleep(0.5)

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