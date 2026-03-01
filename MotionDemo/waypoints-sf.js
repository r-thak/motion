/**
 * 75 fixed waypoint locations in San Francisco for demo mode (no Routes API).
 * Generated with seed 42 for reproducibility.
 */
(function (global) {
  var seed = 42;
  function mulberry32() {
    var t = (seed += 0x6d2b79f5);
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  }
  var north = 37.81, south = 37.70, west = -122.52, east = -122.35;
  var out = [];
  for (var i = 0; i < 75; i++) {
    out.push({
      lat: south + mulberry32() * (north - south),
      lng: west + mulberry32() * (east - west),
    });
  }
  global.FIXED_WAYPOINTS_SF = out;
})(typeof window !== 'undefined' ? window : this);
