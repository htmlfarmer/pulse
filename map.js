// ...existing code to initialize your Leaflet map as 'map'...

fetch('data/current_events.geojson')
  .then(response => response.json())
  .then(geojsonData => {
    L.geoJSON(geojsonData, {
      onEachFeature: function (feature, layer) {
        const p = feature.properties;
        let popupHtml = `
          <strong><a href="${p.url}" target="_blank">${p.title}</a></strong><br>
          <div style="max-height:100px;overflow:auto;">
            <em>${p.summary}</em>
          </div>
          <hr>
          <div style="max-height:80px;overflow:auto;">
            <b>Top headlines:</b>
            <ul style="margin:0;padding-left:18px;">
              ${Array.isArray(p.headlines_urls) ? p.headlines_urls.map(h => `<li><a href="${h.url}" target="_blank">${h.title}</a></li>`).join('') : ''}
            </ul>
          </div>
        `;
        layer.bindPopup(popupHtml);
      },
      pointToLayer: function (feature, latlng) {
        return L.marker(latlng, {icon: L.icon({iconUrl: 'marker-icon.png'})}); // customize icon if needed
      }
    }).addTo(map);
  });
