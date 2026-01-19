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
        if Llama is None:
            logging.error("llama-cpp Llama class not available; skipping model load.")
            self.llm = None
            return
        try:
            with SuppressStderr():
                self.llm = Llama(model_path=model_path, **self.config["llama_params"])
            if self.llm is not None:
                logging.info("--> AI Core: Model loaded successfully.")
            else:
                logging.error("!!! FATAL: AI Model not loaded (llm is None).")
        except Exception as e:
            logging.error(f"!!! FATAL: Error loading model: {e}")
            self.llm = None

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
    ai_model = None
    if Path(model_path).exists():
        ai_model = AIModel(model_path)
        if not getattr(ai_model, 'llm', None):
            logging.error("Failed to load LLM. Continuing with fallback geolocation.")
            ai_model = None
    else:
        logging.info(f"LLM model not found at {model_path}. Continuing with fallback geolocation.")

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

    import re as _re
    date_id_re = _re.compile(r'^\d{4}_[A-Z][a-z]+_\d{1,2}$')
    # Collect all date blocks across the page (multiple `.current-events` containers exist)
    news_items = []  # list of dicts: { 'text': str, 'links': [url, ...] }
    more_link_found = bool(soup.find_all('div', class_='current-events-more'))

    # First try to find explicit date blocks by class
    date_blocks = soup.find_all('div', class_=lambda c: c and 'current-events-main' in c)
    # Also collect from any portal container variants if none found yet
    if not date_blocks:
        containers = soup.find_all('div', class_='p-current-events-events') + soup.find_all('div', class_='current-events')
        for container in containers:
            date_blocks.extend(container.find_all('div', class_=lambda c: c and 'current-events-main' in c))
    # Fallback: look for divs with date-like ids anywhere
    if not date_blocks:
        for div in soup.find_all('div', id=True):
            if date_id_re.match(div['id']):
                date_blocks.append(div)

    for div in date_blocks:
        # If we hit the 'more' block, stop collecting further days
        if 'current-events-more' in (div.get('class') or []):
            break
        content_div = div.find('div', class_='current-events-content') or div.find('div', class_='current-events-content description')
        if not content_div:
            continue
        current_category = None
        for elem in content_div.children:
            if getattr(elem, 'name', None) == 'p':
                b = elem.find('b')
                ptext = elem.get_text(' ', strip=True)
                if b and len(ptext) < 60 and (b.get_text(strip=True) == ptext):
                    current_category = ptext
                elif ptext:
                    text = f"{current_category}: {ptext}" if current_category else ptext
                    news_items.append({'text': text, 'links': []})
            elif getattr(elem, 'name', None) == 'ul':
                for li in elem.find_all('li', recursive=False):
                    litext = li.get_text(' ', strip=True)
                    if not litext:
                        continue
                    links = []
                    for a in li.find_all('a', href=True):
                        href = a['href']
                        if href.startswith('//'):
                            href = 'https:' + href
                        elif href.startswith('/'):
                            href = 'https://en.wikipedia.org' + href
                        links.append(href)
                    text = f"{current_category}: {litext}" if current_category else litext
                    news_items.append({'text': text, 'links': links})



    # Erase the output file at the start of each run and create a running flag
    out_path.parent.mkdir(parents=True, exist_ok=True)
    running_flag = out_path.parent / 'current_events.running'
    try:
        running_flag.write_text('1', encoding='utf-8')
    except Exception:
        pass

    # Process each news item one at a time. If an LLM is available use it,
    # otherwise attempt a lightweight geolocation fallback using linked Wikipedia pages or heuristics.
    features = []
    import json as _json
    import hashlib
    if not news_items:
        logging.warning("No news items found for the current date section.")
    # --- Minimal direct LLM test ---
    for idx, item in enumerate(news_items):
        news_item_text = item.get('text', '')
        news_links = item.get('links', [])

        title = ''
        summary = ''
        place_str = ''
        lat = lng = None
        event_text = news_item_text
        llm_sentence = None
        llm_raw = None

        if ai_model and getattr(ai_model, 'llm', None):
            single_prompt = (
                "You are a news geolocator. Given the following news item, extract a JSON object with: "
                "'title', 'summary', 'place' (city, country), 'lat', 'lng', and 'event_text'. Respond ONLY with a JSON object, no extra text. "
                f"News Item: {news_item_text}"
            )
            system_prompt = (
                "You are a helpful assistant that extracts a single news story as a JSON object with 'title', 'summary', 'place', 'lat', 'lng', and 'event_text'. "
                "Do not include any extra commentary or explanation."
            )
            ai_model.config["generation_params"]["max_tokens"] = 1024
            llm_response = ai_model.ask(single_prompt, system_prompt=system_prompt)
            llm_raw = llm_response
            # Print full LLM story extraction raw reply for immediate visibility
            try:
                print(f"LLM story raw for item {idx}: {llm_raw}", file=sys.stderr)
            except Exception:
                pass
            cleaned = llm_response.strip()
            if '```json' in cleaned:
                start = cleaned.find('```json') + 7
                end = cleaned.find('```', start)
                cleaned = cleaned[start:end].strip() if end != -1 else cleaned[start:].strip()
            elif '```' in cleaned:
                start = cleaned.find('```') + 3
                end = cleaned.find('```', start)
                cleaned = cleaned[start:end].strip() if end != -1 else cleaned[start:].strip()
            try:
                story = _json.loads(cleaned)
                if isinstance(story, dict):
                    title = story.get('title') or ''
                    summary = story.get('summary') or ''
                    place = story.get('place')
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
                    event_text = story.get('event_text') or event_text
                    llm_sentence = story.get('place') if story.get('place') else None
            except Exception as e:
                logging.warning(f"LLM did not return valid JSON for item {idx}: {e}\nResponse: {llm_response[:300]}")
            # --- NEW: Ask LLM directly (only) to estimate lat/lng from the news item text ---
            llm_only_geocode_raw = None
            llm_only_geocode_parsed = None
            if ai_model and getattr(ai_model, 'llm', None):
                try:
                    geocode_only_prompt = (
                        "You are a geocoder. Read the news item below and estimate the most likely coordinates "
                        "(latitude and longitude) of the main location associated with this story. "
                        "Respond ONLY with a JSON object containing numeric 'lat' and 'lng' fields, or the single word 'Unknown'.\n\n"
                        f"News Item: {news_item_text}"
                    )
                    llm_only_geocode_raw = ai_model.ask(geocode_only_prompt)
                    # Print raw reply immediately for visibility
                    try:
                        print(f"LLM-only geocode raw for item {idx} ('{news_item_text[:60]}...'): {llm_only_geocode_raw}", file=sys.stderr)
                    except Exception:
                        pass
                    # Tolerant parse: reuse the cleaning/parsing logic used below for llm_geocode_raw
                    cleaned_geo2 = (llm_only_geocode_raw or '').strip()
                    if '```json' in cleaned_geo2:
                        start = cleaned_geo2.find('```json') + 7
                        end = cleaned_geo2.find('```', start)
                        cleaned_geo2 = cleaned_geo2[start:end].strip() if end != -1 else cleaned_geo2[start:].strip()
                    elif '```' in cleaned_geo2:
                        start = cleaned_geo2.find('```') + 3
                        end = cleaned_geo2.find('```', start)
                        cleaned_geo2 = cleaned_geo2[start:end].strip() if end != -1 else cleaned_geo2[start:].strip()
                    parsed_ok2 = False
                    try:
                        geo_obj2 = _json.loads(cleaned_geo2)
                        if isinstance(geo_obj2, dict) and 'lat' in geo_obj2 and 'lng' in geo_obj2:
                            lat_geo2 = float(geo_obj2['lat'])
                            lng_geo2 = float(geo_obj2['lng'])
                            llm_only_geocode_parsed = {'lat': lat_geo2, 'lng': lng_geo2}
                            parsed_ok2 = True
                            try:
                                print(f"LLM-only geocode parsed for item {idx}: {lat_geo2},{lng_geo2}", file=sys.stderr)
                            except Exception:
                                pass
                    except Exception:
                        parsed_ok2 = False
                    if not parsed_ok2:
                        # numeric extraction
                        import re as __re2
                        m = __re2.search(r'([-+]?[0-9]{1,3}\.?[0-9]*)\D+([-+]?[0-9]{1,3}\.?[0-9]*)', cleaned_geo2)
                        if m:
                            try:
                                lat_geo2 = float(m.group(1))
                                lng_geo2 = float(m.group(2))
                                llm_only_geocode_parsed = {'lat': lat_geo2, 'lng': lng_geo2}
                                parsed_ok2 = True
                                try:
                                    print(f"LLM-only geocode parsed (numeric) for item {idx}: {lat_geo2},{lng_geo2}", file=sys.stderr)
                                except Exception:
                                    pass
                            except Exception:
                                parsed_ok2 = False
                        # lat/lng label patterns
                        if not parsed_ok2:
                            m2 = __re2.search(r'lat[^0-9-]*([-+]?[0-9]{1,3}\.?[0-9]*)[^0-9-]+lng[^0-9-]*([-+]?[0-9]{1,3}\.?[0-9]*)', cleaned_geo2, __re2.I)
                            if m2:
                                try:
                                    lat_geo2 = float(m2.group(1))
                                    lng_geo2 = float(m2.group(2))
                                    llm_only_geocode_parsed = {'lat': lat_geo2, 'lng': lng_geo2}
                                    parsed_ok2 = True
                                    try:
                                        print(f"LLM-only geocode parsed (pattern) for item {idx}: {lat_geo2},{lng_geo2}", file=sys.stderr)
                                    except Exception:
                                        pass
                                except Exception:
                                    parsed_ok2 = False
                    # DMS parsing
                    if not parsed_ok2 and ('°' in cleaned_geo2 or '′' in cleaned_geo2 or '"' in cleaned_geo2):
                        dms_matches = __re2.findall(r"([0-9]{1,3})°\s*([0-9]{1,2})['′]\s*([0-9]{1,2}(?:\.[0-9]+)?)\"?\s*([NnSsEeWw])", cleaned_geo2)
                        if len(dms_matches) >= 2:
                            try:
                                def _dms_to_decimal_local(d, m, s, hemi):
                                    val = float(d) + (float(m) / 60.0) + (float(s) / 3600.0)
                                    if hemi and hemi.upper() in ['S', 'W']:
                                        return -abs(val)
                                    return val
                                lat_geo2 = _dms_to_decimal_local(*dms_matches[0])
                                lng_geo2 = _dms_to_decimal_local(*dms_matches[1])
                                if lat_geo2 is not None and lng_geo2 is not None:
                                    llm_only_geocode_parsed = {'lat': lat_geo2, 'lng': lng_geo2}
                                    parsed_ok2 = True
                                    try:
                                        print(f"LLM-only geocode parsed (DMS) for item {idx}: {lat_geo2},{lng_geo2}", file=sys.stderr)
                                    except Exception:
                                        pass
                            except Exception:
                                parsed_ok2 = False
                except Exception as e:
                    logging.error(f"Error during LLM-only geocode for item {idx}: {e}")
            else:
                llm_only_geocode_raw = None
                llm_only_geocode_parsed = None
        else:
            # No LLM available: attempt lightweight fallback geolocation
            # Prefer a linked Wikipedia article if present
            if news_links:
                first = news_links[0]
                if first.startswith('https://en.wikipedia.org/wiki/'):
                    # Derive title from URL and query Wikidata
                    wiki_title = first.split('/wiki/')[-1].replace('_', ' ')
                    coords = get_coords_from_wikidata(wiki_title, user_agent)
                    if coords:
                        lat, lng = coords[0], coords[1]
                        place_str = wiki_title
                        title = wiki_title
                else:
                    # Try to extract a capitalized Place phrase from the text as a heuristic
                    import re
                    m = re.search(r" in ([A-Z][a-zA-Z\-]+(?: [A-Z][a-zA-Z\-]+)*)", news_item_text)
                    if m:
                        candidate = m.group(1)
                        coords = get_coords_from_wikidata(candidate, user_agent)
                        if coords:
                            lat, lng = coords[0], coords[1]
                            place_str = candidate
                            title = candidate
        try:
            lat = float(lat) if lat is not None else None
            lng = float(lng) if lng is not None else None
        except (TypeError, ValueError):
            lat = lng = None

        coords = get_coords_from_wikidata(place_str, user_agent) if place_str else None
        lat_wiki = lng_wiki = None
        llm_geocode_raw = None
        llm_geocode_parsed = None
        if coords:
            lat_wiki, lng_wiki = coords[0], coords[1]
            logging.info(f"  -> Using Wikidata coords for '{place_str}': {lat_wiki},{lng_wiki}")
        else:
            logging.warning(f"  -> Wikidata: No search results for '{place_str}'. Will ask LLM for coords if available.")
            # If Wikidata couldn't find coords, try asking the LLM specifically for lat/lng
            if ai_model and getattr(ai_model, 'llm', None) and place_str:
                try:
                    geocode_prompt = (
                        f"You are a geocoder. Given the place name or descriptor: '{place_str}'. "
                        "Respond ONLY with a JSON object containing numeric fields 'lat' and 'lng', or the single word 'Unknown'. "
                        "If you are unsure, respond with 'Unknown'."
                    )
                    llm_geocode_raw = ai_model.ask(geocode_prompt)
                    logging.info(f"  -> LLM raw geocode reply for '{place_str}': {repr(llm_geocode_raw)[:1000]}")
                    try:
                        # Also print to stderr for immediate console visibility
                        print(f"LLM geocode raw for '{place_str}': {llm_geocode_raw}", file=sys.stderr)
                    except Exception:
                        pass
                    cleaned_geo = llm_geocode_raw.strip()
                    if '```json' in cleaned_geo:
                        start = cleaned_geo.find('```json') + 7
                        end = cleaned_geo.find('```', start)
                        cleaned_geo = cleaned_geo[start:end].strip() if end != -1 else cleaned_geo[start:].strip()
                    elif '```' in cleaned_geo:
                        start = cleaned_geo.find('```') + 3
                        end = cleaned_geo.find('```', start)
                        cleaned_geo = cleaned_geo[start:end].strip() if end != -1 else cleaned_geo[start:].strip()
                    # Try to parse JSON
                    parsed_ok = False
                    # Helper: convert DMS to decimal
                    def _dms_to_decimal(d, m, s, hemi):
                        try:
                            val = float(d) + (float(m) / 60.0) + (float(s) / 3600.0)
                            if hemi and hemi.upper() in ['S', 'W']:
                                return -abs(val)
                            return val
                        except Exception:
                            return None
                    try:
                        geo_obj = _json.loads(cleaned_geo)
                        if isinstance(geo_obj, dict) and 'lat' in geo_obj and 'lng' in geo_obj:
                            try:
                                lat_geo = float(geo_obj['lat'])
                                lng_geo = float(geo_obj['lng'])
                                lat_wiki, lng_wiki = lat_geo, lng_geo
                                llm_geocode_parsed = {'lat': lat_geo, 'lng': lng_geo}
                                logging.info(f"  -> LLM geocode provided coords for '{place_str}': {lat_wiki},{lng_wiki}")
                                try:
                                    print(f"LLM geocode parsed for '{place_str}': {lat_geo},{lng_geo}", file=sys.stderr)
                                except Exception:
                                    pass
                                parsed_ok = True
                            except Exception:
                                parsed_ok = False
                    except Exception:
                        parsed_ok = False

                    if not parsed_ok:
                        # Not valid JSON — try tolerant parsing heuristics
                        logging.warning(f"  -> LLM geocode parse failed for '{place_str}': {cleaned_geo[:1000]}")
                        try:
                            print(f"LLM geocode parse failed for '{place_str}': {cleaned_geo}", file=sys.stderr)
                        except Exception:
                            pass
                        import re as __re
                        # 0) Try to parse DMS pairs if present (e.g. 12°34'56" N, 45°67'89" E)
                        if not parsed_ok and ('°' in cleaned_geo or '′' in cleaned_geo or '"' in cleaned_geo):
                            dms_matches = __re.findall(r"([0-9]{1,3})°\s*([0-9]{1,2})['′]\s*([0-9]{1,2}(?:\.[0-9]+)?)\"?\s*([NnSsEeWw])", cleaned_geo)
                            if len(dms_matches) >= 2:
                                try:
                                    lat_geo = _dms_to_decimal(*dms_matches[0])
                                    lng_geo = _dms_to_decimal(*dms_matches[1])
                                    if lat_geo is not None and lng_geo is not None:
                                        lat_wiki, lng_wiki = lat_geo, lng_geo
                                        llm_geocode_parsed = {'lat': lat_geo, 'lng': lng_geo}
                                        logging.info(f"  -> Extracted DMS coords from LLM reply for '{place_str}': {lat_geo},{lng_geo}")
                                        try:
                                            print(f"LLM geocode parsed (DMS) for '{place_str}': {lat_geo},{lng_geo}", file=sys.stderr)
                                        except Exception:
                                            pass
                                        parsed_ok = True
                                except Exception:
                                    parsed_ok = False

                        # 1) Try to find two floats in the reply (lat, lng)
                        m = __re.search(r'([-+]?[0-9]{1,3}\.?[0-9]*)\D+([-+]?[0-9]{1,3}\.?[0-9]*)', cleaned_geo)
                        if m:
                            try:
                                lat_geo = float(m.group(1))
                                lng_geo = float(m.group(2))
                                lat_wiki, lng_wiki = lat_geo, lng_geo
                                llm_geocode_parsed = {'lat': lat_geo, 'lng': lng_geo}
                                logging.info(f"  -> Extracted numeric coords from LLM reply for '{place_str}': {lat_geo},{lng_geo}")
                                try:
                                    print(f"LLM geocode parsed (numeric) for '{place_str}': {lat_geo},{lng_geo}", file=sys.stderr)
                                except Exception:
                                    pass
                                parsed_ok = True
                            except Exception:
                                parsed_ok = False

                        # 2) Try patterns like 'lat: 12.3, lng: 45.6' or 'latitude=.. longitude=..'
                        if not parsed_ok:
                            m2 = __re.search(r'lat[^0-9-]*([-+]?[0-9]{1,3}\.?[0-9]*)[^0-9-]+lng[^0-9-]*([-+]?[0-9]{1,3}\.?[0-9]*)', cleaned_geo, __re.I)
                            if m2:
                                try:
                                    lat_geo = float(m2.group(1))
                                    lng_geo = float(m2.group(2))
                                    lat_wiki, lng_wiki = lat_geo, lng_geo
                                    llm_geocode_parsed = {'lat': lat_geo, 'lng': lng_geo}
                                    logging.info(f"  -> Extracted lat/lng pattern from LLM reply for '{place_str}': {lat_geo},{lng_geo}")
                                    try:
                                        print(f"LLM geocode parsed (pattern) for '{place_str}': {lat_geo},{lng_geo}", file=sys.stderr)
                                    except Exception:
                                        pass
                                    parsed_ok = True
                                except Exception:
                                    parsed_ok = False

                        # 3) If the reply is 'City, Country' or similar, try to resolve that place via Wikidata
                        if not parsed_ok:
                            m3 = __re.match(r"^\s*([\w\-\.\'\s]{2,}),\s*([\w\-\.\'\s]{2,})\s*$", cleaned_geo)
                            if m3:
                                place_guess = f"{m3.group(1).strip()}, {m3.group(2).strip()}"
                                logging.info(f"  -> LLM geocode looks like place string '{place_guess}', trying Wikidata lookup...")
                                try:
                                    coords2 = get_coords_from_wikidata(place_guess, user_agent)
                                    if coords2:
                                        lat_wiki, lng_wiki = coords2[0], coords2[1]
                                        llm_geocode_parsed = {'lat': lat_wiki, 'lng': lng_wiki}
                                        logging.info(f"  -> Resolved LLM place '{place_guess}' via Wikidata: {lat_wiki},{lng_wiki}")
                                        try:
                                            print(f"LLM geocode resolved via Wikidata for '{place_str}': {lat_wiki},{lng_wiki}", file=sys.stderr)
                                        except Exception:
                                            pass
                                        parsed_ok = True
                                except Exception:
                                    parsed_ok = False
                except Exception as e:
                    logging.error(f"  -> Error asking LLM for geocode of '{place_str}': {e}")

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
            # If an LLM-only geocode was parsed separately (more precise), prefer it
            if llm_only_geocode_parsed and isinstance(llm_only_geocode_parsed, dict):
                try:
                    llm_lat_v = float(llm_only_geocode_parsed.get('lat'))
                    llm_lng_v = float(llm_only_geocode_parsed.get('lng'))
                    # Override the working coords with the LLM-only parsed coords
                    lat = llm_lat_v
                    lng = llm_lng_v
                except Exception:
                    # If parsing fails, fall back to previously-determined coords
                    pass

            # stable feature id so client can dedupe
            fid_src = (title or '') + '|' + (place_str or '') + '|' + str(idx)
            fid = hashlib.md5(fid_src.encode('utf-8')).hexdigest()
            # Decide which source was used for final coordinates
            decision = 'unknown'
            if 'use_llm' in locals() and use_llm:
                decision = 'llm'
            elif lat_wiki is not None and lng_wiki is not None:
                decision = 'wikidata'
            else:
                decision = 'llm' if llm_raw else 'fallback'

            # Normalize numeric comma spacing in the LLM-generated summary
            summary = _normalize_number_commas(summary)
            feature = {
                "type": "Feature",
                "id": fid,
                "properties": {
                    "id": fid,
                    "title": title or (news_item_text[:100] + '...'),
                    "summary": summary,
                    "place": place_str,
                    "lat": lat,
                    "lng": lng,
                    "event_text": event_text,
                    "url": news_links[0] if news_links else None,
                    "event_links": news_links,
                    "llm_sentence": llm_sentence,
                    "llm_raw": (llm_raw[:1000] if isinstance(llm_raw, str) else None),
                    "llm_geocode_raw": (llm_geocode_raw[:1000] if isinstance(llm_geocode_raw, str) else None),
                    "llm_geocode_parsed": llm_geocode_parsed,
                    "llm_only_geocode_raw": (llm_only_geocode_raw[:1000] if isinstance(llm_only_geocode_raw, str) else None),
                    "llm_only_geocode_parsed": llm_only_geocode_parsed,
                    "decision": decision,
                    "source": "Wikipedia Current Events",
                    "geolocation_source": "llm" if llm_sentence else "fallback"
                },
                "geometry": {"type": "Point", "coordinates": [lng, lat]}
            }
            features.append(feature)
            # write incremental file atomically so client can pick up additions
            try:
                tmp = out_path.parent / (out_path.name + '.tmp')
                tmp.write_text(_json.dumps(to_geojson(features), ensure_ascii=False, indent=2), encoding='utf-8')
                os.replace(str(tmp), str(out_path))
            except Exception as e:
                logging.warning(f"Failed incremental write for current events: {e}")
            # Append concise debug line to debug log
            try:
                dbg = out_path.parent / 'current_events_debug.log'
                llm_geo_str = ''
                if llm_geocode_parsed:
                    llm_geo_str = f"{llm_geocode_parsed.get('lat')},{llm_geocode_parsed.get('lng')}"
                elif llm_geocode_raw:
                    llm_geo_str = llm_geocode_raw.replace('\n', ' ')[:200]
                line = f"{int(time.time())}\t{fid}\t{decision}\t{place_str}\t{lat},{lng}\t{llm_geo_str}\t{title}\n"
                with dbg.open('a', encoding='utf-8') as df:
                    df.write(line)
            except Exception:
                pass
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