Notes / ideas previously collected in /home/asher/github/watch

I couldn't access the /home/asher/github/watch directory from this environment (outside the workspace). Below are useful ideas and practical additions that typically live in a companion repo called `watch` and that will help `pulse.py` be more robust and reusable. You can copy these into `/home/asher/github/watch` or merge them into this project.

Suggested features to add (quick checklist):

- structured config (YAML/JSON) for feeds, wiki topics, user-agent and rate limits
- modular fetchers: separate RSS fetch, HTML fetch, and content extractor with pluggable extractors (readability/boilerpipe)
- caching improvements: use sqlite for HTTP response caching (ETag/Last-Modified) and better geocode cache invalidation
- more NER options: spaCy models, fallback heuristics, and a small custom blacklist/whitelist for place names
- a small web UI or simple Flask app to preview generated GeoJSON
- unit tests for text extraction, place candidate filtering, and geocoding cache behavior
- CI workflow (GitHub Actions) to run linting, tests, and optionally a smoke-run that writes a small sample GeoJSON

Quick migration notes:

- copy any feed lists and wiki topic files into this repo (`feeds.txt`, `wiki_topics.txt`) and update paths in README
- if there are helper scripts (e.g. `watch/fetchers.py`), consider integrating them as modules and adding tests
- remember to keep polite user-agent strings and respect robots.txt and site rate limits

If you want, I can:
- try to open the watch repo if you add it to the workspace
- scaffold a small config file loader and a Flask preview UI here
- add tests and a GitHub Actions workflow

