<?php
  // Execute the python script to refresh the geojson data
  $output = shell_exec('python3 pulse.py');
  // todo: consider adding error handling here
?>
<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <title>News Study Map</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/4.7.0/css/font-awesome.min.css" />
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/Leaflet.awesome-markers/2.0.2/leaflet.awesome-markers.css" />
    <style>
      html,body,#map { height: 100%; margin: 0; padding: 0 }
      .news-popup { max-width: 360px; }
      .news-title { font-weight: 600; margin-bottom: 6px; }
      .news-source { color: #666; font-size: 90%; }
      .news-summary { margin-top: 6px; }
    </style>
  </head>
  <body>
    <div id="map"></div>
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/Leaflet.awesome-markers/2.0.2/leaflet.awesome-markers.js"></script>
    <script>
      const map = L.map('map').setView([20,0], 2);

      const baseLayers = {
        'Satellite': L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
          maxZoom: 19,
          attribution: 'Tiles &copy; Esri'
        })
      };

      const today = new Date();
      const year = today.getUTCFullYear();
      const month = String(today.getUTCMonth() + 1).padStart(2, '0');
      const day = String(today.getUTCDate()).padStart(2, '0');
      const date = `${year}-${month}-${day}`;

      const overlays = {
        'Weather': L.tileLayer(`https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/MODIS_Terra_CorrectedReflectance_TrueColor/default/${date}/GoogleMapsCompatible_Level9/{z}/{y}/{x}.jpg`, {
          attribution: '&copy; NASA GIBS',
          maxZoom: 9
        })
      };
      
      baseLayers.Satellite.addTo(map);
      L.control.layers(baseLayers, overlays).addTo(map);

      const allMarkers = [];

      fetch(`data/articles.geojson?v=${Date.now()}`).then(r=>r.json()).then(geo=>{
        if (!geo || !geo.features) return;
        geo.features.forEach(f=>{
          try {
            const coords = f.geometry && f.geometry.coordinates;
            if (!coords || coords.length < 2) return;
            const props = f.properties || {};
            const lon = coords[0], lat = coords[1];
            
            const title = props.title || props.place || 'News item';
            const place = props.place || '';
            const source = props.source || '';
            const link = props.news_link || '#';
            const summary = (props.summary || '').replace(/\n/g,' ').slice(0,800);
            const wikiTopic = props.wiki_topic || title;

            const html = `
              <div class="news-popup">
                <div class="news-title"><a href="${link}" target="_blank" rel="noopener noreferrer">${escapeHtml(title)}</a></div>
                <div class="news-source">${escapeHtml(source)} ${place?('&middot; '+escapeHtml(place)):''}</div>
                <div class="news-summary">${escapeHtml(summary)}</div>
                <div style="margin-top:6px">
                  <a href="${link}" target="_blank" rel="noopener noreferrer">Open article</a>
                  &middot;
                  <a href="https://en.wikipedia.org/w/index.php?search=${encodeURIComponent(wikiTopic)}" target="_blank" rel="noopener noreferrer">Search Wikipedia</a>
                </div>
              </div>`;

            const marker = L.marker([lat, lon], {
              icon: L.AwesomeMarkers.icon({
                icon: props.icon || 'info-circle',
                markerColor: props.markerColor || 'gray',
                prefix: 'fa'
              })
            });
            marker.bindPopup(html);
            marker.addTo(map);
            allMarkers.push(marker);
          } catch (e) { console.error('feature error', e); }
        });
        
        if (allMarkers.length > 0) {
          const group = L.featureGroup(allMarkers);
          map.fitBounds(group.getBounds().pad(0.5));
        }
      }).catch(e=>console.error(e));

      function escapeHtml(s){
        if(!s) return '';
        return String(s)
          .replace(/&/g,'&amp;')
          .replace(/</g,'&lt;')
          .replace(/>/g,'&gt;')
          .replace(/"/g,'&quot;')
          .replace(/'/g,'&#39;');
      }
    </script>
  </body>
</html>
