Deployment notes — serving pulse GeoJSON via Apache/WordPress

Goal: periodically run `pulse.py` (cron, systemd timer) and make the generated `articles.geojson` available under your existing Apache/WordPress site so you can open `maps.html` and load the GeoJSON.

Options

1) Serve directly from the repo (simple, quick)
   - Copy `web/data/articles.geojson` into a web-accessible directory under Apache, for example `/var/www/html/pulse/data/articles.geojson`.
   - Use the script `scripts/run_and_deploy.sh --deploy /var/www/html/pulse/data/articles.geojson` to run pulse and copy the file.

2) Symlink or alias directory
   - Create a directory under your WordPress/Apache root (for example `/var/www/html/pulse`) and symlink the `web/data` folder from the repo:
     sudo ln -s /home/asher/github/pulse/web /var/www/html/pulse
   - Ensure Apache follows symlinks (AllowOverride / FollowSymLinks) or create an Alias in your Apache site conf.

3) Use a WordPress-friendly location
   - WordPress often disallows writes to plugin directories; prefer `/var/www/html/pulse/data/` outside WP content folders. Then use a small HTML page or iframe to point to the map.

Scheduling options

- Cron (simple):
  Edit `crontab -e` for the user that can run the repo and add:
    # run pulse every 15 minutes
    */15 * * * * /home/asher/github/pulse/scripts/run_and_deploy.sh --venv /home/asher/github/pulse/.venv --deploy /var/www/html/pulse/data/articles.geojson --limit 5 --max-places 100

- systemd timer (recommended):
  Create a systemd service and timer unit (example below). The service runs the deploy script; the timer schedules it.

  /etc/systemd/system/pulse-run.service
  -------------------------------------
  [Unit]
  Description=Run pulse and deploy GeoJSON

  [Service]
  Type=oneshot
  ExecStart=/home/asher/github/pulse/scripts/run_and_deploy.sh --venv /home/asher/github/pulse/.venv --deploy /var/www/html/pulse/data/articles.geojson --limit 5 --max-places 100

  /etc/systemd/system/pulse-run.timer
  -----------------------------------
  [Unit]
  Description=Run pulse every 15 minutes

  [Timer]
  OnBootSec=5min
  OnUnitActiveSec=15min

  [Install]
  WantedBy=timers.target

  Then enable and start:
    sudo systemctl enable --now pulse-run.timer

Security and permissions
- The deploy script will attempt to chown the deployed file to `www-data` if run as root. Prefer running the cron or systemd unit as a user that has write access to the target directory or configure sudoers to allow the copy/chown command.

WordPress integration ideas
- Add a small WP page template or a custom plugin that includes a Leaflet map and fetches `/pulse/data/articles.geojson`.
- If your WordPress is hosted in the same server, you can simply include a script tag that loads the generated GeoJSON URL.

Troubleshooting
- If Apache returns 403 for the deployed file, check file ownership and Apache config (Options +FollowSymLinks and AllowOverride).
- If the GeoJSON is stale, check the cron/systemd logs for failures.


Trigger server (optional)
-------------------------
If you want the map page to request background regenerations when a user changes the date range, run the small trigger server included in `scripts/trigger_server.py`:

  export TRIGGER_SECRET="your-secret"
  python3 scripts/trigger_server.py

Important security notes:
- The trigger server is intentionally a minimal helper and must only be run bound to localhost (127.0.0.1). Do not expose it on a public interface. The default example binds to 127.0.0.1:5050.
- You MUST set a strong random `TRIGGER_SECRET` value in the environment before starting the server. The server will refuse to start without this value.
- Do not embed secrets in client-side JavaScript or commit a `.env` file containing secrets to git. Use `.env` locally and add it to `.gitignore` (an example `.env.example` is included).

By default it listens on 127.0.0.1:5050 and expects the header `X-Trigger-Token` to match the `TRIGGER_SECRET`. The map page can POST to http://127.0.0.1:5050/trigger when the date range changes and the UI will poll `/log` to detect completion.

For production, run the trigger server under systemd or another process manager and set `TRIGGER_SECRET` in the service environment. See `scripts/pulse.service` for examples.

Generating and rotating secrets
--------------------------------
- Use the included helper to create a strong secret: `scripts/generate_secret.sh`.
- Copy the secret into a local `.env` file (example in `.env.example`) or set it in your systemd unit environment.
- If you accidentally commit a secret into git, rotate the secret immediately and consider purging it from history (tools: `git filter-repo` or `git filter-branch`). Review GitHub docs on removing sensitive data.

