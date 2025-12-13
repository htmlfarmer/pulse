# pulse
PULSE

geo location for news...

How to run and test (quick)

Option A — Apache Alias to the repo (recommended for development)
- If your Apache config maps http://localhost/pulse to /home/asher/github/pulse (Alias), then run from the repo root:
    cd /home/asher/github/pulse
    python3 pulse.py --feeds feeds.txt --wikipedia wiki_topics.txt --out web/data/articles.geojson --limit 5 --verbose

- Then open in the browser:
    http://localhost/pulse/index.html

Option B — Apache serves /var/www/html/pulse (deploy to docroot)
- Create target dirs and allow your user to write, then run the script to output directly into docroot:
    sudo mkdir -p /var/www/html/pulse/data
    sudo chown $(whoami):$(whoami) /var/www/html/pulse/data
    python3 pulse.py --feeds feeds.txt --wikipedia wiki_topics.txt --out /var/www/html/pulse/data/articles.geojson --limit 5 --verbose

- After generation, set permissions for Apache:
    sudo chown -R www-data:www-data /var/www/html/pulse
    sudo chmod -R 755 /var/www/html/pulse

- Open:
    http://localhost/pulse/index.html

Add weather overlay (OpenWeatherMap)
- Sign up for a free API key at https://openweathermap.org/api (Current Weather Data)
- Export as env var or pass on CLI:
    export OWM_API_KEY=your_key_here
    python3 pulse.py --feeds feeds.txt --wikipedia wiki_topics.txt --out web/data/articles.geojson --limit 5 --include-weather --owm-key $OWM_API_KEY --verbose

Or rely on env:
    python3 pulse.py --include-weather --out web/data/articles.geojson --limit 5

What to expect
- The map will show news markers and distinct circle markers for weather (orange).
- News/news.json/news.html will also include weather entries (category: weather).
- If you see fewer news items, try increasing --limit or lowering filters (--max-places / --max_per_article) or run with --verbose to inspect geocoding skips.

Quick troubleshooting
- Verify file reachable via HTTP:
    curl -I "http://localhost/pulse/data/articles.geojson"
    curl -v "http://localhost/pulse/data/news.json"

- If you see network errors in the browser:
  - Confirm the URL the page requests (open DevTools → Network).
  - If request gives 404/403, fix path/permissions.
  - If content is non-JSON, open the raw URL in a new tab to inspect.

- Use the script's verbose logs to see progress:
    python3 pulse.py ... --verbose

Notes
- If Apache is aliased to the repo you do NOT need to copy files — simply write into web/data/ inside the repo and Apache will serve them.
- If images don't appear, open the image URL directly in the browser to confirm reachability / hotlinking restrictions.
- If you want the script to copy or set ownership into /var/www/html automatically, tell me and I will add a --deploy-path / --set-owner option.

Where to specify news sources

- Default file: the script expects a newline-separated feeds file (default: feeds.txt). Each non-empty line is treated as an RSS/atom feed URL. Lines beginning with `#` are ignored as comments.

  Example feeds.txt:
  ```
  # filepath: /home/asher/github/pulse/feeds.txt
  https://example.com/rss
  https://news.example.org/feed
  ```

- CLI override: pass a comma-separated list of feed URLs with --feeds or use --sources-include to explicitly include a comma-separated set of sources:
  - Use a file (default behavior):
    python3 pulse.py --feeds feeds.txt ...
  - Use a comma list:
    python3 pulse.py --feeds "https://a.example/rss,https://b.example/feed" ...

- Note about sites.txt: you currently have a sites.txt in the repo (used for other scraping tasks). pulse.py uses RSS/Atom feeds (feeds.txt) and the --feeds/--sources-include options. If you want pulse.py to use sites.txt instead, either rename that file to feeds.txt or pass it explicitly:
    python3 pulse.py --feeds sites.txt ...

- Quick tips:
  - Keep one URL per line, remove trailing whitespace.
  - Use --verbose when running to see which feeds were fetched and any parsing warnings.
  - If a feed doesn't return many items, increase --limit or add more feeds.