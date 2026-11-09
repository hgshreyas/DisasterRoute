// Minimal stand-in for Leaflet, used ONLY by tests/frontend_smoke.py (the sandbox cannot fetch the real
// library). It records what the page asks the map to draw so the test can check it; it renders nothing.
(function () {
  class Layer {
    addTo(t) { t.addLayer(this); return this; }
    bindTooltip(t) { this.tip = t; return this; }
    bindPopup(p) { this.popup = p; return this; }
  }
  class Group extends Layer {
    constructor() { super(); this.items = []; }
    addLayer(l) { this.items.push(l); return this; }
    clearLayers() { this.items = []; return this; }
  }
  const L = {};
  L.map = () => {
    const m = {handlers: {}, items: [],
      setView(c, z) { m.view = [c, z]; return m; },
      on(ev, fn) { m.handlers[ev] = fn; return m; },
      addLayer(l) { m.items.push(l); return m; },
      removeLayer(l) { m.items = m.items.filter(x => x !== l); return m; },
      fitBounds(b) { m.fit = b; return m; }};
    window.__map = m;
    return m;
  };
  L.tileLayer = (url) => { const l = new Layer(); l.url = url; return l; };
  L.control = {layers: () => ({addTo() { return this; }})};
  L.layerGroup = () => new Group();
  L.divIcon = (o) => o;
  L.marker = (ll, o) => { const l = new Layer(); l.ll = ll; l.opts = o; return l; };
  L.circleMarker = (ll, o) => { const l = new Layer(); l.ll = ll; l.style = o; return l; };
  L.polyline = (ll, style) => { const l = new Layer(); l.ll = ll; l.style = style; return l; };
  L.geoJSON = (data, opts) => {
    const g = new Layer(); g.data = data;
    for (const f of data.features) {
      if (opts.style) g.lastStyle = opts.style(f);          // run the page's callbacks to catch errors
      if (opts.onEachFeature) opts.onEachFeature(f, new Layer());
    }
    return g;
  };
  window.L = L;
})();
