#!/usr/bin/env python3
"""
pulse.py

Renamed from news_study.py — fetch RSS + Wikipedia, extract place names,
geocode them and produce a GeoJSON file suitable for display on an OpenStreetMap
frontend (Leaflet). Designed as a starting point — respect robots.txt, rate
limits and site terms of service.

Usage:
  python pulse.py --feeds feeds.txt --wikipedia wiki_topics.txt --out web/data/articles.geojson --limit 10

"""
import argparse
import json
import time
import logging
from pathlib import Path
from typing import List, Dict, Tuple, Optional
from collections import Counter

import feedparser
import requests
from bs4 import BeautifulSoup
from pathlib import Path as _Path

from config import load_config, default_config
from request_helper import request_text
from nominatim_helper import nominatim_lookup

try:
	import spacy
	NLP = spacy.load("en_core_web_sm")
except Exception:
	NLP = None

from geopy.geocoders import Nominatim
from geopy.extra.rate_limiter import RateLimiter
from geopy.exc import GeocoderTimedOut, GeocoderUnavailable
import sqlite3
import re
import time as _time


# default user agent (can be overridden by CLI)
USER_AGENT = "pulse/1.0 (+https://github.com/htmlfarmer/pulse)"

def read_lines(path: Path) -> List[str]:
	if not path.exists():
		return []
	return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip() and not line.strip().startswith("#")]


def fetch_rss_items(feeds: List[str], limit_per_feed: int = 5) -> List[Dict]:
	items = []
	for url in feeds:
		try:
			# try to parse; if feedparser fails to fetch, fall back to requests
			try:
				fp = feedparser.parse(url)
			except Exception:
				text = request_text(url)
				if text:
					fp = feedparser.parse(text)
				else:
					fp = feedparser.parse('')
			for entry in fp.entries[:limit_per_feed]:
				# normalize published date if available
				pub_iso = None
				try:
					if hasattr(entry, 'published_parsed') and entry.published_parsed:
						tp = entry.published_parsed
						# feedparser sometimes uses lists, ensure tuple/struct_time
						if isinstance(tp, (list, tuple)):
							tp = tuple(tp[:9])
						try:
							pub_iso = time.strftime('%Y-%m-%dT%H:%M:%SZ', tp)
						except Exception:
							pub_iso = None
					elif entry.get('published'):
						pub_iso = entry.get('published')
				except Exception:
					pub_iso = entry.get('published') if entry.get('published') else None

				items.append({
					"source": url,
					"title": entry.get("title", ""),
					"link": entry.get("link", ""),
					"summary": entry.get("summary", ""),
					"published": pub_iso,
				})
		except Exception as e:
			logging.warning(f"Failed to parse feed {url}: {e}")
	return items


def fetch_wikipedia_summaries(titles: List[str], user_agent: Optional[str] = None) -> List[Dict]:
	out = []
	session = requests.Session()
	# polite user agent to avoid being blocked by API
	session.headers.update({"User-Agent": user_agent or USER_AGENT})
	API = "https://en.wikipedia.org/w/api.php"
	for t in titles:
		params = {
			"action": "query",
			"prop": "extracts",
			"exintro": True,
			"explaintext": True,
			"format": "json",
			"titles": t,
		}
		try:
			r = session.get(API, params=params, timeout=10)
			r.raise_for_status()
			data = r.json()
			pages = data["query"]["pages"]
			page = next(iter(pages.values()))
			out.append({
				"source": "wikipedia",
				"title": page.get("title", t),
				"link": f"https://en.wikipedia.org/wiki/{t.replace(' ', '_')}",
				"summary": page.get("extract", ""),
			})
		except Exception as e:
			logging.warning(f"Failed to fetch wiki {t}: {e}")
		time.sleep(0.5)
	return out


	def search_wikipedia_for_term(term: str, user_agent: Optional[str] = None) -> Optional[Dict]:
		"""Search Wikipedia for a term and return first match (title, link, extract) or None."""
		session = requests.Session()
		session.headers.update({"User-Agent": user_agent or USER_AGENT})
		API = "https://en.wikipedia.org/w/api.php"
		params = {
			"action": "query",
			"list": "search",
			"srsearch": term,
			"srlimit": 1,
			"format": "json",
		}
		try:
			r = session.get(API, params=params, timeout=8)
			r.raise_for_status()
			data = r.json()
			results = data.get('query', {}).get('search', [])
			if not results:
				return None
			title = results[0].get('title')
			if not title:
				return None
			# fetch extract
			ex = fetch_wikipedia_summaries([title], user_agent=user_agent)
			if ex:
				return ex[0]
		except Exception:
			return None
		return None


# small stoplist to avoid common words being treated as places
_STOPWORDS = set([w.lower() for w in [
	"and", "but", "it", "the", "a", "an", "one", "lets", "let's",
	"he", "she", "they", "his", "her", "this", "that", "is", "was",
	"in", "on", "at", "by", "for", "from", "with", "as", "of",
	"news", "update", "report",
	# days/months and generic words
	"monday","tuesday","wednesday","thursday","friday","saturday","sunday",
	"january","february","march","april","may","june","july","august","september","october","november","december",
	"several","many","most","hundreds","thousands","dozens","months","years"
]])

# candidate must include a letter and not be mostly punctuation or digits
_candidate_re = re.compile(r"^[\w\s\-\.'’()]+$")


def clean_place_name(name: str) -> str:
	if not name:
		return ""
	s = name.strip()
	# remove surrounding quotes/brackets
	s = re.sub(r'[\'"`\u2018\u2019\u201c\u201d\(\[]+|[\'"`\u2018\u2019\u201c\u201d\)\]]+$', '', s)
	# remove trailing punctuation
	s = s.rstrip('.,:;!?)\"')
	# remove possessive 's or ’s
	s = re.sub(r"\b's$|’s$", "", s)
	# collapse whitespace
	s = re.sub(r"\s+", " ", s)
	return s


def extract_place_names(text: str) -> List[str]:
	if not text:
		return []
	if NLP:
		doc = NLP(text)
		places_raw = [ent.text for ent in doc.ents if ent.label_ in ("GPE", "LOC", "FAC")]
		seen = set(); out = []
		for p in places_raw:
			p2 = clean_place_name(p)
			if not p2:
				continue
			# filter short tokens and obvious stopwords
			if len(p2) < 3 and ' ' not in p2:
				continue
			low = p2.lower()
			if low in _STOPWORDS:
				continue
			# skip short uppercase acronyms (IDF, etc.) unless multiword
			if p2.isupper() and len(p2) <= 3 and ' ' not in p2:
				continue
			if p2 not in seen:
				seen.add(p2); out.append(p2)
		return out
	else:
		# fallback: simple heuristics - capitalized words sequences of length 1-4
		words = BeautifulSoup(text, "html.parser").get_text().split()
		out = []
		i = 0
		while i < len(words):
			w = words[i]
			if w and w[0].isupper():
				j = i
				buf = [w]
				j += 1
				while j < len(words) and words[j] and words[j][0].isupper() and len(buf) < 4:
					buf.append(words[j]); j += 1
				candidate = clean_place_name(" ".join(buf))
				low = candidate.lower()
				if candidate and low not in _STOPWORDS and (len(candidate) >= 3 or ' ' in candidate) and not (candidate.isupper() and len(candidate) <= 3):
					out.append(candidate)
				i = j
			else:
				i += 1
		# dedupe
		seen = set(); res = []
		for p in out:
			if p not in seen:
				seen.add(p); res.append(p)
		return res



def _cache_db_path() -> Path:
	d = Path('.cache')
	d.mkdir(exist_ok=True)
	return d / 'geocode_cache.sqlite'


def _init_cache(conn: sqlite3.Connection):
	conn.execute('''CREATE TABLE IF NOT EXISTS geocode (place TEXT PRIMARY KEY, lat REAL, lon REAL, resolved TEXT, ts INTEGER)''')
	conn.commit()


def _get_cached(conn: sqlite3.Connection, place: str) -> Optional[Tuple[float, float, str]]:
	cur = conn.execute('SELECT lat, lon, resolved FROM geocode WHERE place = ?', (place,))
	row = cur.fetchone()
	return (row[0], row[1], row[2]) if row else None


def _set_cached(conn: sqlite3.Connection, place: str, lat: float, lon: float, resolved: str):
	conn.execute('REPLACE INTO geocode(place, lat, lon, resolved, ts) VALUES (?, ?, ?, ?, ?)', (place, lat, lon, resolved, int(_time.time())))
	conn.commit()


def geocode_places(places: List[str], user_agent: str = "news_study_app") -> Dict[str, Tuple[float, float]]:
	geolocator = Nominatim(user_agent=user_agent)
	geocode = RateLimiter(geolocator.geocode, min_delay_seconds=1, max_retries=1)
	coords: Dict[str, Tuple[float, float]] = {}

	# open cache
	dbp = _cache_db_path()
	conn = sqlite3.connect(str(dbp))
	_init_cache(conn)

	for p in places:
		p_clean = clean_place_name(p)
		if not p_clean:
			logging.debug(f"Skipping empty candidate: '{p}'")
			continue
		# basic filters
		if len(p_clean) < 3 and ' ' not in p_clean:
			logging.debug(f"Skipping too-short token: '{p_clean}'")
			continue
		if p_clean.lower() in _STOPWORDS:
			logging.debug(f"Skipping stopword candidate: '{p_clean}'")
			continue
		if p_clean.isupper() and len(p_clean) <= 3 and ' ' not in p_clean:
			logging.debug(f"Skipping short uppercase acronym: '{p_clean}'")
			continue

		# check cache
		cached = _get_cached(conn, p_clean)
		if cached:
			lat, lon, resolved = cached
			coords[p_clean] = (lat, lon)
			logging.debug(f"Cache hit: {p_clean} -> {(lat, lon)} (resolved: {resolved})")
			continue

		# try geocoding with retries/backoff
		attempts = 0
		max_attempts = 3
		backoff = 1.0
		success = False
		while attempts < max_attempts and not success:
			try:
				attempts += 1
				# give geopy a longer read timeout
				loc = geocode(p_clean, exactly_one=True, addressdetails=True, timeout=10)
				if loc:
					# check that the result looks like a real place; prefer class/type that indicate place
					raw = getattr(loc, 'raw', {})
					typ = raw.get('type') or raw.get('class') or ''
					typ = typ.lower() if isinstance(typ, str) else ''
					# accept types that are place-like
					place_like = any(k in typ for k in ('city', 'town', 'village', 'hamlet', 'suburb', 'county', 'state', 'country', 'locality', 'square', 'island', 'borough', 'neighbourhood', 'region'))
					if place_like or ',' in getattr(loc, 'address', ''):
						coords[p_clean] = (loc.latitude, loc.longitude)
						_set_cached(conn, p_clean, loc.latitude, loc.longitude, getattr(loc, 'address', p_clean))
						logging.info(f"Geocoded: {p_clean} -> {coords[p_clean]} (type={typ})")
					else:
						logging.debug(f"Rejected geocode (not place-like) for {p_clean}: type={typ} address={getattr(loc,'address', '')}")
				success = True
			except (GeocoderTimedOut, GeocoderUnavailable, requests.exceptions.RequestException) as e:
				logging.warning(f"Geocode attempt {attempts} failed for '{p_clean}': {e}")
				if attempts < max_attempts:
					_time.sleep(backoff)
					backoff *= 2
				else:
					logging.warning(f"Giving up on geocoding '{p_clean}' after {attempts} attempts")
					break
			except Exception as e:
				logging.exception(f"Geocode error for {p_clean}: {e}")
				break

	conn.close()
	return coords


def article_text_from_summary(summary: str, link: str, user_agent: Optional[str] = None) -> str:
	# try to fetch full content (best-effort); fall back to summary
	try:
		r = requests.get(link, timeout=8, headers={"User-Agent": user_agent or USER_AGENT})
		r.raise_for_status()
		soup = BeautifulSoup(r.text, "html.parser")
		# join text blocks
		paragraphs = [p.get_text().strip() for p in soup.find_all("p")]
		text = "\n\n".join([p for p in paragraphs if p])
		return text if len(text) > 200 else summary
	except Exception:
		return summary


def to_geojson(features: List[Dict]) -> Dict:
	return {"type": "FeatureCollection", "features": features}


def main():
	p = argparse.ArgumentParser()
	p.add_argument("--feeds", default="feeds.txt", help="newline list of RSS feed URLs")
	p.add_argument("--round-robin", action='store_true', help="Interleave items from feeds (one per feed, then second per feed, ...) to prioritize variety")
	p.add_argument("--sources-include", default=None, help="Optional comma-separated list of feed URLs to include (overrides feeds file)")
	p.add_argument("--wikipedia", default="wiki_topics.txt", help="newline list of Wikipedia page titles to include")
	p.add_argument("--out", default="web/data/articles.geojson", help="output geojson file")
	p.add_argument("--limit", type=int, default=5, help="items per feed")
	p.add_argument("--max-places", type=int, default=200, help="maximum unique place candidates to geocode (top frequent)")
	p.add_argument("--max-features", type=int, default=None, help="maximum total geojson features to include in output (overrides config)")
	p.add_argument("--wiki-per-item", type=int, default=None, help="number of related Wikipedia links to attach per news item")
	p.add_argument("--since-year", default=None, help="only include RSS items from this year or later (YYYY)")
	p.add_argument("--max-per-article", type=int, default=None, help="maximum features to create per news article (to avoid one article dominating)")
	p.add_argument("--user-agent", default=None, help="User-Agent header for HTTP requests")
	p.add_argument("--verbose", action="store_true", help="Enable verbose logging")
	p.add_argument("--config", default="config.yml", help="YAML config file")
	args = p.parse_args()

	# configure logging
	logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format='%(levelname)s: %(message)s')

	# load config file and merge with CLI args (CLI wins)
	cfg_path = _Path(args.config)
	cfg = default_config()
	if cfg_path.exists():
		cfg.update(load_config(cfg_path))

	# resolved user-agent for this run (CLI override > config file > default)
	user_agent = args.user_agent or cfg.get('user_agent') or USER_AGENT

	# feeds handling: support either a file or a comma-separated list passed on CLI
	if args.sources_include:
		feeds_list = [s.strip() for s in args.sources_include.split(',') if s.strip()]
	else:
		if Path(args.feeds).exists():
			feeds_list = read_lines(Path(args.feeds))
		else:
			# maybe a comma-separated string passed in --feeds
			feeds_list = [s.strip() for s in str(args.feeds).split(',') if s.strip()] if args.feeds else cfg.get('feeds', [])
	wiki_list = read_lines(Path(args.wikipedia)) if Path(args.wikipedia).exists() else cfg.get('wikipedia', [])

	limit = args.limit or cfg.get('limit', 5)
	max_places = args.max_places or cfg.get('max_places', 200)

	feeds = feeds_list
	wiki = wiki_list

	logging.info(f"Feeds: {len(feeds)} wiki topics: {len(wiki)}")

	# fetch per-feed items
	# fetch_rss_items currently returns a flattened list; we need per-feed lists to optionally do round-robin
	per_feed_items = []
	for url in feeds:
		items = []
		try:
			fp = feedparser.parse(url)
			for entry in fp.entries[:limit]:
				pub_iso = None
				try:
					if hasattr(entry, 'published_parsed') and entry.published_parsed:
						tp = entry.published_parsed
						if isinstance(tp, (list, tuple)):
							tp = tuple(tp[:9])
						try:
							pub_iso = time.strftime('%Y-%m-%dT%H:%M:%SZ', tp)
						except Exception:
							pub_iso = None
					elif entry.get('published'):
						pub_iso = entry.get('published')
				except Exception:
					pub_iso = entry.get('published') if entry.get('published') else None
				items.append({
					"source": url,
					"title": entry.get('title',''),
					"link": entry.get('link',''),
					"summary": entry.get('summary',''),
					"published": pub_iso,
				})
		except Exception:
			logging.warning(f'Failed to fetch feed {url}')
		per_feed_items.append((url, items))

	# build rss_items either flat or round-robin
	rss_items = []
	if args.round_robin:
		more = True
		idx = 0
		while more:
			more = False
			for (_, items) in per_feed_items:
				if idx < len(items):
					rss_items.append(items[idx])
					more = True
			idx += 1
	else:
		for (_, items) in per_feed_items:
			rss_items.extend(items)
	wiki_items = fetch_wikipedia_summaries(wiki, user_agent=user_agent)

	# handle since-year filtering for rss items
	since_year = None
	if args.since_year:
		try:
			since_year = int(args.since_year)
		except Exception:
			since_year = None

	# enhance content: process RSS items separately and keep wiki items as a lookup
	enhanced_news = []
	for it in rss_items:
		# if since_year is provided, skip items older than Jan 1 of that year
		if since_year:
			pub = it.get('published') or it.get('date') or None
			d = None
			try:
				d = None if not pub else (time.strptime(pub, '%Y-%m-%dT%H:%M:%SZ') if 'T' in pub else None)
			except Exception:
				d = None
			if d:
				if d.tm_year < since_year:
					continue
		text = article_text_from_summary(it.get("summary", ""), it.get("link", ""), user_agent=user_agent)
		it["text"] = text
		it["places"] = extract_place_names(text)
		enhanced_news.append(it)

	# prepare wiki lookup (title and summary lowercased)
	wiki_lookup = []
	for w in wiki_items:
		wiki_lookup.append({
			"title": w.get("title"),
			"link": w.get("link"),
			"summary": w.get("summary", ""),
			"text_lc": (w.get("title", "") + " " + (w.get("summary") or "")).lower()
		})

	# collect unique places
	# count frequencies and select top candidates to avoid excessive geocoding
	counter = Counter()
	for it in enhanced_news:
		for p in it["places"]:
			counter[p] += 1
	uniques = [p for p, _ in counter.most_common(args.max_places)]

	logging.info(f"Selected {len(uniques)} unique place candidates (top {max_places})")
	coords = geocode_places(uniques, user_agent=user_agent)

	features = []
	# Build features from news items; attach related wiki links per news feature
	wiki_per_item = args.wiki_per_item if args.wiki_per_item is not None else cfg.get('wiki_per_item', 1)
	try:
		wiki_per_item = int(wiki_per_item)
	except Exception:
		wiki_per_item = 1
	# enforce at least 1 wiki link per article as requested
	if wiki_per_item < 1:
		wiki_per_item = 1
	max_per_article = args.max_per_article if args.max_per_article is not None else cfg.get('max_per_article', 3)

	# For each article, build a list of candidate place coords
	per_article_places = []  # list of tuples (it, [(place, lat, lon), ...])
	for it in enhanced_news:
		places_for_it = []
		for p in it.get("places", []):
			if p in coords:
				lat, lon = coords[p]
				places_for_it.append((p, lat, lon))
		if places_for_it:
			per_article_places.append((it, places_for_it))

	# round-robin over articles, picking up to max_per_article places per article
	more = True
	article_idx = 0
	per_article_counts = [0] * len(per_article_places)
	while more:
		more = False
		for ai, (it, places_list) in enumerate(per_article_places):
			if per_article_counts[ai] >= max_per_article:
				continue
			if per_article_counts[ai] >= len(places_list):
				continue
			p, lat, lon = places_list[per_article_counts[ai]]
			# find matching wiki pages
			matches = []
			pl = p.lower()
			for w in wiki_lookup:
				if pl in (w.get('title') or '').lower() or pl in w.get('text_lc', ''):
					matches.append({"title": w.get('title'), "link": w.get('link'), "summary": (w.get('summary') or '')[:300]})
					if len(matches) >= wiki_per_item:
						break
			# if no wiki matches, try to find a related wikipedia page by searching for prominent terms from the article text
			if not matches:
				try:
					# pick candidate terms: top nouns/words from the article text
					text = (it.get('text') or it.get('summary') or '')
					# basic tokenization and counting
					tokens = [w.strip(".,:;()[]\"'`)\n\r").lower() for w in re.findall(r"\w+", text) if len(w) > 3]
					freq = Counter(tokens)
					common = [t for t,_ in freq.most_common(8)]
					for term in common:
						res = search_wikipedia_for_term(term, user_agent=user_agent)
						if res:
							matches.append({"title": res.get('title'), "link": res.get('link'), "summary": (res.get('summary') or '')[:300]})
							break
				except Exception:
					pass
			features.append({
				"type": "Feature",
				"properties": {
					"title": it.get("title"),
					"news_link": it.get("link"),
					"news_source": it.get("source"),
					"place": p,
					"summary": (it.get("summary") or "")[:400],
					"published": it.get("published"),
					"wiki_matches": matches,
				},
				"geometry": {"type": "Point", "coordinates": [lon, lat]},
			})
			logging.info(f"Feature added: title={it.get('title')!r} source={it.get('source')!r} link={it.get('link')!r} place={p!r} coords=({lat},{lon})")
			per_article_counts[ai] += 1
			more = True
			# break early if we've reached a global max_features (if set)
			if (args.max_features is not None) or (cfg.get('max_features') is not None):
				try:
					if args.max_features is not None:
						global_max = int(args.max_features)
					else:
						global_max = int(cfg.get('max_features'))
				except Exception:
					global_max = None
				if global_max and len(features) >= global_max:
					more = False
					break

	# enforce maximum features cap
	max_features = args.max_features or cfg.get('max_features')
	if max_features:
		features = features[:int(max_features)]

	out_path = Path(args.out)
	out_path.parent.mkdir(parents=True, exist_ok=True)
	geo = to_geojson(features)
	out_path.write_text(json.dumps(geo, ensure_ascii=False, indent=2), encoding="utf-8")
	logging.info(f"Wrote {len(features)} features to {out_path}")


if __name__ == "__main__":
	main()

