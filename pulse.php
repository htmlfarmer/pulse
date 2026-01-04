<?php
  if (isset($_GET['run_pulse_py'])) {
      header('Content-Type: application/json');
      set_time_limit(300); // 5 minutes, because the LLM can be slow

      // Delete old geojson before running the script
      $geojson_file = __DIR__ . '/data/current_events.geojson';
      if (file_exists($geojson_file)) {
          unlink($geojson_file);
      }

      $command = 'cd ' . __DIR__ . ' && python3 pulse.py 2>&1';
      $output = shell_exec($command);
      
      if ($output === null) {
          http_response_code(500);
          echo json_encode(['status' => 'error', 'message' => 'Failed to execute script. Check server logs and file permissions.']);
      } else {
          // Check for fatal errors in output, as shell_exec doesn't give a reliable status code
          if (strpos($output, 'FATAL:') !== false) {
              http_response_code(500);
              echo json_encode(['status' => 'error', 'message' => 'Script executed with fatal errors.', 'output' => $output]);
          } else {
              echo json_encode(['status' => 'success', 'message' => 'Data refresh script executed.', 'output' => $output]);
          }
      }
      exit;
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

  if (isset($_GET['wikidata_lookup']) && isset($_GET['lat']) && isset($_GET['lon'])) {
    header('Content-Type: application/json');
    $lat = floatval($_GET['lat']);
    $lon = floatval($_GET['lon']);
    
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

  if (isset($_GET['current_events'])) {
    header('Content-Type: application/json');
    $events_file = 'data/current_events.geojson';
    if (file_exists($events_file)) {
        readfile($events_file);
    } else {
        echo json_encode(['type' => 'FeatureCollection', 'features' => []]);
    }
    exit;
  }

  if (isset($_GET['earthquakes'])) {
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
      .news-popup { max-width: 360px; }
      .leaflet-popup-content-wrapper, .leaflet-popup-content {
        max-width: 350px !important;
        max-height: 260px !important;
        overflow-y: auto !important;
      }
      .leaflet-popup-content {
        font-size: 15px;
        line-height: 1.3;
      }
      .news-title { font-weight: 600; margin-bottom: 6px; }
      .news-source { color: #666; font-size: 90%; }
      .news-summary { margin-top: 6px; }
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

    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/Leaflet.awesome-markers/2.0.2/leaflet.awesome-markers.js"></script>
    <script>
      // Utility function for escaping HTML (was missing)
      function escapeHtml(text) {
        if (!text) return '';
        return text.toString()
          .replace(/&/g, "&amp;")
          .replace(/</g, "&lt;")
          .replace(/>/g, "&gt;")
          .replace(/"/g, "&quot;")
          .replace(/'/g, "&#039;");
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
            this._div.innerHTML = '<span class="info-panel-close">&times;</span><div class="info-panel-content"></div>';
            
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
      const liveNewsLayer = L.layerGroup();
      const currentEventsLayer = L.layerGroup();
      let searchCircle = null; // Visual blue circle for news/search area
      let currentEventsGeoJsonLayer = null; // Track the actual GeoJSON layer

      const overlays = {
        'Current Events': currentEventsLayer,
        'Live News': liveNewsLayer,
        'Cities': citiesLayer,
        'Weather': L.tileLayer(`pulse.php?proxy_gibs=true&date=${date}&z={z}&y={y}&x={x}`, {
          attribution: '&copy; NASA GIBS',
          maxZoom: 9
        })
        // 'Earthquakes' will be added after earthquakesLayer is created
      };

      // --- Current Events (Wikipedia) ---
      let currentEventsLoaded = false;

      function loadCurrentEvents() {
        // Remove previous GeoJSON layer if it exists
        if (currentEventsGeoJsonLayer) {
          currentEventsLayer.removeLayer(currentEventsGeoJsonLayer);
          currentEventsGeoJsonLayer = null;
        }
        fetch('pulse.php?current_events=true')
          .then(r => r.json())
          .then(data => {
            if (data && data.features) {
              const eventIcon = L.AwesomeMarkers.icon({
                  icon: 'globe',
                  markerColor: 'cadetblue',
                  prefix: 'fa'
              });
              currentEventsGeoJsonLayer = L.geoJSON(data, {
                pointToLayer: (feature, latlng) => L.marker(latlng, { icon: eventIcon }),
                onEachFeature: (feature, layer) => {
                  const p = feature.properties;
                  let llmSentence = p.llm_sentence ? escapeHtml(p.llm_sentence) : '';
                  let eventText = p.event_text ? escapeHtml(p.event_text) : '';
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
                    <div style="max-height:100px;overflow:auto;">
                      <em>${eventText}</em>
                    </div>
                    ${eventLinksHtml}
                    <hr>
                    <div style="max-height:80px;overflow:auto;">
                      <b>Top headlines:</b>
                      <ul style="margin:0;padding-left:18px;">
                        ${Array.isArray(p.headlines_urls) ? p.headlines_urls.map(h => `<li><a href="${h.url}" target="_blank">${h.title}</a></li>`).join('') : ''}
                      </ul>
                    </div>
                    <hr>
                    <div style="font-size:90%;color:#888;">
                      <b>LLM evaluated sentence for geolocation:</b><br>
                      <span>${llmSentence}</span>
                    </div>
                  `;
                  layer.bindPopup(popupHtml);
                  layer.on('click', function(e) {
                    layer.openPopup();
                  });
                }
              });
              currentEventsGeoJsonLayer.addTo(currentEventsLayer);
              currentEventsLoaded = true;
            }
          })
          .catch(e => {
            console.error("Error fetching current events:", e);
            // Show a user-friendly error if desired
            alert("Failed to load current events data.");
          });
      }

      // Remove pins when overlay is removed
      map.on('overlayremove', function(e) {
        if (e.name === 'Current Events' && currentEventsGeoJsonLayer) {
          currentEventsLayer.removeLayer(currentEventsGeoJsonLayer);
          currentEventsGeoJsonLayer = null;
          currentEventsLoaded = false;
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
        if (e.name === 'Live News') {
          startLiveNews();
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

      // Allow clicking the map to look up nearby info / live news
      map.on('click', function(e) {
        fetchAndShowCityInfo(e.latlng);
      });
      
      // --- Live News ---
      let liveNewsTimer;
      let lastNewsTimestamp = 0;

      function startLiveNews() {
        if (liveNewsTimer) return; // Already running
        liveNewsTimer = setInterval(fetchLiveNews, 60000); // Update every minute
        fetchLiveNews(); // Initial fetch
      }

      function fetchLiveNews() {
        fetch('pulse.php?live_news=true&since=' + lastNewsTimestamp)
          .then(response => response.json())
          .then(data => {
            if (data && data.features) {
              const newsIcon = L.AwesomeMarkers.icon({
                  icon: 'info',
                  markerColor: 'orange',
                  prefix: 'fa'
              });
              const newItems = data.features.filter(item => item.properties.published_ts > lastNewsTimestamp);
              newItems.forEach(item => {
                const marker = L.geoJSON(item, {
                  pointToLayer: (feature, latlng) => L.marker(latlng, { icon: newsIcon }),
                  onEachFeature: (feature, layer) => {
                    const p = feature.properties;
                    let description = `<strong>${escapeHtml(p.title)}</strong>`;
                    description += `<br><em>${escapeHtml(p.summary)}</em>`;
                    description += `<br><small>Source: ${escapeHtml(p.source)}</small>`;
                    layer.bindPopup(description);
                  }
                });
                liveNewsLayer.addLayer(marker);
              });
              if (newItems.length > 0) {
                lastNewsTimestamp = newItems[newItems.length - 1].properties.published_ts;
              }
            }
          })
          .catch(e => console.error("Error fetching live news:", e));
      }

      // --- City Info (Restored Logic) ---
      function showInfoPopup(data, latlng) {
        let html = 'No information found for this area.';
        let all_articles = [];
        let current_article_index = 0;
        const articles_to_load = 5;

        function render_articles() {
          const newsDiv = document.getElementById('news-headlines');
          if (!newsDiv) return;

          let newsHtml = newsDiv.innerHTML;
          if (current_article_index === 0) {
              newsHtml = '<b>Top Headlines:</b><ul style="padding:0;margin:0;list-style:none">';
          }

          const articles_to_render = all_articles.slice(current_article_index, current_article_index + articles_to_load);
          
          articles_to_render.forEach(article => {
            newsHtml += `
              <li style="margin-bottom: 1em; display: flex; align-items: center;">
                ${article.image ? `<img src="${article.image}" style="width:60px;height:60px;margin-right:10px">` : ''}
                <div>
                  <a href="${article.link}" target="_blank" rel="noopener noreferrer">${escapeHtml(article.title)}</a>
                  <div style="font-size:90%"><i>${escapeHtml(article.source)}</i></div>
                </div>
              </li>`;
          });

          newsDiv.innerHTML = newsHtml + (current_article_index === 0 ? '</ul>' : '');
          current_article_index += articles_to_load;

          if (current_article_index >= all_articles.length) {
            newsDiv.onscroll = null; // No more articles to load
          }
        }

        const hasNearestCity = data && data.nearest_city;
        const hasOtherCities = data && data.other_cities && data.other_cities.length > 0;
        const hasWikiTopics = data && data.wiki_topics && data.wiki_topics.length > 0;

        if (hasNearestCity || hasOtherCities || hasWikiTopics) {
          html = '';
          if (hasNearestCity) {
            const city = data.nearest_city;
            const search_query = `${city.name}${city.state ? ', ' + city.state : ''}${city.country ? ', ' + city.country : ''}`;
            const zoom = Math.max(map.getZoom(), 12);
            html += `<b>Nearest City:</b> <a href="https://en.wikipedia.org/w/index.php?search=${encodeURIComponent(search_query)}" target="_blank" rel="noopener noreferrer">${escapeHtml(city.name)}</a> <a href="https://news.google.com/search?q=${encodeURIComponent(search_query)}" target="_blank" rel="noopener noreferrer">(news)</a><br><small><a href="https://www.google.com/maps/@${latlng.lat},${latlng.lng},${zoom}z/data=!3m1!1e3" target="_blank" rel="noopener noreferrer">Google Satellite</a> &middot; <a href="https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=${latlng.lat},${latlng.lng}" target="_blank" rel="noopener noreferrer">Street View</a> &middot; <a href="https://www.openstreetmap.org/#map=${zoom}/${latlng.lat}/${latlng.lng}" target="_blank" rel="noopener noreferrer">OpenStreetMap</a></small><hr>`;
            
            html += '<div id="news-headlines">Loading news...</div>';

            fetch(`pulse.php?news_for_city=${encodeURIComponent(search_query)}`)
              .then(r=>r.json())
              .then(news => {
                all_articles = news;
                const newsDiv = document.getElementById('news-headlines');
                if (news && news.length > 0) {
                  render_articles();
                  newsDiv.onscroll = () => {
                    if ((newsDiv.scrollTop + newsDiv.clientHeight) >= newsDiv.scrollHeight - 10) {
                      render_articles();
                    }
                  };
                } else {
                  newsDiv.innerHTML = '<i>No news found for this area.</i>';
                }
              });
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
        }
        
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

        infoPanel.update(html);
      }

      function fetchAndShowCityInfo(latlng) {
        const lat = latlng.lat;
        const lon = latlng.lng;

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
            showInfoPopup(combinedData, latlng);
          }).catch(handleFailure);
      }

      // --- On-Demand Data Refresh Control ---
      const RefreshControl = L.Control.extend({
        onAdd: function(map) {
            const container = L.DomUtil.create('div', 'leaflet-bar leaflet-control refresh-control');
            const link = L.DomUtil.create('a', '', container);
            link.href = '#';
            link.title = 'Refresh Data from Source';
            link.setAttribute('role', 'button');
            link.setAttribute('aria-label', 'Refresh Data');

            L.DomEvent.on(link, 'click', L.DomEvent.stop).on(link, 'click', function() {
                if (link.classList.contains('loading')) return;
                
                if (!confirm('This will run the data processing script on the server. It can take several minutes, especially if the LLM is running. Continue?')) return;

                link.classList.add('loading');
                link.title = 'Refreshing data... This may take a few minutes.';
                
                fetch('pulse.php?run_pulse_py=true')
                    .then(response => {
                        if (!response.ok) {
                            return response.json().then(err => { throw new Error(err.message || 'Server error') });
                        }
                        return response.json();
                    })
                    .then(data => {
                        console.log('Script output:', data.output);
                        alert('Data refresh complete! Reloading relevant layers...');
                        
                        // Invalidate and reload layers that have been loaded
                        if (currentEventsLoaded) {
                            currentEventsLoaded = false;
                            currentEventsLayer.clearLayers();
                            if(map.hasLayer(currentEventsLayer)) {
                                loadCurrentEvents();
                            }
                        }
                        // Live news will get updated on its own timer, but we can clear it
                        // to show only the newest items from the regenerated file.
                        liveNewsLayer.clearLayers();
                        if (map.hasLayer(liveNewsLayer)) {
                            lastNewsTimestamp = 0; // Force reload of all recent news
                            fetchLiveNews();
                        }
                      })
                    .catch(err => {
                        console.error('Refresh script error:', err);
                        alert('An error occurred while refreshing the data: ' + err.message);
                    })
                    .finally(() => {
                        link.classList.remove('loading');
                        link.title = 'Refresh Data from Source';
                    });
            });

            return container;
        },
      });
      new RefreshControl({ position: 'topleft' }).addTo(map);

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
