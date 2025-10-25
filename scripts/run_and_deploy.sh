#!/usr/bin/env bash
# Run pulse.py inside the repo venv and deploy the generated GeoJSON to a web-accessible path.
# Usage:
#   ./scripts/run_and_deploy.sh --venv .venv --out web/data/articles.geojson --deploy /var/www/html/pulse/data/articles.geojson
# Environment:
#   If you don't pass --venv, it defaults to .venv
#   If you don't pass --deploy, it will print the path to the generated file but not copy it.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PULSE_PY="$REPO_ROOT/pulse.py"
VENV="$REPO_ROOT/.venv"
OUT_PATH="$REPO_ROOT/web/data/articles.geojson"
DEPLOY_PATH=""
LIMIT=5
MAX_PLACES=200
USER_AGENT="pulse/1.0 (+https://github.com/htmlfarmer/pulse)"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --venv)
      VENV="$2"; shift 2;;
    --pulse)
      PULSE_PY="$2"; shift 2;;
    --out)
      OUT_PATH="$2"; shift 2;;
    --deploy)
      DEPLOY_PATH="$2"; shift 2;;
    --symlink-web)
      SYMLINK_WEB="$2"; shift 2;;
    --limit)
      LIMIT="$2"; shift 2;;
    --max-places)
      MAX_PLACES="$2"; shift 2;;
    --user-agent)
      USER_AGENT="$2"; shift 2;;
    -h|--help)
      sed -n '1,120p' "$0"; exit 0;;
    *)
      echo "Unknown arg: $1" >&2; exit 1;;
  esac
done

PY="$VENV/bin/python"
if [[ ! -x "$PY" ]]; then
  echo "Python not found in venv: $VENV" >&2
  echo "If you haven't created the venv, create it with: python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt" >&2
  exit 2
fi

LOG_PATH="$REPO_ROOT/web/data/pulse.log"
mkdir -p "$(dirname "$LOG_PATH")"

echo "Running pulse (limit=$LIMIT max_places=$MAX_PLACES) with user-agent: $USER_AGENT"
# write a header to the log then stream python output into it so the UI can tail it
echo "=== pulse run start: $(date -u) pid=$$ ===" > "$LOG_PATH"
echo "Command: $PY $PULSE_PY --limit $LIMIT --max-places $MAX_PLACES --user-agent $USER_AGENT" >> "$LOG_PATH"

# run pulse and append stdout+stderr to the log while also showing it on stdout
"$PY" "$PULSE_PY" --limit "$LIMIT" --max-places "$MAX_PLACES" --user-agent "$USER_AGENT" 2>&1 | tee -a "$LOG_PATH"
echo "=== pulse run end: $(date -u) ===" >> "$LOG_PATH"

echo "Pulse finished. Output file: $OUT_PATH"

if [[ -n "$DEPLOY_PATH" ]]; then
  echo "Deploying to $DEPLOY_PATH"
  mkdir -p "$(dirname "$DEPLOY_PATH")"
  cp -f "$OUT_PATH" "$DEPLOY_PATH"
  echo "Copied to $DEPLOY_PATH"
  # try to set ownership to www-data if available (may require sudo)
  if id -u www-data >/dev/null 2>&1; then
    if [[ $(id -u) -eq 0 ]]; then
      chown www-data:www-data "$DEPLOY_PATH" || true
    else
      echo "Attempting sudo chown to www-data:www-data (may prompt)"
      sudo chown www-data:www-data "$DEPLOY_PATH" || true
    fi
  fi
  echo "Deployed. URL (example): http://your-server/$(basename "$DEPLOY_PATH")"
else
  echo "No deploy path provided; file left in repo: $OUT_PATH"
fi

if [[ -n "${SYMLINK_WEB:-}" ]]; then
  echo "Creating symlink for web folder to: $SYMLINK_WEB"
  # remove existing folder if it's a symlink
  if [[ -L "$SYMLINK_WEB" ]]; then
    rm -f "$SYMLINK_WEB"
  fi
  # if a real directory exists, back it up
  if [[ -d "$SYMLINK_WEB" && ! -L "$SYMLINK_WEB" ]]; then
    echo "Backing up existing directory to ${SYMLINK_WEB}.bak"
    mv "$SYMLINK_WEB" "${SYMLINK_WEB}.bak"
  fi
  mkdir -p "$(dirname "$SYMLINK_WEB")"
  ln -sfn "$REPO_ROOT/web" "$SYMLINK_WEB"
  echo "Symlinked $REPO_ROOT/web -> $SYMLINK_WEB"
fi

exit 0
