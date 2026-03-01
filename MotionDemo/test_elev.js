const USGS_BASE = 'https://epqs.nationalmap.gov/v1/json';
function getElevation(lat, lng) {
    const url = `${USGS_BASE}?x=${lng}&y=${lat}&units=Meters`;
    return fetch(url)
      .then((r) => r.json())
      .then((data) => {
        console.log("Success for", lat, lng, "data:", data);
        const v = data.value != null ? data.value : 0;
        return v;
      })
      .catch((e) => {
        console.error("Error for", lat, lng, e);
        return 0;
      });
}

function getElevationsBatch(points) {
    return Promise.all(points.map((p) => getElevation(p.lat, p.lng)));
}

let pts = [];
for (let i=0; i<35; i++) pts.push({lat: 37.7749, lng: -122.4194});
getElevationsBatch(pts).then(console.log);
