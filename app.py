from flask import Flask, request, jsonify, send_file, Response, abort
from flask_cors import CORS
import subprocess, os, requests, json, csv
from pathlib import Path
from datetime import datetime, timedelta

BASE_DIR = Path(__file__).resolve().parent

app = Flask(__name__, static_folder=str(BASE_DIR / 'static'), static_url_path='')
CORS(app)


def safe_json_load(text):
    try:
        return json.loads(text)
    except Exception:
        return None


@app.route('/')
def index():
    return app.send_static_file('index.html')


@app.route('/api/gibs_date')
def gibs_date():
    y = (datetime.utcnow() - timedelta(days=1)).strftime('%Y-%m-%d')
    return jsonify({'date': y})


@app.route('/api/run_pulse_py')
def run_pulse_py():
    try:
        res = subprocess.run(['python3', 'pulse.py'], cwd=str(BASE_DIR), capture_output=True, text=True, timeout=300)
        out = res.stdout + res.stderr
        if res.returncode != 0:
            return jsonify({'status': 'error', 'output': out}), 500
        return jsonify({'status': 'success', 'output': out})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/proxy_gibs')
def proxy_gibs():
    date = request.args.get('date', '')
    z = request.args.get('z', '0')
    y = request.args.get('y', '0')
    x = request.args.get('x', '0')
    if not date or not len(date) == 10:
        return 'Invalid date', 400
    url = f"https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/MODIS_Terra_CorrectedReflectance_TrueColor/default/{date}/GoogleMapsCompatible_Level9/{z}/{y}/{x}.jpg"
    try:
        r = requests.get(url, stream=True, timeout=10, headers={'User-Agent': 'Pulse/1.0'})
        return Response(r.raw.read(), content_type='image/jpeg')
    except Exception as e:
        return ('', 502)


@app.route('/api/geo_lookup')
def geo_lookup():
    lat = request.args.get('lat')
    lon = request.args.get('lon')
    if not lat or not lon:
        return jsonify({'titles': []})
    try:
        radius = 10000
        url = f"https://en.wikipedia.org/w/api.php?action=query&list=geosearch&gscoord={lat}%7C{lon}&gsradius={radius}&gslimit=5&format=json"
        r = requests.get(url, headers={'User-Agent': 'Pulse/1.0'}, timeout=5)
        j = safe_json_load(r.text) or {}
        titles = [i.get('title') for i in (j.get('query', {}).get('geosearch', []) or [])]
        return jsonify({'titles': titles})
    except Exception:
        return jsonify({'titles': []})


@app.route('/api/wikidata_lookup')
def wikidata_lookup():
    lat = request.args.get('lat')
    lon = request.args.get('lon')
    if not lat or not lon:
        return jsonify({'error': 'missing coords'})
    try:
        radius = 10000
        gs = f"https://www.wikidata.org/w/api.php?action=query&list=geosearch&gscoord={lat}%7C{lon}&gsradius={radius}&gslimit=1&format=json"
        r1 = requests.get(gs, headers={'User-Agent': 'Pulse/1.0'}, timeout=5)
        j1 = safe_json_load(r1.text) or {}
        qid = None
        items = j1.get('query', {}).get('geosearch', [])
        if items:
            qid = items[0].get('title')
        if not qid:
            return jsonify({'result': None})
        ent = f"https://www.wikidata.org/w/api.php?action=wbgetentities&ids={qid}&format=json&props=descriptions|claims&languages=en"
        r2 = requests.get(ent, headers={'User-Agent': 'Pulse/1.0'}, timeout=5)
        return Response(r2.content, content_type='application/json')
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/current_events')
def current_events():
    p = BASE_DIR / 'data' / 'current_events.geojson'
    if not p.exists():
        return jsonify({'type':'FeatureCollection','features':[]})
    return send_file(str(p), mimetype='application/json')


@app.route('/api/current_events_status')
def current_events_status():
    running = (BASE_DIR / 'data' / 'current_events.running').exists()
    return jsonify({'running': running})


@app.route('/api/earthquakes')
def earthquakes():
    try:
        r = requests.get('https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_month.geojson', timeout=5)
        return Response(r.content, content_type='application/json')
    except Exception:
        return jsonify({'features': []})


@app.route('/api/cities')
def cities():
    if request.args.get('cities') == 'all':
        out = []
        csvp = BASE_DIR / 'cities.csv'
        if csvp.exists():
            with open(csvp, newline='', encoding='utf-8') as fh:
                reader = csv.DictReader(fh)
                for row in reader:
                    try:
                        out.append({'city': row.get('city'), 'lat': float(row.get('lat') or 0), 'lng': float(row.get('lng') or 0), 'population': int(row.get('population') or 0)})
                    except Exception:
                        continue
        return jsonify(out)
    return jsonify([])


@app.route('/api/news_for_city')
def news_for_city():
    city = request.args.get('city') or request.args.get('news_for_city')
    if not city:
        return jsonify([])
    url = f"https://news.google.com/rss/search?q={requests.utils.quote(city)}&hl=en-US&gl=US&ceid=US:en"
    try:
        r = requests.get(url, timeout=5, headers={'User-Agent': 'Pulse/1.0'})
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(r.content, 'xml')
        items = []
        for item in soup.find_all('item')[:20]:
            title = item.title.string if item.title else ''
            link = item.link.string if item.link else ''
            pubDate = item.pubDate.string if item.pubDate else ''
            source = item.source.string if item.source else ''
            desc = item.description.string if item.description else ''
            img = ''
            if desc:
                m = BeautifulSoup(desc, 'html.parser').find('img')
                if m and m.get('src'):
                    img = m.get('src')
            items.append({'title': title, 'link': link, 'pubDate': pubDate, 'source': source, 'image': img})
        return jsonify(items)
    except Exception:
        return jsonify([])


@app.route('/api/reddit_search')
def reddit_search():
    q = request.args.get('reddit_search') or request.args.get('q')
    limit = int(request.args.get('limit') or 20)
    if not q:
        return jsonify([])
    url = f"https://www.reddit.com/search.json?q={requests.utils.quote(q)}&sort=new&limit={limit}"
    try:
        r = requests.get(url, headers={'User-Agent': 'Pulse/1.0'}, timeout=5)
        j = safe_json_load(r.text) or {}
        items = []
        for c in j.get('data', {}).get('children', []):
            p = c.get('data', {})
            items.append({'title': p.get('title',''), 'subreddit': p.get('subreddit',''), 'url': ('https://reddit.com' + p.get('permalink')) if p.get('permalink') else (p.get('url') or ''), 'created_utc': int(p.get('created_utc') or 0), 'score': int(p.get('score') or 0)})
        return jsonify(items)
    except Exception:
        return jsonify([])


@app.route('/api/find_cities')
def find_cities():
    lat = request.args.get('lat')
    lon = request.args.get('lon')
    radius = request.args.get('radius')
    if not lat or not lon or not radius:
        return ('', 400)
    try:
        cmd = ['python3', 'find_cities.py', '--lat', lat, '--lon', lon, '--radius', radius]
        res = subprocess.run(cmd, cwd=str(BASE_DIR), capture_output=True, text=True, timeout=30)
        return Response(res.stdout, content_type='application/json')
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/ask_llm', methods=['POST','GET'])
def ask_llm():
    # Forward to local LLM server
    body = None
    if request.method == 'POST':
        body = request.get_data()
    else:
        prompt = request.args.get('prompt')
        if not prompt:
            return jsonify({'error':'missing prompt'}), 400
        payload = {'prompt': prompt}
        body = json.dumps(payload)
    try:
        h = {'Content-Type':'application/json'}
        r = requests.post('http://127.0.0.1:5005/ask', data=body, headers=h, timeout=60)
        return Response(r.content, content_type='application/json', status=r.status_code)
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@app.route('/api/stop_llm')
def stop_llm():
    pid_file = BASE_DIR / '.llm_pid'
    if not pid_file.exists():
        return jsonify({'status':'none','message':'No running LLM process found.'})
    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, 9)
    except Exception as e:
        try:
            pid_file.unlink()
        except Exception:
            pass
        return jsonify({'status':'failed','message': str(e)})
    try:
        pid_file.unlink()
    except Exception:
        pass
    return jsonify({'status':'killed','pid': pid})


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=8000, debug=True)
