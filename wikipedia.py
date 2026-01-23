#!/usr/bin/env python3
"""
wikipedia.py

Extracted Wikipedia current events processing and geolocation helpers.
This module provides `fetch_and_process_current_events(out_path, user_agent)`
which mirrors the logic previously embedded in `pulse.py`.
"""
import json
import logging
import os
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple, List, Dict
import requests
from bs4 import BeautifulSoup
import re
import hashlib

USER_AGENT = "pulse/1.0 (+https://github.com/htmlfarmer/pulse)"


def _normalize_number_commas(s: Optional[str]) -> Optional[str]:
    if s is None:
        return s
    try:
        out = s
        while True:
            new = re.sub(r'(?<=\d),\s+(?=\d{3}\b)', ',', out)
            if new == out:
                break
            out = new
        return out
    except Exception:
        return s


class SuppressStderr:
    """Context manager to suppress C-level stderr from llama_cpp if loaded."""
    def __enter__(self):
        self.original_stderr = os.dup(2)
        self.devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(self.devnull, 2)

    def __exit__(self, exc_type, exc_val, exc_tb):
        os.dup2(self.original_stderr, 2)
        os.close(self.devnull)


try:
    from llama_cpp import Llama
except Exception:
    Llama = None


class AIModel:
    def __init__(self, model_path):
        self.llm = None
        self.config = {
            "llama_params": {"n_ctx": 2048, "n_threads": 4, "n_gpu_layers": 0, "verbose": False},
            "generation_params": {"temperature": 0.2, "top_k": 40, "top_p": 0.95, "repeat_penalty": 1.1, "max_tokens": 50, "stop": ["<|eot_id|>"]}
        }
        self.default_system_prompt = "You are a helpful assistant. Keep your answers concise."
        logging.info("--> AI Core: Loading model for geolocation (wikipedia.py)...")
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
        if not self.llm:
            logging.error("Error: The AI model is not loaded.")
            return "Error: Model not loaded."
        if system_prompt is None:
            system_prompt = "You are a geolocator. Based on the news headline, identify the main city and country. Respond ONLY with the format 'City, Country'. If you cannot determine the location, respond with 'Unknown'."
        messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_question}]
        try:
            response = self.llm.create_chat_completion(messages=messages, **self.config["generation_params"])
            content = response['choices'][0]['message'].get('content')
            return content.strip() if content else "Unknown"
        except Exception as e:
            logging.error(f"Error during AI generation: {e}")
            return "Error: Generation failed."


class RemoteLLMClient:
    def __init__(self, server_url: str):
        self.server_url = server_url.rstrip('/')
        if not self.server_url.endswith('/ask'):
            self.server_url = self.server_url + '/ask'
        # Determine provider now (allow env override)
        self.provider = os.environ.get('LLM_SERVER_PROVIDER', os.environ.get('LLM_DEFAULT_PROVIDER', 'gemini-2.5-flash-lite'))
        logging.info(f"Remote LLM provider set to: {self.provider}")
        self.available = False
        self.config = {"generation_params": {"max_tokens": 1024}}
        try:
            base = self.server_url.rsplit('/ask', 1)[0] or self.server_url
            r = requests.get(base, timeout=5)
            self.available = r.ok
        except Exception as e:
            logging.warning(f"RemoteLLMClient health check failed for {self.server_url}: {e}")
            self.available = False

    def ask(self, user_question: str, system_prompt: str = None) -> str:
        if not self.available:
            logging.error("Remote LLM server not available.")
            return "Error: Remote server not available."
        payload = {'prompt': user_question, 'conversation': []}
        if system_prompt:
            payload['system_prompt'] = system_prompt
        # Use the provider determined at init time (env override allowed)
        payload['provider'] = getattr(self, 'provider', os.environ.get('LLM_SERVER_PROVIDER', os.environ.get('LLM_DEFAULT_PROVIDER', 'gemini-2.5-flash-lite')))
        logging.debug(f"Remote LLM payload provider: {payload.get('provider')}")
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
        retry_count = int(os.environ.get('LLM_RETRY_COUNT', '2'))
        backoff_base = float(os.environ.get('LLM_RETRY_BACKOFF', '0.5'))
        attempt = 0
        last_exc = None
        while attempt <= retry_count:
            try:
                r = requests.post(self.server_url, json=payload, headers=headers, timeout=30)
                r.raise_for_status()
                ct = (r.headers.get('content-type') or '').lower()
                if 'application/json' in ct:
                    j = r.json()
                    resp_text = j.get('response', '') or ''
                else:
                    resp_text = r.text or ''
                resp_text = resp_text.strip()
                # Detect API key errors and optionally retry with provider=local
                if (('403' in resp_text and 'API key' in resp_text) or 'Your API key' in resp_text or 'leaked' in resp_text) and not payload.get('provider') == 'local':
                    logging.warning('Detected API key error in remote response; retrying once with provider=local')
                    payload['provider'] = 'local'
                    try:
                        r2 = requests.post(self.server_url, json=payload, headers=headers, timeout=30)
                        r2.raise_for_status()
                        ct2 = (r2.headers.get('content-type') or '').lower()
                        if 'application/json' in ct2:
                            j2 = r2.json()
                            return j2.get('response', 'Unknown') or 'Unknown'
                        return r2.text.strip() or 'Unknown'
                    except Exception as e:
                        logging.warning(f'Retry with provider=local failed: {e}')
                return resp_text if resp_text else 'Unknown'
            except requests.HTTPError as he:
                last_exc = he
                status = None
                try:
                    status = he.response.status_code
                except Exception:
                    pass
                logging.warning(f"Remote LLM POST returned HTTP error {he} (status={status}); attempt {attempt}/{retry_count}")
                if attempt < retry_count:
                    sleep_time = backoff_base * (2 ** attempt)
                    logging.info(f"Retrying after {sleep_time}s...")
                    time.sleep(sleep_time)
                    attempt += 1
                    continue
                if status is None or (isinstance(status, int) and 500 <= status < 600):
                    try:
                        stream_url = self.server_url
                        if '?stream=1' not in stream_url:
                            stream_url = stream_url + '?stream=1'
                        stream_headers = {'Content-Type': 'application/json', 'Accept': 'text/event-stream'}
                        with requests.post(stream_url, json=payload, headers=stream_headers, timeout=60, stream=True) as resp:
                            try:
                                resp.raise_for_status()
                            except Exception as e:
                                logging.error(f"Streaming POST failed: {e}")
                                raise
                            parts = []
                            for raw in resp.iter_lines(decode_unicode=True):
                                if raw is None:
                                    continue
                                line = raw.strip()
                                if not line:
                                    continue
                                if line.startswith('data:'):
                                    data = line[len('data:'):].strip()
                                    if data == '[DONE]':
                                        break
                                    parts.append(data)
                            final = '\n'.join(parts).strip()
                            return final or 'Unknown'
                    except Exception as e:
                        last_exc = e
                        logging.warning(f"Streaming fallback failed: {e}")
                break
            except requests.RequestException as re:
                last_exc = re
                logging.warning(f"Remote LLM request exception on attempt {attempt}/{retry_count}: {re}")
                if attempt < retry_count:
                    sleep_time = backoff_base * (2 ** attempt)
                    logging.info(f"Retrying after {sleep_time}s...")
                    time.sleep(sleep_time)
                    attempt += 1
                    continue
                else:
                    break
        logging.error(f"All Remote LLM attempts failed: {last_exc}")
        return f"Error: Remote request failed after {attempt} attempts: {last_exc}"


def get_coords_from_wikidata(location_name: str, user_agent: str) -> Optional[Tuple[float, float]]:
    if not location_name or location_name.lower() in ["unknown", "error: model not loaded.", "error: generation failed."]:
        return None
    logging.info(f"  -> Querying Wikidata for: {location_name}")
    search_url = f"https://www.wikidata.org/w/api.php?action=wbsearchentities&search={requests.utils.quote(location_name)}&language=en&limit=1&format=json"
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
        if 'P625' in claims:  # P625 is "coordinate location"
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


def to_geojson(features: List[Dict]) -> Dict:
    return {"type": "FeatureCollection", "features": features}


def fetch_and_process_current_events(out_path: Path, user_agent: str = USER_AGENT):
    """Main entry: fetch the Wikipedia Portal:Current_events page and geolocate items.
    Writes a GeoJSON file to `out_path`.
    """
    # Default to the known remote LLM server and prefer Gemini Flash Lite as provider
    llm_server_url = os.environ.get('LLM_SERVER_URL', 'http://ashy.tplinkdns.com:5005/ask')
    os.environ.setdefault('LLM_SERVER_PROVIDER', os.environ.get('LLM_SERVER_PROVIDER', 'gemini-2.5-flash-lite'))
    ai_model = None
    model_path = os.environ.get('LOCAL_LLM_MODEL_PATH', '/home/asher/.lmstudio/models/lmstudio-community/gemma-3-1b-it-GGUF/gemma-3-1b-it-Q4_K_M.gguf')
    try:
        remote_client = RemoteLLMClient(llm_server_url)
        if remote_client.available:
            logging.info(f"Using remote LLM server at {llm_server_url}")
            ai_model = remote_client
        else:
            logging.warning(f"Remote LLM server at {llm_server_url} not available.")
    except Exception as e:
        logging.warning(f"Remote LLM client init failed: {e}")

    if ai_model is None:
        allow_local = os.environ.get('ALLOW_LOCAL_LLM', '0').lower() in ('1', 'true', 'yes')
        if allow_local:
            logging.info('ALLOW_LOCAL_LLM is set — attempting to load local GGUF model as fallback.')
            if Path(model_path).exists():
                ai_model = AIModel(model_path)
                if not getattr(ai_model, 'llm', None):
                    logging.error("Failed to load local GGUF LLM. Continuing with fallback geolocation.")
                    ai_model = None
            else:
                logging.info(f"Local model path not found at {model_path}; cannot load fallback.")
        else:
            logging.info('Local GGUF fallback is disabled (set ALLOW_LOCAL_LLM=1 or pass --allow-local-llm to enable).')

    logging.info("Fetching full Wikipedia Current Events page for LLM extraction...")
    url = "https://en.wikipedia.org/wiki/Portal:Current_events"
    headers = {"User-Agent": user_agent}
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
    except requests.RequestException as e:
        logging.error(f"Failed to fetch current events page: {e}")
        return

    def _load_local_model():
        nonlocal ai_model
        if getattr(ai_model, '__class__', None) is AIModel and getattr(ai_model, 'llm', None):
            return ai_model
        if Path(model_path).exists():
            lm = AIModel(model_path)
            if getattr(lm, 'llm', None):
                logging.info("Loaded local model for fallback.")
                ai_model = lm
                return ai_model
            else:
                logging.warning("Local model class created but underlying Llama not available.")
                return None
        else:
            logging.info(f"Local model path not found at {model_path}; cannot load fallback.")
            return None

    def _ask_with_fallback(prompt, system_prompt=None):
        nonlocal ai_model
        if not ai_model:
            lm = _load_local_model()
            if not lm:
                return "Error: No LLM available."
            return lm.ask(prompt, system_prompt=system_prompt)
        try:
            r = ai_model.ask(prompt, system_prompt=system_prompt)
            if isinstance(r, str) and r.startswith('Error:'):
                logging.warning(f"LLM reported an error; attempting local fallback: {r}")
                lm = _load_local_model()
                if lm:
                    ai_model = lm
                    return lm.ask(prompt, system_prompt=system_prompt)
            return r
        except Exception as e:
            logging.error(f"LLM ask failed with exception: {e}")
            lm = _load_local_model()
            if lm:
                ai_model = lm
                return lm.ask(prompt, system_prompt=system_prompt)
            return f"Error: {e}"

    # Parse the page and extract news items (same logic as before)
    soup = BeautifulSoup(response.content, 'html.parser')
    for tag in soup(['script', 'style', 'header', 'footer', 'nav', 'aside']):
        tag.decompose()

    date_id_re = re.compile(r'^\d{4}_[A-Z][a-z]+_\d{1,2}$')
    news_items = []

    # Try today, then up to 3 previous days, selecting the first day that
    # contains actual content (some UTC offsets mean "today" on the server
    # may be a future-dated empty block). If none found, fall back to the
    # previous generic parsing approach.
    def _blocks_for_day(dtm):
        did = f"{dtm.year}_{dtm.strftime('%B')}_{dtm.day}"
        div = soup.find('div', id=did)
        blocks = []
        if div:
            blocks.append(div)
            for sib in div.find_next_siblings():
                if getattr(sib, 'name', None) != 'div':
                    continue
                if 'current-events-more' in (sib.get('class') or []):
                    blocks.append(sib)
                else:
                    break
        return blocks

    date_blocks = []
    for offset in range(0, 4):
        cand_day = datetime.now(timezone.utc) - timedelta(days=offset)
        cand_blocks = _blocks_for_day(cand_day)
        if not cand_blocks:
            continue
        # quick content check: look for at least one <p> or <ul> child with text
        has_content = False
        for cb in cand_blocks:
            content_div = cb.find('div', class_='current-events-content') or cb.find('div', class_='current-events-content description')
            if not content_div:
                continue
            for elem in content_div.children:
                if getattr(elem, 'name', None) in ('p', 'ul'):
                    txt = elem.get_text(' ', strip=True)
                    if txt and txt.strip():
                        has_content = True
                        break
            if has_content:
                break
        if has_content:
            date_blocks = cand_blocks
            break

    if not date_blocks:
        # Fallback: collect all date blocks as before
        date_blocks = soup.find_all('div', class_=lambda c: c and 'current-events-main' in c)
        if not date_blocks:
            containers = soup.find_all('div', class_='p-current-events-events') + soup.find_all('div', class_='current-events')
            for container in containers:
                date_blocks.extend(container.find_all('div', class_=lambda c: c and 'current-events-main' in c))
        if not date_blocks:
            for div in soup.find_all('div', id=True):
                if date_id_re.match(div['id']):
                    date_blocks.append(div)

    # Also include any top-level 'current-events' containers (regional/topic listings)
    # that contain content for the selected day. These often hold multiple topic
    # sections and should be parsed as well.
    try:
        additional = []
        for cont in soup.find_all('div', class_='current-events'):
            # prevent adding containers that are already in date_blocks
            if cont in date_blocks:
                continue
            content_div = cont.find('div', class_='current-events-content') or cont.find('div', class_='current-events-content description')
            if not content_div:
                continue
            has_text = False
            for elem in content_div.children:
                if getattr(elem, 'name', None) in ('p', 'ul'):
                    if elem.get_text(' ', strip=True):
                        has_text = True
                        break
            if has_text:
                additional.append(cont)
        # Append additional containers after original date blocks so ordering is preserved
        for a in additional:
            if a not in date_blocks:
                date_blocks.append(a)
    except Exception:
        pass

    for div in date_blocks:
        # Skip explicit 'more' placeholders only if they contain no content
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

    out_path.parent.mkdir(parents=True, exist_ok=True)
    running_flag = out_path.parent / 'current_events.running'
    try:
        running_flag.write_text('1', encoding='utf-8')
    except Exception:
        pass

    features = []
    if not news_items:
        logging.warning("No news items found for the current date section.")

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
        # Track the prompts/contexts sent to the LLM so we can report what the model studied
        llm_story_prompt = None
        llm_system_prompt = None
        llm_only_geocode_prompt = None
        llm_geocode_prompt = None

        if ai_model:
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
            # Record what we're asking the model
            llm_story_prompt = single_prompt
            llm_system_prompt = system_prompt
            llm_response = _ask_with_fallback(single_prompt, system_prompt=system_prompt)
            llm_raw = llm_response
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
                story = json.loads(cleaned)
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

            # Ask LLM-only geocode
            llm_only_geocode_raw = None
            llm_only_geocode_parsed = None
            if ai_model:
                try:
                    geocode_only_prompt = (
                        "You are a geocoder. Read the news item below and estimate the most likely coordinates "
                        "(latitude and longitude) of the main location associated with this story. "
                        "Respond ONLY with a JSON object containing numeric 'lat' and 'lng' fields, or the single word 'Unknown'.\n\n"
                        f"News Item: {news_item_text}"
                    )
                    # Record the exact geocode prompt the model will study
                    llm_only_geocode_prompt = geocode_only_prompt
                    llm_only_geocode_raw = _ask_with_fallback(geocode_only_prompt)
                    try:
                        print(f"LLM-only geocode raw for item {idx} ('{news_item_text[:60]}...'): {llm_only_geocode_raw}", file=sys.stderr)
                    except Exception:
                        pass
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
                        geo_obj2 = json.loads(cleaned_geo2)
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
                except Exception as e:
                    logging.error(f"Error during LLM-only geocode for item {idx}: {e}")
            else:
                llm_only_geocode_raw = None
                llm_only_geocode_parsed = None
        else:
            if news_links:
                first = news_links[0]
                if first.startswith('https://en.wikipedia.org/wiki/'):
                    wiki_title = first.split('/wiki/')[-1].replace('_', ' ')
                    coords = get_coords_from_wikidata(wiki_title, user_agent)
                    if coords:
                        lat, lng = coords[0], coords[1]
                        place_str = wiki_title
                        title = wiki_title
                else:
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
            if ai_model and place_str:
                try:
                    geocode_prompt = (
                        f"You are a geocoder. Given the place name or descriptor: '{place_str}'. "
                        "Respond ONLY with a JSON object containing numeric fields 'lat' and 'lng', or the single word 'Unknown'. "
                        "If you are unsure, respond with 'Unknown'."
                    )
                    # Record the place geocode prompt the model will study
                    llm_geocode_prompt = geocode_prompt
                    llm_geocode_raw = _ask_with_fallback(geocode_prompt)
                    logging.info(f"  -> LLM raw geocode reply for '{place_str}': {repr(llm_geocode_raw)[:1000]}")
                    try:
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
                    parsed_ok = False
                    def _dms_to_decimal(d, m, s, hemi):
                        try:
                            val = float(d) + (float(m) / 60.0) + (float(s) / 3600.0)
                            if hemi and hemi.upper() in ['S', 'W']:
                                return -abs(val)
                            return val
                        except Exception:
                            return None
                    try:
                        geo_obj = json.loads(cleaned_geo)
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
                        logging.warning(f"  -> LLM geocode parse failed for '{place_str}': {cleaned_geo[:1000]}")
                        try:
                            print(f"LLM geocode parse failed for '{place_str}': {cleaned_geo}", file=sys.stderr)
                        except Exception:
                            pass
                        import re as __re
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

        def is_close(a, b, tol=1.0):
            try:
                return abs(float(a) - float(b)) <= tol
            except Exception:
                return False

        use_llm = False
        if lat is not None and lng is not None:
            if lat_wiki is not None and lng_wiki is not None:
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
            if llm_only_geocode_parsed and isinstance(llm_only_geocode_parsed, dict):
                try:
                    llm_lat_v = float(llm_only_geocode_parsed.get('lat'))
                    llm_lng_v = float(llm_only_geocode_parsed.get('lng'))
                    lat = llm_lat_v
                    lng = llm_lng_v
                except Exception:
                    pass

            fid_src = (title or '') + '|' + (place_str or '') + '|' + str(idx)
            fid = hashlib.md5(fid_src.encode('utf-8')).hexdigest()
            decision = 'unknown'
            if 'use_llm' in locals() and use_llm:
                decision = 'llm'
            elif lat_wiki is not None and lng_wiki is not None:
                decision = 'wikidata'
            else:
                decision = 'llm' if llm_raw else 'fallback'

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
                    "llm_story_prompt": (llm_story_prompt[:2000] if isinstance(llm_story_prompt, str) else None),
                    "llm_system_prompt": (llm_system_prompt[:1000] if isinstance(llm_system_prompt, str) else None),
                    "llm_geocode_prompt": (llm_geocode_prompt[:1000] if isinstance(llm_geocode_prompt, str) else None),
                    "llm_geocode_raw": (llm_geocode_raw[:1000] if isinstance(llm_geocode_raw, str) else None),
                    "llm_geocode_parsed": llm_geocode_parsed,
                    "llm_only_geocode_prompt": (llm_only_geocode_prompt[:1000] if isinstance(llm_only_geocode_prompt, str) else None),
                    "llm_only_geocode_raw": (llm_only_geocode_raw[:1000] if isinstance(llm_only_geocode_raw, str) else None),
                    "llm_only_geocode_parsed": llm_only_geocode_parsed,
                    "decision": decision,
                    "source": "Wikipedia Current Events",
                    "geolocation_source": "llm" if llm_sentence else "fallback"
                },
                "geometry": {"type": "Point", "coordinates": [lng, lat]}
            }
            features.append(feature)
            try:
                tmp = out_path.parent / (out_path.name + '.tmp')
                tmp.write_text(json.dumps(to_geojson(features), ensure_ascii=False, indent=2), encoding='utf-8')
                os.replace(str(tmp), str(out_path))
            except Exception as e:
                logging.warning(f"Failed incremental write for current events: {e}")
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


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--out', default='data/current_events.geojson')
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    fetch_and_process_current_events(Path(args.out))
