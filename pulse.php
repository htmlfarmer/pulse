<?php
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
  /*
  if (isset($_GET['proxy_noaa'])) {
    if (isset($_GET['debug_proxy'])) {
      header('Content-Type: text/plain');
      $params = $_GET;
      unset($params['proxy_noaa']);
      unset($params['debug_proxy']);
      $queryString = http_build_query($params);
      $url = "https://idpgis.ncep.noaa.gov/arcgis/services/NWS_Forecasts_Guidance_Warnings/natl_sfc_wx/MapServer/WMSServer?" . $queryString;
      echo "DEBUG MODE\n\n";
      echo "The proxy will request this URL from NOAA:\n";
      echo $url;
      exit;
    }
    $params = $_GET;
    unset($params['proxy_noaa']);
    $queryString = http_build_query($params);
    
    $url = "https://idpgis.ncep.noaa.gov/arcgis/services/NWS_Forecasts_Guidance_Warnings/natl_sfc_wx/MapServer/WMSServer?" . $queryString;

    $ch = curl_init();
    
    curl_setopt($ch, CURLOPT_URL, $url);
    curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
    curl_setopt($ch, CURLOPT_HEADER, false);
    curl_setopt($ch, CURLOPT_FOLLOWLOCATION, true);
    curl_setopt($ch, CURLOPT_USERAGENT, 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36');
    // Disable SSL verification for maximum compatibility
    curl_setopt($ch, CURLOPT_SSL_VERIFYPEER, false);
    curl_setopt($ch, CURLOPT_SSL_VERIFYHOST, false);

    $image_data = curl_exec($ch);
    $http_code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    $error_message = curl_error($ch);
    
    curl_close($ch);
    
    if ($http_code == 200 && $image_data) {
        header('Content-Type: image/png');
        echo $image_data;
    } else {
        http_response_code(404);
        echo "cURL Error: " . $error_message . " (HTTP Code: " . $http_code . ")";
    }
    exit;
  }
  */
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

  // Execute the python script to refresh the geojson data
  $output = shell_exec('python3 pulse.py');
  // todo: consider adding error handling here
  
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
    </style>
  </head>
  <body>
    <div id="map"></div>
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/Leaflet.awesome-markers/2.0.2/leaflet.awesome-markers.js"></script>
    <script>
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

      const citiesLayer = L.layerGroup();

      const overlays = {
        'Cities': citiesLayer,
        'Weather': L.tileLayer(`pulse.php?proxy_gibs=true&date=${date}&z={z}&y={y}&x={x}`, {
          attribution: '&copy; NASA GIBS',
          maxZoom: 9
        }),
        /* 'Pressure': L.tileLayer.wms('pulse.php', {
          layers: '2',
          format: 'image/png',
          transparent: true,
          attribution: 'NOAA/NWS',
          // Custom parameter to trigger our proxy
          proxy_noaa: true
        }) */
      };
      
      const earthquakesLayer = L.geoJSON(null, {
        pointToLayer: function(feature, latlng) {
          const mag = feature.properties.mag;
          const time = feature.properties.time;
          const hours = (Date.now() - time) / 3600000; // Quake age in hours
          
          let color = 'white';
          if (hours <= 24) color = 'red';
          else if (hours <= 24 * 7) color = 'orange';
          else if (hours <= 24 * 14) color = 'yellow';

          return L.circleMarker(latlng, {
            radius: mag * 1.5,
            fillColor: color,
            color: "#000",
            weight: 1,
            opacity: 1,
            fillOpacity: 0.8
          });
        },
        onEachFeature: function(feature, layer) {
          const props = feature.properties;
          const date = new Date(props.time).toLocaleString();
          const html = `<b>Magnitude ${props.mag}</b><br>${props.place}<br>${date}<br><a href="${props.url}" target="_blank" rel="noopener noreferrer">More details (USGS)</a>`;
          layer.bindPopup(html);
        }
      });

      fetch('pulse.php?earthquakes=true').then(r=>r.json()).then(data=>{
        earthquakesLayer.addData(data);
      });

      overlays['Earthquakes'] = earthquakesLayer;

      L.control.layers(baseLayers, overlays).addTo(map);

      // --- Theme switcher for popups ---
      const mapContainer = document.getElementById('map');
      
      // Function to set theme based on layer name
      function setTheme(layerName) {
        if (layerName === 'Dark') {
          mapContainer.classList.add('dark-mode');
        } else {
          mapContainer.classList.remove('dark-mode');
        }
      }

      // Listen for base layer changes are handled below
      
      // --- End theme switcher ---

      // fetch(`data/articles.geojson?v=${Date.now()}`).then(r=>r.json()).then(geo=>{
      //   if (!geo || !geo.features) return;
      //   geo.features.forEach(f=>{
      //     try {
      //       const coords = f.geometry && f.geometry.coordinates;
      //       if (!coords || coords.length < 2) return;
      //       const props = f.properties || {};
      //       const lon = coords[0], lat = coords[1];
            
      //       const title = props.title || props.place || 'News item';
      //       const place = props.place || '';
      //       const source = props.source || '';
      //       const link = props.news_link || '#';
      //       const summary = (props.summary || '').replace(/\n/g,' ').slice(0,800);
      //       const wikiTopic = props.wiki_topic || title;

      //       const html = `
      //         <div class="news-popup">
      //           <div class="news-title"><a href="${link}" target="_blank" rel="noopener noreferrer">${escapeHtml(title)}</a></div>
      //           <div class="news-source">${escapeHtml(source)} ${place?('&middot; '+escapeHtml(place)):''}</div>
      //           <div class="news-summary">${escapeHtml(summary)}</div>
      //           <div style="margin-top:6px">
      //             <a href="${link}" target="_blank" rel="noopener noreferrer">Open article</a>
      //             &middot;
      //             <a href="https://en.wikipedia.org/w/index.php?search=${encodeURIComponent(wikiTopic)}" target="_blank" rel="noopener noreferrer">Search Wikipedia</a>
      //           </div>
      //         </div>`;

      //       const marker = L.marker([lat, lon], {
      //         icon: L.AwesomeMarkers.icon({
      //           icon: props.icon || 'info-circle',
      //           markerColor: props.markerColor || 'gray',
      //           prefix: 'fa'
      //         })
      //       });
      //       marker.bindPopup(html);
      //       marker.addTo(map);
      //       allMarkers.push(marker);
      //     } catch (e) { console.error('feature error', e); }
      //   });
        
      //   if (allMarkers.length > 0) {
      //     const group = L.featureGroup(allMarkers);
      //     map.fitBounds(group.getBounds().pad(0.5));
      //   }
      // }).catch(e=>console.error(e));

      let searchCircle;

      fetch('pulse.php?cities=all').then(r=>r.json()).then(cities=>{
        cities.forEach(city => {
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
        });
      });
      function escapeHtml(s){
        if(!s) return '';
        return String(s)
          .replace(/&/g,'&amp;')
          .replace(/</g,'&lt;')
          .replace(/>/g,'&gt;')
          .replace(/"/g,'&quot;')
          .replace(/'/g,'&#39;');
      }

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
            // Changed to Wikipedia search URL
            const zoom = Math.max(map.getZoom(), 12);
            html += `<b>Nearest City:</b> <a href="https://en.wikipedia.org/w/index.php?search=${encodeURIComponent(search_query)}" target="_blank" rel="noopener noreferrer">${escapeHtml(city.name)}</a> <a href="https://news.google.com/search?q=${encodeURIComponent(search_query)}" target="_blank" rel="noopener noreferrer">(news)</a><br><small><a href="https://www.google.com/maps/@${latlng.lat},${latlng.lng},${zoom}z/data=!3m1!1e3" target="_blank" rel="noopener noreferrer">Google Satellite</a> &middot; <a href="https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=${latlng.lat},${latlng.lng}" target="_blank" rel="noopener noreferrer">Street View</a> &middot; <a href="https://www.openstreetmap.org/#map=${zoom}/${latlng.lat}/${latlng.lng}" target="_blank" rel="noopener noreferrer">OpenStreetMap</a></small><hr>`;
            
            html += '<div id="news-headlines" style="max-height: 250px; overflow-y: auto;">Loading news...</div>';

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
            html += '<div id="other-cities" style="max-height: 150px; overflow-y: auto; border-top: 1px solid #ccc; margin-top: 10px; padding-top: 10px;">';
            html += '<b>Other cities in area:</b><ul style="padding-left: 1.2em; margin-top: 0;">';
            data.other_cities.forEach(city => {
              const search_query = `${city.name}, ${city.state}, ${city.country}`;
              html += `<li style="margin-bottom: 0.5em;"><a href="https://en.wikipedia.org/w/index.php?search=${encodeURIComponent(search_query)}" target="_blank" rel="noopener noreferrer">${escapeHtml(city.name)}</a> <a href="https://news.google.com/search?q=${encodeURIComponent(search_query)}" target="_blank" rel="noopener noreferrer">(news)</a></li>`;
            });
            html += '</ul></div>';
          }
          if (hasWikiTopics) {
            html += '<div id="wiki-topics" style="max-height: 150px; overflow-y: auto; border-top: 1px solid #ccc; margin-top: 10px; padding-top: 10px;">';
            html += '<b>Wikipedia Related Area Topics:</b><ul style="padding-left: 1.2em; margin-top: 0;">';
            data.wiki_topics.forEach(topic => {
              // Changed to Wikipedia search URL
              html += `<li style="margin-bottom: 0.5em;"><a href="https://en.wikipedia.org/w/index.php?search=${encodeURIComponent(topic)}" target="_blank" rel="noopener noreferrer">${escapeHtml(topic)}</a></li>`;
            });
            html += '</ul></div>';
          }
        }
        L.popup({maxHeight: 500, maxWidth: 400, autoPanPadding: [100, 100]})
          .setLatLng(latlng)
          .setContent(html)
          .openOn(map);
        
        if (searchCircle) {
          map.removeLayer(searchCircle);
          searchCircle = null; // Reset the circle
        }
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
        };

        const citySearchPromise = fetch(`pulse.php?lat=${lat}&lon=${lon}&radius=${radius}`).then(r => r.json());
        const wikiSearchPromise = fetch(`pulse.php?geo_lookup=true&lat=${lat}&lon=${lon}`).then(r => r.json());

        Promise.all([citySearchPromise, wikiSearchPromise])
          .then(([cityData, wikiData]) => {
            const combinedData = {
                ...cityData,
                wiki_topics: wikiData.titles || []
            };
            showInfoPopup(combinedData, latlng);
          }).catch(handleFailure);
      }

      map.on('click', function(e) {
        fetchAndShowCityInfo(e.latlng);
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
      
      // Set overlays. Default to all on if 'overlays' param is not in URL.
      const initialOverlays = urlOverlays !== null 
          ? (urlOverlays === '' ? [] : urlOverlays.split(',')) 
          : ['Weather', 'Cities', 'Earthquakes'];

      initialOverlays.forEach(name => {
        if (overlays[name] && !map.hasLayer(overlays[name])) {
          overlays[name].addTo(map);
        }
      });
    </script>
  </body>
</html>
