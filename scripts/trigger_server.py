"""Small Flask server that accepts a POST to trigger a background run of pulse.
This keeps the current GeoJSON until the background job finishes writing a new file.

Usage: run this under a process manager (systemd) or in the background. It listens on localhost only.
"""
from flask import Flask, request, jsonify
import subprocess
import shlex
import os
from threading import Thread
import sys

app = Flask(__name__)

# secret token - MUST be set via env var TRIGGER_SECRET
# intentionally do NOT provide a default here; fail fast when starting the server
SECRET = os.environ.get('TRIGGER_SECRET')
PULSE_SCRIPT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'scripts', 'run_and_deploy.sh'))
VENV = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '.venv'))


def run_bg(cmd_list):
    # run in background detached without shell to avoid injection
    try:
        subprocess.Popen(cmd_list)
    except Exception as e:
        app.logger.exception('failed to start background job: %s', e)


@app.after_request
def add_cors_headers(response):
    # allow local browser requests (map page) to call this trigger endpoint
    origin = request.headers.get('Origin')
    # only allow CORS for obvious localhost origins (don't expose this endpoint to remote origins)
    if origin and (origin.startswith('http://localhost') or origin.startswith('http://127.0.0.1') or origin.startswith('https://localhost')):
        response.headers['Access-Control-Allow-Origin'] = origin
    # always allow trigger token header if present in requests from allowed origins
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, X-Trigger-Token'
    response.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
    return response


@app.route('/trigger', methods=['POST'])
def trigger():
    token = request.headers.get('X-Trigger-Token') or request.args.get('token')
    if not SECRET:
        return jsonify({'ok': False, 'error': 'server-misconfigured'}), 500
    if not token or token != SECRET:
        return jsonify({'ok': False, 'error': 'unauthorized'}), 401

    # optional params: limit, max_places, max_features, wiki_per_item, since_year, round_robin, sources
    params = request.json or {}
    limit = int(params.get('limit', 5))
    max_places = int(params.get('max_places', 200))
    max_features = params.get('max_features')
    wiki_per_item = params.get('wiki_per_item')
    since_year = params.get('since_year')
    round_robin = bool(params.get('round_robin'))
    sources = params.get('sources')

    # build argument list (avoid shell=True)
    cmd_list = [PULSE_SCRIPT, '--venv', VENV, '--limit', str(limit), '--max-places', str(max_places)]
    if max_features:
        cmd_list += ['--max-features', str(int(max_features))]
    if wiki_per_item is not None:
        cmd_list += ['--wiki-per-item', str(int(wiki_per_item))]
    if since_year and since_year != 'all':
        cmd_list += ['--since-year', str(since_year)]
    if round_robin:
        cmd_list.append('--round-robin')
    if sources:
        # sanitize sources: allow only letters, numbers, comma, dash, underscore
        if isinstance(sources, list):
            srcs = ','.join([str(s) for s in sources])
        else:
            srcs = str(sources)
        # reject suspicious input
        import re
        if re.search(r"[^a-zA-Z0-9,._-]", srcs):
            return jsonify({'ok': False, 'error': 'invalid sources'}), 400
        cmd_list += ['--sources-include', srcs]

    # run async
    Thread(target=run_bg, args=(cmd_list,)).start()

    return jsonify({'ok': True, 'message': 'triggered'}), 202


@app.route('/trigger', methods=['OPTIONS'])
def trigger_options():
    # handle preflight
    return ('', 204)


@app.route('/log', methods=['GET'])
def tail_log():
    # return last N lines of pulse.log (safe tail) -- optional ?lines=N
    lines = int(request.args.get('lines', 200))
    log_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'web', 'data', 'pulse.log'))
    if not os.path.exists(log_path):
        return jsonify({'ok': False, 'error': 'no log'}), 404
    try:
        with open(log_path, 'r', encoding='utf-8', errors='ignore') as fh:
            all_lines = fh.read().splitlines()
            out_lines = all_lines[-lines:]
            out = '\n'.join(out_lines)
            # redact the secret if present
            try:
                if SECRET:
                    out = out.replace(SECRET, '[REDACTED]')
            except Exception:
                pass
        return jsonify({'ok': True, 'log': out})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/feeds', methods=['GET'])
def feeds_list():
    # return feeds.txt canonical list if present
    feeds_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'feeds.txt'))
    if not os.path.exists(feeds_path):
        return jsonify({'ok': False, 'feeds': []})
    try:
        with open(feeds_path, 'r', encoding='utf-8') as fh:
            items = [l.strip() for l in fh.read().splitlines() if l.strip() and not l.strip().startswith('#')]
        return jsonify({'ok': True, 'feeds': items})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


if __name__ == '__main__':
    if not SECRET:
        print('ERROR: TRIGGER_SECRET environment variable is not set. Exiting.', file=sys.stderr)
        sys.exit(2)
    app.run(host='127.0.0.1', port=5050)
