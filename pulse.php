<?php
  if (isset($_GET['proxy_noaa'])) {
    $params = $_GET;
    unset($params['proxy_noaa']);
    $queryString = http_build_query($params);
    
    $url = "https://nowcoast.noaa.gov/arcgis/services/nowcoast/analysis_meteohydro_sfc_rtma_time/MapServer/WMSServer?" . $queryString;

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
?>
<!doctype html>
<html>
  <head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Pulse</title>
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
    </style>
  </head>
  <body>
    <div id="map"></div>
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/Leaflet.awesome-markers/2.0.2/leaflet.awesome-markers.js"></script>
    <script>
      const
        'Satellite': L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
          maxZoom: 19,
          attribution: 'Tiles &copy; Esri'
        })
      };

      const today = new Date();
      const yesterday = new Date(today);
      yesterday.setUTCDate(today.getUTCDate() - 1);
      const year = yesterday.getUTCFullYear();
      const month = String(yesterday.getUTCMonth() + 1).padStart(2, '0');
      const day = String(yesterday.getUTCDate()).padStart(2, '0');
      const date = `${year}-${month}-${day}`;

      const displayDate = yesterday.toLocaleDateString('en-US', { 
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

      const overlays = {
        'Weather': L.tileLayer(`https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/MODIS_Terra_CorrectedReflectance_TrueColor/default/${date}/GoogleMapsCompatible_Level9/{z}/{y}/{x}.jpg`, {
          attribution: '&copy; NASA GIBS',
          maxZoom: 9
        }),
        'Pressure': L.tileLayer.wms('pulse.php', {
          layers: '10',
          format: 'image/png',
          transparent: true,
          attribution: 'NOAA/NWS',
          // Custom parameter to trigger our proxy
          proxy_noaa: true
        })
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

      baseLayers.Satellite.addTo(map);
      overlays['Weather'].addTo(map);
      L.control.layers(baseLayers, overlays).addTo(map);

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
          L.circleMarker([city.lat, city.lng], {
            radius: 3,
            fillColor: "#ff7800",
            color: "#000",
            weight: 1,
            opacity: 1,
            fillOpacity: 0.8
          }).addTo(map);
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

            map.on('contextmenu', (e) => {
        // Allow the default context menu to appear
      });

      map.on('click', function(e) {
        const lat = e.latlng.lat;
        const lon = e.latlng.lng;

        if (searchCircle) {
          map.removeLayer(searchCircle);
        }

        const radius = 200; // 200km fixed radius

        searchCircle = L.circle(e.latlng, {
          radius: radius * 1000,
          color: 'blue',
          fillColor: '#33f',
          fillOpacity: 0.2
        }).addTo(map);

        fetch(`pulse.php?lat=${lat}&lon=${lon}&radius=${radius}`)
          .then(r => r.json())
          .then(data => {
            let html = 'No cities found in this area. Try increasing the search radius.';
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

            if (data && (data.nearest_city || data.other_cities.length > 0)) {
              html = '';
              if (data.nearest_city) {
                const city = data.nearest_city;
                const search_query = `${city.name}, ${city.state}, ${city.country}`;
                html += `<b>Nearest City:</b> <a href="https://en.wikipedia.org/w/index.php?search=${encodeURIComponent(search_query)}" target="_blank" rel="noopener noreferrer">${escapeHtml(city.name)}</a> <a href="https://news.google.com/search?q=${encodeURIComponent(search_query)}" target="_blank" rel="noopener noreferrer">(news)</a><hr>`;
                
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
              if (data.other_cities.length > 0) {
                html += '<div id="other-cities" style="max-height: 150px; overflow-y: auto; border-top: 1px solid #ccc; margin-top: 10px; padding-top: 10px;">';
                html += '<b>Other cities in area:</b><ul style="padding-left: 1.2em; margin-top: 0;">';
                data.other_cities.forEach(city => {
                  const search_query = `${city.name}, ${city.state}, ${city.country}`;
                  html += `<li style="margin-bottom: 0.5em;"><a href="https://en.wikipedia.org/w/index.php?search=${encodeURIComponent(search_query)}" target="_blank" rel="noopener noreferrer">${escapeHtml(city.name)}</a> <a href="https://news.google.com/search?q=${encodeURIComponent(search_query)}" target="_blank" rel="noopener noreferrer">(news)</a></li>`;
                });
                html += '</ul></div>';
              }
            }
            L.popup({maxHeight: 500, maxWidth: 400})
              .setLatLng(e.latlng)
              .setContent(html)
              .openOn(map);
            
            if (searchCircle) {
              map.removeLayer(searchCircle);
              searchCircle = null; // Reset the circle
            }
          }).catch(e=>{
            console.error('city lookup error', e);
            if (searchCircle) {
              map.removeLayer(searchCircle);
              searchCircle = null;
            }
          });
      });
    </script>
  </body>
</html>
