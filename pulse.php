<?php
// Simple request logger for debugging; writes to system temp folder.
function pulse_log($tag, $message = '') {
  $logfile = sys_get_temp_dir() . '/pulse_requests.log';
  $ip = $_SERVER['REMOTE_ADDR'] ?? 'cli';
  $time = gmdate('Y-m-d H:i:s');
  $php = defined('PHP_BINARY') ? PHP_BINARY : 'php';
  $ver = phpversion();
  $uri = $_SERVER['REQUEST_URI'] ?? '';
  $method = $_SERVER['REQUEST_METHOD'] ?? 'CLI';
  // keep message to a reasonable length
  $m = is_string($message) ? str_replace("\n", ' ', $message) : json_encode($message);
  $m = mb_substr($m, 0, 2000);
  $entry = "[$time] [$tag] ip={$ip} php={$php} ver={$ver} method={$method} uri={$uri} msg={$m}" . PHP_EOL;
  @file_put_contents($logfile, $entry, FILE_APPEND | LOCK_EX);
}
    if (isset($_GET['run_pulse_py'])) {
      header('Content-Type: application/json');

      // Remove old geojson before running the script (preserve original intent)
      $geojson_file = __DIR__ . '/data/current_events.geojson';
      if (file_exists($geojson_file)) {
        @unlink($geojson_file);
      }

      // First try to call the local Python API (app.py) to run the job synchronously
      $api_url = 'http://127.0.0.1:8000/api/run_pulse_py';
      $ctx = stream_context_create(['http' => ['timeout' => 5, 'header' => "User-Agent: PulsePHP/1.0\r\n"]]);
      $resp = @file_get_contents($api_url, false, $ctx);
      if ($resp !== false) {
        // Assume the API returned JSON already
        echo $resp;
        exit;
      }

      // If the API is unavailable, start the Python script as a non-blocking background job.
      // Use nohup & to detach; echo the PID so we can report status.
      $python = 'python3';
      $pulse_py = escapeshellarg(__DIR__ . '/pulse.py');
      $logfile = escapeshellarg(sys_get_temp_dir() . '/pulse_background.log');
      $cmd = "cd " . escapeshellarg(__DIR__) . " && nohup {$python} {$pulse_py} > {$logfile} 2>&1 & echo $!";
      $pid = null;
      try {
        $pid = trim(shell_exec($cmd));
      } catch (Exception $e) {
        $pid = null;
      }

      if ($pid && is_numeric($pid)) {
        // create a marker file indicating processing is underway
        @file_put_contents(__DIR__ . '/data/current_events.running', (string)time());
        echo json_encode(['status' => 'started', 'pid' => intval($pid)]);
        exit;
      } else {
        http_response_code(500);
        echo json_encode(['status' => 'error', 'message' => 'Failed to start background process.']);
        exit;
      }
    }

  if (isset($_GET['proxy_gibs'])) {
    $date = isset($_GET['date']) ? $_GET['date'] : '';
    $z = isset($_GET['z']) ? intval($_GET['z']) : 0;
    $y = isset($_GET['y']) ? intval($_GET['y']) : 0;
    $x = isset($_GET['x']) ? intval($_GET['x']) : 0;

    if (!preg_match('/^\d{4}-\d{2}-\d{2}$/', $date)) {
        http_response_code(400);
        exit('Invalid date format');
    }

    $url = "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/MODIS_Terra_CorrectedReflectance_TrueColor/default/{$date}/GoogleMapsCompatible_Level9/{$z}/{$y}/{$x}.jpg";

    $ch = curl_init();

    curl_setopt($ch, CURLOPT_URL, $url);
    curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
    curl_setopt($ch, CURLOPT_HEADER, false);
    curl_setopt($ch, CURLOPT_FOLLOWLOCATION, true);
    curl_setopt($ch, CURLOPT_USERAGENT, 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36');
    curl_setopt($ch, CURLOPT_SSL_VERIFYPEER, false);
    curl_setopt($ch, CURLOPT_SSL_VERIFYHOST, false);

    $image_data = curl_exec($ch);
    $http_code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    
    curl_close($ch);
    
    if ($http_code == 200 && $image_data) {
        header('Content-Type: image/jpeg');
        echo $image_data;
    } else {
        http_response_code($http_code > 0 ? $http_code : 500);
    }
    exit;
  }

  if (isset($_GET['geo_lookup']) && isset($_GET['lat']) && isset($_GET['lon'])) {
    header('Content-Type: application/json');
    $lat = floatval($_GET['lat']);
    $lon = floatval($_GET['lon']);
    
    // Using Wikipedia's geosearch API
    $radius = 10000; // 10km search radius
    $url = "https://en.wikipedia.org/w/api.php?action=query&list=geosearch&gscoord={$lat}|{$lon}&gsradius={$radius}&gslimit=5&format=json";

    // Wikipedia API requires a User-Agent header.
    $opts = [ "http" => [ "header" => "User-Agent: Pulse/1.0 (pulse.app; contact@example.com)\r\n" ] ];
    $context = stream_context_create($opts);
    
    $response_json = @file_get_contents($url, false, $context);
    if ($response_json === FALSE) {
        echo json_encode(['titles' => []]);
        exit;
    }
    
    $response_data = json_decode($response_json, true);
    
    $titles = [];
    if (isset($response_data['query']['geosearch'])) {
        foreach($response_data['query']['geosearch'] as $item) {
            $titles[] = $item['title'];
        }
    }
    echo json_encode(['titles' => $titles]);
    exit;
  }

  if (isset($_GET['live_news'])) {
    header('Content-Type: application/json');
    $since = isset($_GET['since']) ? intval($_GET['since']) : 0;

    $live_news_file = 'data/live_news.json';

    if (!file_exists($live_news_file)) {
        echo json_encode(['type' => 'FeatureCollection', 'features' => [], 'latest' => $since]);
        exit;
    }

    $live_data_json = file_get_contents($live_news_file);
    $live_data = json_decode($live_data_json, true);

    if (!$live_data || !isset($live_data['features'])) {
        echo json_encode(['type' => 'FeatureCollection', 'features' => [], 'latest' => $since]);
        exit;
    }

    $features_since = array_filter($live_data['features'], function($feature) use ($since) {
        return isset($feature['properties']['published_ts']) && $feature['properties']['published_ts'] > $since;
    });

    // Re-index the array to ensure it's a JSON array, not an object
    $features_since = array_values($features_since);

    echo json_encode([
        'type' => 'FeatureCollection',
        'features' => $features_since,
        'latest' => $live_data['latest'] ?? $since
    ]);
    exit;
  }

    if (
    filter_input(INPUT_GET, 'wikidata_lookup', FILTER_VALIDATE_BOOLEAN) !== null &&
    filter_input(INPUT_GET, 'lat', FILTER_VALIDATE_FLOAT) !== false &&
    filter_input(INPUT_GET, 'lon', FILTER_VALIDATE_FLOAT) !== false
    ) {
    header('Content-Type: application/json');
    $lat = filter_input(INPUT_GET, 'lat', FILTER_VALIDATE_FLOAT);
    $lon = filter_input(INPUT_GET, 'lon', FILTER_VALIDATE_FLOAT);
    if ($lat === false || $lon === false) {
      echo json_encode(['error' => 'Invalid coordinates.']);
      exit;
    }
    // Step 1: Find the nearest Wikidata item using geosearch
    $radius = 10000; // 10km search radius
    $geosearch_url = "https://www.wikidata.org/w/api.php?action=query&list=geosearch&gscoord={$lat}|{$lon}&gsradius={$radius}&gslimit=1&format=json";
    $opts = [ "http" => [ "header" => "User-Agent: Pulse/1.0 (pulse.app; contact@example.com)\r\n" ] ];
    $context = stream_context_create($opts);
    $geosearch_response_json = @file_get_contents($geosearch_url, false, $context);
    if ($geosearch_response_json === FALSE) {
      echo json_encode(['error' => 'Failed to fetch geosearch data from Wikidata.']);
      exit;
    }
    $geosearch_data = json_decode($geosearch_response_json, true);
    $qid = null;
    if (isset($geosearch_data['query']['geosearch'][0]['title'])) {
      $qid = $geosearch_data['query']['geosearch'][0]['title'];
    }
    if ($qid) {
      // Step 2: Get details for the found Wikidata item (QID)
      $entity_url = "https://www.wikidata.org/w/api.php?action=wbgetentities&ids={$qid}&format=json&props=descriptions|claims&languages=en";
      $entity_response_json = @file_get_contents($entity_url, false, $context);
      if ($entity_response_json === FALSE) {
        echo json_encode(['error' => 'Failed to fetch entity data from Wikidata.']);
        exit;
      }
      $entity_data = json_decode($entity_response_json, true);
      echo json_encode($entity_data);
    } else {
      echo json_encode(['result' => null]);
    }
    exit;
    }

  if (filter_input(INPUT_GET, 'current_events', FILTER_VALIDATE_BOOLEAN) !== null) {
    header('Content-Type: application/json');
    $events_file = 'data/current_events.geojson';
    if (file_exists($events_file)) {
      $txt = @file_get_contents($events_file);
      if ($txt === FALSE || !trim($txt)) {
        echo json_encode(['type' => 'FeatureCollection', 'features' => []]);
      } else {
        echo $txt;
      }
    } else {
      echo json_encode(['type' => 'FeatureCollection', 'features' => []]);
    }
    exit;
  }

  if (isset($_GET['current_events_status'])) {
    header('Content-Type: application/json');
    $running = file_exists(__DIR__ . '/data/current_events.running');
    echo json_encode(['running' => $running]);
    exit;
  }

  if (filter_input(INPUT_GET, 'earthquakes', FILTER_VALIDATE_BOOLEAN) !== null) {
    header('Content-Type: application/json');
    $url = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_month.geojson";
    $data = @file_get_contents($url);
    if ($data === FALSE) {
        echo json_encode(['features' => []]); // Return empty GeoJSON structure on error
        exit;
    }
    echo $data;
    exit;
  }

  if (isset($_GET['cities']) && $_GET['cities'] === 'all') {
    header('Content-Type: application/json');
    $cities = [];
    if (($handle = fopen("cities.csv", "r")) !== FALSE) {
        $header = fgetcsv($handle); // Read header
        while (($data = fgetcsv($handle, 1000, ",")) !== FALSE) {
            $row = array_combine($header, $data);
            if (isset($row['lat']) && isset($row['lng'])) {
                $cities[] = [
                    'city' => $row['city'],
                    'lat' => floatval($row['lat']),
                    'lng' => floatval($row['lng']),
                    'population' => isset($row['population']) ? intval($row['population']) : 0
                ];
            }
        }
        fclose($handle);
    }
    echo json_encode($cities);
    exit;
  }

  if (isset($_GET['news_for_city'])) {
    header('Content-Type: application/json');
    $city = $_GET['news_for_city'];
    $url = "https://news.google.com/rss/search?q=" . urlencode($city) . "&hl=en-US&gl=US&ceid=US:en";
    
    // Suppress errors from file_get_contents for invalid feeds
    $rss_content = @file_get_contents($url);
    if ($rss_content === FALSE) {
        echo json_encode([]);
        exit;
    }
    
    $xml = simplexml_load_string($rss_content);
    if ($xml === FALSE) {
        echo json_encode([]);
        exit;
    }
    
    $articles = [];
    if (isset($xml->channel->item)) {
        foreach ($xml->channel->item as $item) {
            $description = (string)$item->description;
            $image_url = '';
            // Use regex to find the src attribute of the first img tag
            if (preg_match('/<img src="([^"]+)"/', $description, $matches)) {
                $image_url = $matches[1];
            }

            $articles[] = [
                'title' => (string)$item->title,
                'link' => (string)$item->link,
                'pubDate' => (string)$item->pubDate,
                'source' => (string)$item->source,
                'image' => $image_url,
            ];
        }
    }

    echo json_encode($articles);
    exit;
  }

  // Reddit search proxy: returns recent reddit posts matching the query
  if (isset($_GET['reddit_search'])) {
    header('Content-Type: application/json');
    $q = $_GET['reddit_search'];
    $limit = isset($_GET['limit']) ? intval($_GET['limit']) : 20;
    $qenc = urlencode($q);
    $url = "https://www.reddit.com/search.json?q={$qenc}&sort=new&limit={$limit}";
    $opts = [ "http" => [ "header" => "User-Agent: Pulse/1.0 (pulse.app; contact@example.com)\r\n", "timeout" => 5 ] ];
    $context = stream_context_create($opts);
    $json = @file_get_contents($url, false, $context);
    if ($json === FALSE) {
      echo json_encode([]);
      exit;
    }
    $data = json_decode($json, true);
    $items = [];
    if (isset($data['data']['children']) && is_array($data['data']['children'])) {
      foreach ($data['data']['children'] as $c) {
        $p = $c['data'] ?? [];
        $items[] = [
          'title' => $p['title'] ?? '',
          'subreddit' => $p['subreddit'] ?? '',
          'url' => isset($p['permalink']) ? ('https://reddit.com' . $p['permalink']) : ($p['url'] ?? ''),
          'created_utc' => isset($p['created_utc']) ? intval($p['created_utc']) : 0,
          'score' => isset($p['score']) ? intval($p['score']) : 0
        ];
      }
    }
    echo json_encode($items);
    exit;
  }

  if (isset($_GET['lat']) && isset($_GET['lon']) && isset($_GET['radius'])) {
    header('Content-Type: application/json');
    $lat = floatval($_GET['lat']);
    $lon = floatval($_GET['lon']);
    $radius = floatval($_GET['radius']);
    // Sanitize inputs
    $escaped_lat = escapeshellarg($lat);
    $escaped_lon = escapeshellarg($lon);
    $escaped_radius = escapeshellarg($radius);
    $command = "python3 find_cities.py --lat {$escaped_lat} --lon {$escaped_lon} --radius {$escaped_radius}";
    $output = shell_exec($command);
    echo $output;
    exit;
  }

  // LLM query endpoint: forwards JSON POST with { prompt, system_prompt } to local HTTP LLM server
  if (isset($_GET['ask_llm'])) {
    header('Content-Type: application/json');

    // Accept either POST with JSON body, or GET with ?prompt=... for quick testing
    $method = $_SERVER['REQUEST_METHOD'];
    if ($method === 'POST') {
      $body = file_get_contents('php://input');
      if (!$body) {
        http_response_code(400);
        echo json_encode(['error' => 'Empty request body']);
        exit;
      }
    } elseif ($method === 'GET' && isset($_GET['prompt'])) {
      $payload = ['prompt' => $_GET['prompt']];
      if (isset($_GET['system_prompt'])) $payload['system_prompt'] = $_GET['system_prompt'];
      $body = json_encode($payload);
    } else {
      http_response_code(400);
      echo json_encode(['error' => 'Invalid request method or missing prompt']);
      exit;
    }

    // Log the ask_llm invocation (safe-truncate body)
    try { pulse_log('ask_llm', $body); } catch(Exception $e) {}

    // Quick health check before forwarding
    $health_url = 'http://127.0.0.1:5005/health';
    $hch = curl_init($health_url);
    curl_setopt($hch, CURLOPT_RETURNTRANSFER, true);
    curl_setopt($hch, CURLOPT_CONNECTTIMEOUT, 1);
    curl_setopt($hch, CURLOPT_TIMEOUT, 2);
    $hresp = curl_exec($hch);
    $hcode = curl_getinfo($hch, CURLINFO_HTTP_CODE);
    curl_close($hch);
    if ($hresp === false || $hcode !== 200) {
      http_response_code(502);
      echo json_encode(['error' => 'LLM server unavailable', 'details' => $hresp ?: 'no response']);
      exit;
    }

    // Forward to local LLM HTTP server
    $llm_url = 'http://127.0.0.1:5005/ask';
    $ch = curl_init($llm_url);
    curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
    curl_setopt($ch, CURLOPT_HTTPHEADER, ['Content-Type: application/json']);
    curl_setopt($ch, CURLOPT_POST, true);
    curl_setopt($ch, CURLOPT_POSTFIELDS, $body);
    // short timeout to avoid tying up PHP workers; adjust as needed
    curl_setopt($ch, CURLOPT_CONNECTTIMEOUT, 2);
    curl_setopt($ch, CURLOPT_TIMEOUT, 60);

    $response = curl_exec($ch);
    $http_code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    $curl_err = curl_error($ch);
    curl_close($ch);

    if ($response === false || $http_code !== 200) {
      http_response_code(502);
      echo json_encode(['error' => 'LLM server error', 'http_code' => $http_code, 'details' => $curl_err ?: $response]);
      exit;
    }

    // The LLM server already returns JSON { response: "..." }
    echo $response;
    exit;
  }

  // Endpoint to stop any running LLM process
  if (isset($_GET['stop_llm'])) {
    header('Content-Type: application/json');
    $pid_file = __DIR__ . '/.llm_pid';
    if (!file_exists($pid_file)) {
      echo json_encode(['status' => 'none', 'message' => 'No running LLM process found.']);
      exit;
    }
    $pid = intval(@file_get_contents($pid_file));
    if ($pid <= 0) {
      @unlink($pid_file);
      echo json_encode(['status' => 'none', 'message' => 'No valid PID found.']);
      exit;
    }
    // Try to kill the process
    exec('kill -9 ' . $pid . ' 2>&1', $out, $rc);
    @unlink($pid_file);
    if ($rc === 0) {
      echo json_encode(['status' => 'killed', 'pid' => $pid]);
    } else {
      echo json_encode(['status' => 'failed', 'pid' => $pid, 'output' => $out]);
    }
    exit;
  }

  // Python script is now run on-demand via a button in the UI.
  
  // Calculate yesterday's date on the server to avoid client clock issues.
  $yesterday = new DateTime('yesterday', new DateTimeZone('UTC'));
  $gibs_date = $yesterday->format('Y-m-d');
?>
<!doctype html>
<html>
  <head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Pulse</title>
    <link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><circle cx=%2250%22 cy=%2250%22 r=%2250%22 fill=%22red%22/></svg>">
    <link rel="stylesheet" href="https://maxcdn.bootstrapcdn.com/bootstrap/4.5.2/css/bootstrap.min.css">
    <script src="https://ajax.googleapis.com/ajax/libs/jquery/3.5.1/jquery.min.js"></script>
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/4.7.0/css/font-awesome.min.css" />
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/Leaflet.awesome-markers/2.0.2/leaflet.awesome-markers.css" />
    <style>
      html,body,#map { height: 100%; margin: 0; padding: 0 }
      .news-popup { max-width: 640px; }
      .leaflet-popup-content-wrapper, .leaflet-popup-content {
        max-width: 640px !important;
      }
      .leaflet-popup-content {
        font-size: 15px;
        line-height: 1.3;
      }
      .news-title { font-weight: 600; margin-bottom: 6px; }
      .news-source { color: #666; font-size: 90%; }
      .news-summary { margin-top: 6px; }
      .event-text { max-height: none; overflow: visible; }
      .event-headlines { margin-top: 6px; }
      .news-list {
        list-style: none;
        padding: 0;
        margin: 0;
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
        gap: 10px;
        align-items: start;
      }
      .news-list li {
        display: flex;
        gap: 10px;
        align-items: flex-start;
      }
      .news-list img { width: 60px; height: 60px; object-fit: cover; }
      .date-control {
        background: rgba(255, 255, 255, 0.7);
        padding: 2px 5px;
        border-radius: 3px;
        font-family: sans-serif;
        font-size: 12px;
      }
      /* Dark theme for popups when 'dark-mode' class is present */
      .dark-mode .leaflet-popup-content-wrapper,
      .dark-mode .leaflet-popup-tip {
        background: #333;
        color: #fff;
        box-shadow: 0 3px 14px rgba(0,0,0,0.4);
      }
      .dark-mode .leaflet-popup-content a {
        color: #7cc;
      }
      .dark-mode .leaflet-popup-close-button {
        color: #fff !important;
      }
      .dark-mode .news-source {
        color: #ccc;
      }
      .info-panel {
        background: white;
        padding: 10px;
        padding-top: 25px; /* space for close button */
        position: absolute; /* Changed from relative to absolute */
        top: 80px; /* Below top controls */
        right: 10px;
        z-index: 1000; /* Ensure it sits on top of the map */
        border-radius: 5px;
        box-shadow: 0 1px 5px rgba(0,0,0,0.65);
        max-width: 400px;
        max-height: 80vh; /* 80% of viewport height */
        overflow-y: auto;
        display: none; /* Hidden by default */
      }
      .info-panel-close {
        position: absolute;
        top: 5px;
        right: 10px;
        font-size: 25px;
        line-height: 1;
        cursor: pointer;
        color: #555;
        font-weight: bold;
      }
      .dark-mode .info-panel {
        background: #333;
        color: #fff;
      }
      .dark-mode .info-panel-close {
        color: #ccc;
      }
      .sparkle {
        position: absolute;
        top: 50%;
        left: 50%;
        width: 40px;
        height: 40px;
        background: radial-gradient(circle, rgba(255,0,0,0.8) 0%, rgba(255,255,0,0.6) 50%, rgba(255,255,255,0) 70%);
        border-radius: 50%;
        transform: translate(-50%, -50%) scale(0);
        opacity: 0;
        animation: sparkle-effect 1.2s ease-out;
        pointer-events: none;
      }

      /* LLM console at bottom for diagnostics */
      .llm-console {
        position: fixed;
        left: 10px;
        right: 10px;
        bottom: 0;
        max-height: 220px;
        background: rgba(18,18,18,0.95);
        color: #e6eef8;
        font-family: monospace;
        font-size: 13px;
        overflow-y: auto;
        padding: 8px 12px 12px 12px;
        border-radius: 6px 6px 0 0;
        z-index: 2000;
        display: none;
      }
      .llm-console .entry { 
        margin-bottom: 6px;
        white-space: pre-wrap; /* preserve newlines and wrap long lines */
        overflow-wrap: anywhere; /* break long words if needed */
        word-break: break-word;
      }
      .llm-console .entry.info { color: #9fd3ff; }
      .llm-console .entry.warn { color: #ffd27a; }
      .llm-console .entry.error { color: #ff8a8a; }
      .llm-console .controls { position: absolute; right: 12px; top: 6px; }
      .llm-console .controls button { margin-left: 6px; }

      @keyframes sparkle-effect {
        0% {
          transform: translate(-50%, -50%) scale(0);
          opacity: 1;
        }
        70% {
          transform: translate(-50%, -50%) scale(1.5);
          opacity: 0.5;
        }
        100% {
          transform: translate(-50%, -50%) scale(2);
          opacity: 0;
        }
      }
    </style>
  </head>
  <body>
    <div id="map"></div>

    <!-- LLM console for diagnostics and visible 'thinking' logs -->
    <div id="llm-console" class="llm-console" aria-live="polite">
      <div class="controls">
        <button id="llm-console-toggle" class="btn btn-sm btn-light">Hide</button>
        <button id="llm-console-clear" class="btn btn-sm btn-light">Clear</button>
      </div>
      <div id="llm-console-entries"></div>

      <!-- Inline QA input for ad-hoc questions about the news -->
      <div style="margin-top:8px;">
        <textarea id="llm-query" rows="2" placeholder="Ask the LLM about recent news..." style="width:100%;resize:vertical;margin-bottom:6px;"></textarea>
        <div style="text-align:right;">
          <button id="llm-query-submit" class="btn btn-sm btn-primary">Ask LLM</button>
        </div>
      </div>
    </div>

    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/Leaflet.awesome-markers/2.0.2/leaflet.awesome-markers.js"></script>
    <script>
      // Utility function for escaping HTML (was missing)

      // Add setTheme function to handle base layer theme changes
      function setTheme(layerName) {
        // Example: toggle dark mode based on layer name
        if (layerName && typeof layerName === 'string' && layerName.toLowerCase().includes('dark')) {
          document.body.classList.add('dark-mode');
        } else {
          document.body.classList.remove('dark-mode');
        }
      }
      function escapeHtml(text) {
        if (!text) return '';
        return text.toString()
          .replace(/&/g, "&amp;")
          .replace(/</g, "&lt;")
          .replace(/>/g, "&gt;")
          .replace(/"/g, "&quot;")
          .replace(/'/g, "&#039;");
      }

      // LLM console logging helper
      function logToConsole(message, level) {
        try {
          const el = document.getElementById('llm-console');
          const entries = document.getElementById('llm-console-entries');
          if (!el || !entries) return;
          const entry = document.createElement('div');
          entry.className = 'entry ' + (level || 'info');
          const ts = new Date().toLocaleTimeString();
          entry.textContent = `${ts} - ${message}`;
          entries.appendChild(entry);
          el.style.display = 'block';
          entries.scrollTop = entries.scrollHeight;
        } catch (e) { console.error('logToConsole error', e); }
      }

      // Append streaming text to a console entry using a simple spacing heuristic.
      // Insert a single space between chunks unless one of several no-space
      // conditions applies (existing ends with whitespace, chunk starts with
      // whitespace, punctuation, mid-word join, or numeric comma grouping).
      function appendWithSpace(existing, add) {
        if (!add) return existing || '';
        if (!existing) return add;
        try {
          // If either side already has whitespace adjacency, just concatenate
          if (/\s$/.test(existing) || /^\s/.test(add)) return existing + add;

          const lastChar = existing.charAt(existing.length - 1) || '';
          const firstChar = add.charAt(0) || '';

          // Mid-word join: letter + lowercase letter (e.g. 'Ch' + 'imo' -> 'Chimo')
          if (/[A-Za-zÀ-ÖØ-öø-ÿ]$/.test(existing) && /^[a-zà-öø-ÿ]/.test(add)) {
            return existing + add;
          }

          // Apostrophe contractions: don't insert space after an apostrophe
          if (/[\u2018\u2019'`\u00B4\u02BC]$/.test(existing) && /^[A-Za-zÀ-ÖØ-öø-ÿ]/.test(add)) {
            return existing + add;
          }

          // Numeric thousands grouping: comma followed by digits (e.g. '1,' + '600' -> '1,600')
          if (/,$/.test(existing) && /^\d/.test(add)) {
            return existing + add;
          }

          // No space before common closing punctuation or commas/periods
          if (/^[\.,;:!?)\]\}]/.test(add)) return existing + add;

          // No space after opening punctuation like '(', '[', '{'
          if (/[\(\[\{]$/.test(existing)) return existing + add;

          // Default: insert a single space
          return existing + ' ' + add;
        } catch (e) {
          return existing + add;
        }
      }

      function appendStreamText(el, chunk) {
        if (!chunk) return;
        try {
          const existing = el.textContent || '';
          el.textContent = appendWithSpace(existing, chunk);
        } catch (e) { /* ignore */ }
      }

      const map = L.map('map').setView([39.8283, -98.5795], 4);

      // Re-enable browser context menu (for inspector) on the map.
      L.DomEvent.off(map.getContainer(), 'contextmenu');

      const baseLayers = {
        'OpenStreetMap': L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
          attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
          maxZoom: 19
        }),
        'Topographical': L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png', {
          attribution: 'Map data: &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors, <a href="http://viewfinderpanoramas.org">SRTM</a> | Map style: &copy; <a href="https://opentopomap.org">OpenTopoMap</a> (<a href="https://creativecommons.org/licenses/by-sa/3.0/">CC-BY-SA</a>)',
          maxZoom: 17
        }),
        'Dark': L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
          attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
          subdomains: 'abcd',
          maxZoom: 19
        }),
        'Satellite': L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
          maxZoom: 19,
          attribution: 'Tiles &copy; Esri'
        })
      };

      const date = '<?php echo $gibs_date; ?>';

      const displayDate = new Date(date + 'T12:00:00Z').toLocaleDateString('en-US', { 
        year: 'numeric', 
        month: 'long', 
        day: 'numeric',
        timeZone: 'UTC' 
      });

      const DateControl = L.Control.extend({
        onAdd: function(map) {
          const div = L.DomUtil.create('div', 'date-control');
          div.innerHTML = `Weather Date: ${displayDate}`;
          return div;
        },
        onRemove: function(map) {
          // Nothing to do here
        }
      });
      const dateControl = new DateControl({ position: 'bottomleft' });
      dateControl.addTo(map);

      // --- Info Panel Control (Restored) ---
      const InfoControl = L.Control.extend({
        onAdd: function(map) {
            this._div = L.DomUtil.create('div', 'info-panel');
            this._div.innerHTML = '<div class="info-panel-header" style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px;"><div class="info-panel-title" style="font-weight:600;">Info Panel</div><span class="info-panel-close" style="cursor:pointer;">&times;</span></div><div class="info-panel-content"></div>';
            
            const closeButton = this._div.querySelector('.info-panel-close');
            L.DomEvent.on(closeButton, 'click', (e) => {
                L.DomEvent.stop(e); // Prevent propagation
                this.hide();
                if (searchCircle) {
                    map.removeLayer(searchCircle);
                    searchCircle = null;
                }
            }, this);
            
            // Stop clicks from propagating to the map
            L.DomEvent.disableClickPropagation(this._div);
            // Prevent mouse wheel / touchpad scrolls from zooming/panning the map when over the panel
            if (L.DomEvent.disableScrollPropagation) {
              L.DomEvent.disableScrollPropagation(this._div);
            }
            return this._div;
        },

        update: function(html) {
            this._div.querySelector('.info-panel-content').innerHTML = html;
            this._div.style.display = 'block';
        },

        hide: function() {
            this._div.style.display = 'none';
            this._div.querySelector('.info-panel-content').innerHTML = '';
        }
      });
      const infoPanel = new InfoControl({ position: 'topright' });
      infoPanel.addTo(map);

      const citiesLayer = L.layerGroup();
      const currentEventsLayer = L.layerGroup();
      let searchCircle = null; // Visual blue circle for news/search area
      let currentEventsGeoJsonLayer = null; // Track the actual GeoJSON layer
      let currentEventsPollTimer = null;
      let currentEventsSeenIds = new Set();

      const overlays = {
        'Current Events': currentEventsLayer,
        'Cities': citiesLayer,
        'Weather': L.tileLayer(`pulse.php?proxy_gibs=true&date=${date}&z={z}&y={y}&x={x}`, {
          attribution: '&copy; NASA GIBS',
          maxZoom: 9
        })
        // 'Earthquakes' will be added after earthquakesLayer is created
      };

      // Toggle to enable/disable asking the LLM
      let llmEnabled = true;

      // --- Current Events (Wikipedia) ---
      let currentEventsLoaded = false;

      function loadCurrentEvents() {
        // Start polling for incremental current events and add them as they arrive
        const eventIcon = L.AwesomeMarkers.icon({ icon: 'globe', markerColor: 'cadetblue', prefix: 'fa' });
        const options = {
          pointToLayer: (feature, latlng) => L.marker(latlng, { icon: eventIcon }),
          onEachFeature: (feature, layer) => {
            const p = feature.properties || {};
              let llmSentence = p.llm_sentence ? escapeHtml(p.llm_sentence) : '';
              let eventText = p.event_text ? escapeHtml(p.event_text) : '';
              let llmOnlyParsed = p.llm_only_geocode_parsed ? (typeof p.llm_only_geocode_parsed === 'object' ? (p.llm_only_geocode_parsed.lat + ',' + p.llm_only_geocode_parsed.lng) : String(p.llm_only_geocode_parsed)) : '';
              let llmOnlyRaw = p.llm_only_geocode_raw ? escapeHtml(p.llm_only_geocode_raw) : '';
            let eventLinksHtml = '';
            if (Array.isArray(p.event_links) && p.event_links.length > 0) {
              eventLinksHtml = `
                <div style="margin-top:6px;">
                  <b>Related sources:</b>
                  <ul style="margin:0;padding-left:18px;">
                    ${p.event_links.map(link => `<li><a href="${link}" target="_blank" rel="noopener noreferrer">${link}</a></li>`).join('')}
                  </ul>
                </div>
              `;
            }
            let popupHtml = `
              <strong><a href="${p.url}" target="_blank">${p.title}</a></strong><br>
              <div class="event-text">
                <em>${eventText}</em>
              </div>
              ${eventLinksHtml}
              <hr>
              <div class="event-headlines">
                <b>Top headlines:</b>
                <ul class="news-list">
                  ${Array.isArray(p.headlines_urls) ? p.headlines_urls.map(h => `<li><a href="${h.url}" target="_blank">${h.title}</a></li>`).join('') : ''}
                </ul>
              </div>
              <hr>
              <div style="font-size:90%;color:#888;">
                <b>LLM evaluated sentence for geolocation:</b><br>
                <span>${llmSentence}</span>
              </div>
              <div style="font-size:90%;color:#888;margin-top:6px;">
                <b>LLM-only geocode (parsed):</b><br>
                <span>${escapeHtml(llmOnlyParsed)}</span>
                <br>
                <b>LLM-only geocode (raw):</b><br>
                <span style="white-space:pre-wrap;">${llmOnlyRaw}</span>
              </div>
            `;
            layer.bindPopup(popupHtml);
            layer.on('click', function(e) { layer.openPopup(); });
          }
        };

        function ensureLayer() {
          if (!currentEventsGeoJsonLayer) {
            currentEventsGeoJsonLayer = L.geoJSON(null, options);
            currentEventsGeoJsonLayer.addTo(currentEventsLayer);
          }
        }

        // use global poll timer and seen id set so overlayremove can stop polling
        if (!currentEventsSeenIds) currentEventsSeenIds = new Set();

        async function pollOnce() {
          try {
            const r = await fetch('pulse.php?current_events=true&ts=' + Date.now());
            if (!r.ok) return;
            const txt = await r.text();
            if (!txt || !txt.trim()) return;
            let data;
            try {
              data = JSON.parse(txt);
            } catch (err) {
              // Partial or invalid JSON — skip this poll and try again
              return;
            }
            if (!data || !Array.isArray(data.features)) return;
            let added = 0;
            data.features.forEach(f => {
              const fid = f.id || (f.properties && f.properties.id);
              if (fid) {
                if (!currentEventsSeenIds.has(fid)) {
                  currentEventsSeenIds.add(fid);
                  ensureLayer();
                  currentEventsGeoJsonLayer.addData(f);
                  added++;
                }
              } else {
                // no id: add anyway (may duplicate)
                ensureLayer();
                currentEventsGeoJsonLayer.addData(f);
                added++;
              }
            });
            if (added > 0) {
              try { logToConsole('Added ' + added + ' current event(s)', 'info'); } catch(e){}
            }
            // Stop polling when server reports processing complete
            try {
              const s = await fetch('pulse.php?current_events_status=true&ts=' + Date.now());
              if (s.ok) {
                const st = await s.json();
                if (!st.running) {
                  if (currentEventsPollTimer) {
                    clearInterval(currentEventsPollTimer);
                    currentEventsPollTimer = null;
                  }
                }
              }
            } catch (e) { /* ignore status errors */ }
          } catch (e) {
            console.error('Error polling current events:', e);
          }
        }

        // Start immediate poll and then poll every 3s
        pollOnce();
        if (!currentEventsPollTimer) currentEventsPollTimer = setInterval(pollOnce, 3000);
      }

      // Remove pins when overlay is removed
      map.on('overlayremove', function(e) {
        if (e.name === 'Current Events' && currentEventsGeoJsonLayer) {
          currentEventsLayer.removeLayer(currentEventsGeoJsonLayer);
          currentEventsGeoJsonLayer = null;
          currentEventsLoaded = false;
        }
        // Stop polling and reset seen ids when the overlay is removed
        if (e.name === 'Current Events') {
          if (currentEventsPollTimer) {
            clearInterval(currentEventsPollTimer);
            currentEventsPollTimer = null;
          }
          currentEventsSeenIds = new Set();
        }
      });

      // Only load markers when overlay is added, and remove any default markers
      map.on('overlayadd', function(e) {
        if (e.name === 'Current Events') {
          if (currentEventsGeoJsonLayer) {
            currentEventsLayer.removeLayer(currentEventsGeoJsonLayer);
            currentEventsGeoJsonLayer = null;
            currentEventsLoaded = false;
          }
          loadCurrentEvents();
        }
      });

      const earthquakesLayer = L.geoJSON(null, {
        pointToLayer: function (feature, latlng) {
          const props = feature.properties || {};
          const mag = Math.max(props.mag || 1, 0.1);
          const time = props.time || Date.now();
          const hours = (Date.now() - time) / 3600000; // age in hours

          let fillColor = '#cccccc'; // default (old)
          if (hours <= 24) fillColor = 'red';
          else if (hours <= 24 * 7) fillColor = 'orange';
          else if (hours <= 24 * 14) fillColor = 'yellow';

          return L.circleMarker(latlng, {
            radius: Math.min(Math.max(mag * 2, 6), 14),
            fillColor: fillColor,
            color: '#000',
            weight: 1,
            opacity: 0.9,
            fillOpacity: 0.9
          });
        },
        onEachFeature: function (feature, layer) {
          const props = feature.properties;
          let popupContent = `<strong>Earthquake Details</strong><br>`;
          popupContent += `Magnitude: ${props.mag}<br>`;
          popupContent += `Location: ${props.place}<br>`;
          popupContent += `Time: ${new Date(props.time).toLocaleString()}<br>`;
          if (props.tsunami === 1) {
            popupContent += `Tsunami: Yes<br>`;
          } else {
            popupContent += `Tsunami: No<br>`;
          }
          // Add USGS details link if available
          if (props.url) {
            popupContent += `<a href="${props.url}" target="_blank" rel="noopener noreferrer">More details (USGS)</a>`;
          }
          layer.bindPopup(popupContent);
        }
      });

      fetch('pulse.php?earthquakes=true').then(r=>r.json()).then(data=>{
        earthquakesLayer.addData(data);
      });

      // Add earthquakes layer into overlays after earthquakesLayer exists
      overlays['Earthquakes'] = earthquakesLayer;

      // Add layer control (already created above)
      const layerControl = L.control.layers(baseLayers, overlays, { position: 'topright', collapsed: true }).addTo(map);

      // Allow clicking the map to look up nearby info
      map.on('click', function(e) {
        fetchAndShowCityInfo(e.latlng);
      });
      
      // Live News UI removed: live news pins and polling disabled.

      // --- City Info (Restored Logic) ---
      function showInfoPopup(data, latlng) {
        // Render static info panel content only once per click. Subsequent calls
        // (e.g. LLM streaming updates) will only update the dedicated LLM element
        // so links and other interactive elements are not destroyed while the
        // LLM is streaming.
        const panelContent = document.querySelector('.info-panel .info-panel-content');
        // If panel already rendered for this selection and we only have an LLM update,
        // update the LLM element and return early.
        if (panelContent && panelContent.dataset.staticRendered === '1') {
          if (data && (data.llm !== undefined && data.llm !== null)) {
            const llmEl = panelContent.querySelector('#llm-summary');
            if (llmEl) {
              llmEl.textContent = String(data.llm || '');
            }
            return;
          }
        }

        // (LLM summary removed from info panel)
        let html = 'No information found for this area.';

        // Format coordinates for display and include a Google Satellite link above the numbers
        const latStr = (latlng && latlng.lat != null) ? latlng.lat.toFixed(5) : '';
        const lonStr = (latlng && latlng.lng != null) ? latlng.lng.toFixed(5) : '';
        const gZoom = Math.max(map.getZoom() || 12, 12);
        const gUrl = (latStr && lonStr) ? `https://www.google.com/maps/@${latStr},${lonStr},${gZoom}z/data=!3m1!1e3` : '#';
        const coordsHtml = (latStr && lonStr)
          ? `<div style="font-size:90%;color:#666;margin-bottom:6px;">
               <a href="${gUrl}" target="_blank" rel="noopener noreferrer" style="color:#0066cc;text-decoration:underline;display:block;margin-bottom:4px;">Coordinates: ${latStr}, ${lonStr}</a>
             </div>`
          : '';

        const hasNearestCity = data && data.nearest_city;
        const hasOtherCities = data && data.other_cities && data.other_cities.length > 0;
        const hasWikiTopics = data && data.wiki_topics && data.wiki_topics.length > 0;

        if (hasNearestCity || hasOtherCities || hasWikiTopics) {
          html = '';
          if (hasNearestCity) {
            // show coordinates above the nearest-city block
            html += coordsHtml;
            const city = data.nearest_city;
            const search_query = `${city.name}${city.state ? ', ' + city.state : ''}${city.country ? ', ' + city.country : ''}`;
            const zoom = Math.max(map.getZoom(), 12);
            html += `<b>Nearest City:</b> <a href="https://en.wikipedia.org/w/index.php?search=${encodeURIComponent(search_query)}" target="_blank" rel="noopener noreferrer">${escapeHtml(city.name)}</a> <a href="https://news.google.com/search?q=${encodeURIComponent(search_query)}" target="_blank" rel="noopener noreferrer">(news)</a><br><small><a href="https://www.google.com/maps/@${latlng.lat},${latlng.lng},${zoom}z/data=!3m1!1e3" target="_blank" rel="noopener noreferrer">Google Satellite</a> &middot; <a href="https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=${latlng.lat},${latlng.lng}" target="_blank" rel="noopener noreferrer">Street View</a> &middot; <a href="https://www.openstreetmap.org/#map=${zoom}/${latlng.lat}/${latlng.lng}" target="_blank" rel="noopener noreferrer">OpenStreetMap</a></small><hr>`;
            
            // News headlines removed from popup (background news still fetched for LLM)
          }
          // Recent headlines section (re-added to main popup) - moved above other cities
          if (data.news && Array.isArray(data.news) && data.news.length) {
            html += '<div id="recent-headlines" style="border-top: 1px solid #ccc; margin-top: 10px; padding-top: 10px;">';
            html += '<b>Recent headlines:</b>';
            html += '<ul class="news-list" style="margin-top:6px;">';
            data.news.slice(0,6).forEach(a => {
              const title = a && a.title ? a.title : (typeof a === 'string' ? a : 'Untitled');
              const link = a && a.link ? a.link : '#';
              html += `<li style="margin-bottom:0.4em;"><a href="${link}" target="_blank" rel="noopener noreferrer">${escapeHtml(title)}</a></li>`;
            });
            html += '</ul></div>';
          }
          if (hasOtherCities) {
            html += '<div id="other-cities" style="border-top: 1px solid #ccc; margin-top: 10px; padding-top: 10px;">';
            html += '<b>Other cities in area:</b><ul style="padding-left: 1.2em; margin-top: 0;">';
            data.other_cities.forEach(city => {
              const search_query = `${city.name}, ${city.state}, ${city.country}`;
              html += `<li style="margin-bottom: 0.5em;"><a href="https://en.wikipedia.org/w/index.php?search=${encodeURIComponent(search_query)}" target="_blank" rel="noopener noreferrer">${escapeHtml(city.name)}</a> <a href="https://news.google.com/search?q=${encodeURIComponent(search_query)}" target="_blank" rel="noopener noreferrer">(news)</a></li>`;
            });
            html += '</ul></div>';
          }
          if (hasWikiTopics) {
            html += '<div id="wiki-topics" style="border-top: 1px solid #ccc; margin-top: 10px; padding-top: 10px;">';
            html += '<b>Wikipedia Related Area Topics:</b><ul style="padding-left: 1.2em; margin-top: 0;">';
            data.wiki_topics.forEach(topic => {
              html += `<li style="margin-bottom: 0.5em;"><a href="https://en.wikipedia.org/w/index.php?search=${encodeURIComponent(topic)}" target="_blank" rel="noopener noreferrer">${escapeHtml(topic)}</a></li>`;
            });
            html += '</ul></div>';
          }
          // Reddit posts (if any)
          if (data.reddit && Array.isArray(data.reddit) && data.reddit.length) {
            html += '<div id="reddit-headlines" style="border-top: 1px solid #ccc; margin-top: 10px; padding-top: 10px;">';
            html += '<b>Reddit posts:</b>';
            html += '<ul class="news-list" style="margin-top:6px;">';
            data.reddit.slice(0,8).forEach(r => {
              const title = r && r.title ? r.title : 'Untitled';
              const link = r && r.url ? r.url : '#';
              const subreddit = r && r.subreddit ? r.subreddit : '';
              const time = r && r.created_utc ? new Date(r.created_utc * 1000).toLocaleString() : '';
              html += `<li style="margin-bottom:0.4em;"><a href="${escapeHtml(link)}" target="_blank" rel="noopener noreferrer">${escapeHtml(title)}</a><br><small style="color:#666;">${escapeHtml(subreddit)} ${time ? '• ' + escapeHtml(time) : ''}</small></li>`;
            });
            html += '</ul></div>';
          }
        }
        // LLM summary intentionally omitted from the info panel.
        
        if (data.wikidata && data.wikidata.entities) {
          const qids = Object.keys(data.wikidata.entities);
          if (qids.length > 0) {
            const qid = qids[0];
            const entity = data.wikidata.entities[qid];
            if (entity && entity.descriptions && entity.descriptions.en) {
              if (html === 'No information found for this area.') html = '';
              html += '<div id="wikidata-details" style="border-top: 1px solid #ccc; margin-top: 10px; padding-top: 10px;">';
              html += `<b><a href="https://www.wikidata.org/wiki/${qid}" target="_blank" rel="noopener noreferrer">Wikidata Details</a>:</b>`;
              html += '<p style="margin-bottom: 0;">' + escapeHtml(entity.descriptions.en.value) + '</p>';
              html += '</div>';
            }
          }
        }

        // If other cities weren't rendered earlier, ensure they're shown at the bottom
        if (hasOtherCities) {
          // Avoid duplicating if the block already exists in the HTML
          if (!/id="other-cities"/.test(html)) {
            html += '<div id="other-cities" style="border-top: 1px solid #ccc; margin-top: 10px; padding-top: 10px;">';
            html += '<b>Other cities in area:</b><ul style="padding-left: 1.2em; margin-top: 0;">';
            data.other_cities.forEach(city => {
              const search_query = `${city.name}, ${city.state}, ${city.country}`;
              html += `<li style="margin-bottom: 0.5em;"><a href="https://en.wikipedia.org/w/index.php?search=${encodeURIComponent(search_query)}" target="_blank" rel="noopener noreferrer">${escapeHtml(city.name)}</a> <a href="https://news.google.com/search?q=${encodeURIComponent(search_query)}" target="_blank" rel="noopener noreferrer">(news)</a></li>`;
            });
            html += '</ul></div>';
          }
        }

        infoPanel.update(html);
        // Mark that the static content has been rendered so streaming updates
        // can update only the LLM element instead of replacing the whole panel.
        try {
          const pc = document.querySelector('.info-panel .info-panel-content');
          if (pc) pc.dataset.staticRendered = '1';
        } catch (e) {}
      }

      function fetchAndShowCityInfo(latlng) {
        const lat = latlng.lat;
        const lon = latlng.lng;

        // Clear the LLM console immediately when a new area is clicked
        try {
          const entries = document.getElementById('llm-console-entries');
          const consoleEl = document.getElementById('llm-console');
          if (entries) {
            entries.innerHTML = '<div class="entry info">Console cleared</div>';
            if (consoleEl) consoleEl.style.display = 'block';
            try { entries.scrollTop = entries.scrollHeight; } catch(e){}
          }
        } catch (e) { /* ignore */ }

        // Update info panel immediately so user sees a response; clear any
        // previous 'staticRendered' flag so the panel will be rebuilt for this click.
        try {
          infoPanel.update('<i>Updating details...</i>');
          const pc = document.querySelector('.info-panel .info-panel-content');
          if (pc) pc.dataset.staticRendered = '';
        } catch(e) {}

        if (searchCircle) {
          map.removeLayer(searchCircle);
        }

        const radius = 200; // 200km fixed radius

        searchCircle = L.circle(latlng, {
          radius: radius * 1000,
          color: 'blue',
          fillColor: '#33f',
          fillOpacity: 0.2
        }).addTo(map);
        
        const handleFailure = (err) => {
          console.error('Info lookup error', err);
          if (searchCircle) {
              map.removeLayer(searchCircle);
              searchCircle = null;
          }
          infoPanel.update('<i>Error looking up location information. Please try again.</i>');
        };
 
        // Use the Python script for city lookup (requires find_cities.py on server)
        // Note: The PHP endpoint for this is ?lat=...&lon=...&radius=...
        // The PHP code provided in the prompt handles this via shell_exec('python3 find_cities.py ...')
        // We need to parse that output. The PHP code returns the raw output from python.
        // Assuming find_cities.py returns JSON with nearest_city and other_cities.
        
        const citySearchPromise = fetch(`pulse.php?lat=${lat}&lon=${lon}&radius=${radius}`).then(r => r.json());
        const wikiSearchPromise = fetch(`pulse.php?geo_lookup=true&lat=${lat}&lon=${lon}`).then(r => r.json());
        const wikidataSearchPromise = fetch(`pulse.php?wikidata_lookup=true&lat=${lat}&lon=${lon}`).then(r => r.json());

        Promise.all([citySearchPromise, wikiSearchPromise, wikidataSearchPromise])
          .then(([cityData, wikiData, wikidata]) => {
            // cityData is expected to be the JSON output from find_cities.py
            // If find_cities.py returns a list of cities (as in previous versions), we might need to adapt.
            // However, the provided "old code" snippet uses `data.nearest_city` and `data.other_cities`.
            // The PHP code for `if (isset($_GET['lat']) ...)` executes `find_cities.py`.
            // If `find_cities.py` returns a list, we need to structure it to match `showInfoPopup` expectations.
            
            // Check if cityData is an array (list of cities) or object
            let structuredCityData = {};
            if (Array.isArray(cityData)) {
                if (cityData.length > 0) {
                    structuredCityData.nearest_city = cityData[0];
                    structuredCityData.other_cities = cityData.slice(1);
                }
            } else {
                structuredCityData = cityData;
            }

            const combinedData = {
                ...structuredCityData,
                wiki_topics: wikiData.titles || [],
                wikidata: wikidata
            };
            // If we have a nearest city, fetch recent headlines for it so the LLM has context
            const nearest = combinedData.nearest_city;
            const search_query = nearest ? `${nearest.name}${nearest.state ? ', ' + nearest.state : ''}${nearest.country ? ', ' + nearest.country : ''}` : '';
            const newsPromise = nearest ? fetch(`pulse.php?news_for_city=${encodeURIComponent(search_query)}`).then(r=>r.json()).catch(()=>[]) : Promise.resolve([]);
            const redditPromise = nearest ? fetch(`pulse.php?reddit_search=${encodeURIComponent(search_query)}&limit=12`).then(r=>r.json()).catch(()=>[]) : Promise.resolve([]);

            Promise.all([newsPromise, redditPromise]).then(([news, reddit]) => {
              combinedData.news = news || [];
              combinedData.reddit = reddit || [];
              // Show initial popup immediately with available info (news and reddit may be present)
              showInfoPopup(combinedData, latlng);

              // Build a concise prompt for the LLM
              let prompt = 'Give the historical context of events in the area ';
              prompt += `near the city: ${search_query} `;
              if (combinedData.other_cities && combinedData.other_cities.length) {
                prompt += 'Discuss how other nearby cities such as: ' + combinedData.other_cities.slice(0,6).map(c=>c.name).join(', ') + ' maybe related.';
              }
              prompt += 'Give a detailed NEWS SUMMARY section with (3-5 sentences), mention any likely historical causes or themes, and list 3-10 short tags." Context: ';
              if (combinedData.news && combinedData.news.length) {
                prompt += 'Recent headlines (titles):\n' + combinedData.news.slice(0,6).map(a=> '- ' + (a.title || a)).join(' ') + ' ';
              }
              if (combinedData.wiki_topics && combinedData.wiki_topics.length) {
                prompt += 'Area topics from Wikipedia: ' + combinedData.wiki_topics.slice(0,8).join('; ') + ' ';
              }
              if (typeof llmEnabled === 'undefined' || llmEnabled) {
                // Indicate the LLM started: show a temporary 'Thinking...' message in the popup
                combinedData.llm = 'Thinking...';
                showInfoPopup(combinedData, latlng);
                logToConsole(`Started LLM for ${search_query}`, 'info');

                const llmPayload = { prompt: prompt, system_prompt: 'You are a helpful, concise local news analyst. Keep answers short.' };
                // Log the exact request sent to the LLM into the console for debugging/visibility
                try { logToConsole('LLM request: ' + JSON.stringify(llmPayload), 'info'); } catch(e) {}
                // Only attempt direct streaming to local LLM server (no proxy fallback).
                const directUrl = 'http://127.0.0.1:5005/ask';
                (async function directStreamOnly(){
                  try {
                    const r = await fetch(directUrl + '?stream=1', {
                      method: 'POST',
                      headers: { 'Content-Type': 'application/json', 'Accept': 'text/event-stream' },
                      body: JSON.stringify(llmPayload)
                    });
                    if (!r.ok) throw new Error('Direct LLM server returned ' + r.status);

                    const reader = r.body.getReader();
                    const dec = new TextDecoder();
                    let buf = '';
                    let llm_partial = '';
                    let streamEntry = null;

                    while (true) {
                      const { done, value } = await reader.read();
                      if (done) break;
                      buf += dec.decode(value, { stream: true });
                      const parts = buf.split('\n\n');
                      buf = parts.pop();
                      for (const part of parts) {
                        const lines = part.split('\n');
                        const dataLines = lines.filter(l => l.startsWith('data:'));
                        if (dataLines.length) {
                          let data = dataLines.map(l => l.slice(6)).join('\n');
                          if (data === '[DONE]') { try { logToConsole('LLM stream done for ' + search_query, 'info'); } catch(e){}; continue; }
                          if (data.startsWith('[ERROR]')) { try { logToConsole('LLM stream error: ' + data, 'error'); } catch(e){}; continue; }
                          data = data.replace(/^\s*assistant[:\s]*/i, '');
                          if (!data) continue;
                          if (!streamEntry) {
                            const entries = document.getElementById('llm-console-entries');
                            streamEntry = document.createElement('div');
                            streamEntry.className = 'entry info stream-entry';
                            const ts = new Date().toLocaleTimeString();
                            streamEntry.textContent = `${ts} - `;
                            entries.appendChild(streamEntry);
                          }
                          llm_partial = appendWithSpace(llm_partial, data);
                          appendStreamText(streamEntry, data);
                           combinedData.llm = llm_partial;
                           showInfoPopup(combinedData, latlng);
                           try { streamEntry.parentElement.scrollTop = streamEntry.parentElement.scrollHeight; } catch(e) {}
                        } else {
                          let chunkText = (part || '').toString().replace(/^\s*assistant[:\s]*/i, '');
                          if (!chunkText) continue;
                          if (!streamEntry) {
                            const entries = document.getElementById('llm-console-entries');
                            streamEntry = document.createElement('div');
                            streamEntry.className = 'entry info stream-entry';
                            const ts = new Date().toLocaleTimeString();
                            streamEntry.textContent = `${ts} - `;
                            entries.appendChild(streamEntry);
                          }
                          llm_partial = appendWithSpace(llm_partial, chunkText);
                          appendStreamText(streamEntry, chunkText);
                           combinedData.llm = llm_partial;
                           showInfoPopup(combinedData, latlng);
                           try { streamEntry.parentElement.scrollTop = streamEntry.parentElement.scrollHeight; } catch(e) {}
                        }
                      }
                    }

                    if (buf && buf.trim()) {
                      const tail = buf.replace(/^\s*assistant[:\s]*/i, '');
                      if (tail) {
                        if (!streamEntry) {
                          const entries = document.getElementById('llm-console-entries');
                          streamEntry = document.createElement('div');
                          streamEntry.className = 'entry info stream-entry';
                          const ts = new Date().toLocaleTimeString();
                          streamEntry.textContent = `${ts} - `;
                          entries.appendChild(streamEntry);
                        }
                        llm_partial = appendWithSpace(llm_partial, tail);
                        appendStreamText(streamEntry, tail);
                         combinedData.llm = llm_partial;
                         showInfoPopup(combinedData, latlng);
                       }
                     }
                    try { logToConsole(`LLM (direct) completed for ${search_query}`, 'info'); } catch(e) {}
                  } catch (err) {
                    try { logToConsole('Direct LLM server call failed: ' + String(err), 'error'); } catch(e) {}
                    combinedData.llm = 'LLM call failed';
                    showInfoPopup(combinedData, latlng);
                  }
                })();
              } else {
                combinedData.llm = 'LLM disabled';
                showInfoPopup(combinedData, latlng);
           }
         });
          }).catch(handleFailure);
      }

        // On-demand data refresh control removed from UI.

      // LLM on/off control removed - LLM always enabled in UI

      // Wire up the console controls
      document.addEventListener('click', function initLLMConsoleHandlers(e){
        // Ensure we only bind once
        document.removeEventListener('click', initLLMConsoleHandlers);
        const toggle = document.getElementById('llm-console-toggle');
        const clear = document.getElementById('llm-console-clear');
        const consoleEl = document.getElementById('llm-console');
        const entries = document.getElementById('llm-console-entries');
        if (toggle && clear && consoleEl && entries) {
          toggle.addEventListener('click', function(){
            if (consoleEl.style.display === 'none' || consoleEl.style.display === '') {
              consoleEl.style.display = 'block';
              toggle.textContent = 'Hide';
            } else {
              consoleEl.style.display = 'none';
              toggle.textContent = 'Show';
            }
          });
          clear.addEventListener('click', function(){
            // Clear entries but leave a small notice so the console area remains visible
            entries.innerHTML = '<div class="entry info">Console cleared</div>';
            // Ensure console is visible and scrolled to the bottom
            consoleEl.style.display = 'block';
            try { entries.scrollTop = entries.scrollHeight; } catch(e){}
          });
          // LLM console inline query handler
          const qBtn = document.getElementById('llm-query-submit');
          const qInput = document.getElementById('llm-query');
          if (qBtn && qInput) {
            qBtn.addEventListener('click', async (ev) => {
              try {
                const prompt = (qInput.value || '').trim();
                if (!prompt) return;
                // Create a console entry for streaming output
                const streamEntry = document.createElement('div');
                streamEntry.className = 'entry info stream-entry';
                const ts = new Date().toLocaleTimeString();
                streamEntry.textContent = `${ts} - `;
                entries.appendChild(streamEntry);
                consoleEl.style.display = 'block';
                try { entries.scrollTop = entries.scrollHeight; } catch(e){}

                logToConsole('LLM inline query: ' + prompt, 'info');

                const payload = { prompt: prompt, system_prompt: 'You are a concise news analyst. Answer briefly and focus on recent relevant events.' };
                const r = await fetch('http://127.0.0.1:5005/ask?stream=1', {
                  method: 'POST',
                  headers: { 'Content-Type': 'application/json', 'Accept': 'text/event-stream' },
                  body: JSON.stringify(payload)
                });
                if (!r.ok) {
                  const txt = await r.text();
                  appendStreamText(streamEntry, 'Error: ' + r.status + ' ' + txt);
                  return;
                }
                const reader = r.body.getReader();
                const dec = new TextDecoder();
                let buf = '';
                while (true) {
                  const { done, value } = await reader.read();
                  if (done) break;
                  buf += dec.decode(value, { stream: true });
                  const parts = buf.split('\n\n');
                  buf = parts.pop();
                  for (const part of parts) {
                    const lines = part.split('\n');
                    const dataLines = lines.filter(l => l.startsWith('data:'));
                    if (dataLines.length) {
                      const data = dataLines.map(l => l.slice(6)).join('\n');
                      if (data === '[DONE]') continue;
                      appendStreamText(streamEntry, data);
                    } else {
                      appendStreamText(streamEntry, part);
                    }
                    try { entries.scrollTop = entries.scrollHeight; } catch(e){}
                  }
                }
                if (buf && buf.trim()) appendStreamText(streamEntry, buf);
              } catch (err) {
                try { appendStreamText(streamEntry, 'Request failed: ' + String(err)); } catch(e){}
              }
            });
          }
        }
      });

      // --- URL State Management & Initial Load ---
      let currentBaseLayerName;
      
        function updateUrl() {
            const center = map.getCenter();
            const zoom = map.getZoom();
            const url = new URL(window.location);
            url.searchParams.set('lat', center.lat.toFixed(5));
            url.searchParams.set('lon', center.lng.toFixed(5));
            url.searchParams.set('zoom', zoom);
            url.searchParams.set('base', currentBaseLayerName);

            const activeOverlays = [];
            for (const name in overlays) {
          if (map.hasLayer(overlays[name])) {
            activeOverlays.push(name);
            }
        }

        if (activeOverlays.length > 0) {
          url.searchParams.set('overlays', activeOverlays.join(','));
        } else {
          url.searchParams.delete('overlays');
        }

            window.history.replaceState({}, '', url);
        }

        map.on('moveend', updateUrl);
        map.on('overlayadd', updateUrl);
        map.on('overlayremove', updateUrl);
      map.on('baselayerchange', function(e) {
        setTheme(e.name);
          currentBaseLayerName = e.name;
          updateUrl();
        });

      const urlParams = new URLSearchParams(window.location.search);
      const urlLat = urlParams.get('lat');
      const urlLon = urlParams.get('lon');
      const urlZoom = urlParams.get('zoom');
      const urlBase = urlParams.get('base');
      const urlOverlays = urlParams.get('overlays');

      // Set view
      if (urlLat && urlLon) {
        const zoom = urlZoom ? parseInt(urlZoom, 10) : map.getZoom();
        map.setView([parseFloat(urlLat), parseFloat(urlLon)], zoom);
            }

      // Set base layer
      const initialBaseLayerName = (urlBase && baseLayers[urlBase]) ? urlBase : 'Satellite';
      baseLayers[initialBaseLayerName].addTo(map);
      currentBaseLayerName = initialBaseLayerName;
      setTheme(initialBaseLayerName);
      
      // Set overlays. Default to all off if 'overlays' param is not in URL.
      const initialOverlays = urlOverlays !== null 
          ? (urlOverlays === '' ? [] : urlOverlays.split(',')) 
          : [];

      initialOverlays.forEach(name => {
        if (overlays[name] && !map.hasLayer(overlays[name])) {
          overlays[name].addTo(map);
        }
      });

      // Load cities into the existing citiesLayer
        fetch('pulse.php?cities=all')
          .then(r => r.json())
          .then(cities => {
            if (!Array.isArray(cities)) return;
            cities.forEach(city => {
              try {
              const marker = L.circleMarker([city.lat, city.lng], {
                radius: 3,
                fillColor: "#ff7800",
                color: "#000",
                weight: 1,
                opacity: 1,
                fillOpacity: 0.8
              });
              marker.on('click', (e) => {
                L.DomEvent.stopPropagation(e);
                fetchAndShowCityInfo(e.latlng);
              });
              marker.addTo(citiesLayer);
            } catch (e) { console.error('City marker error', e); }
            });
          })
        .catch(e => console.error('Failed to load cities:', e));
    </script>
  </body>
</html>
