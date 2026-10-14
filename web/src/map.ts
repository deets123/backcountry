// Owns the MapLibre instance. Draws only what the API sends.
import maplibregl, { type LngLatBoundsLike, type Map as MLMap } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { BASEMAPS, baseStyle, candidateColor, dayColor } from "./basemaps";
import type { Basemap } from "./store";
import type { Candidate, RouteFeature, TrailFeatureCollection, Trailhead } from "./types";

const TRAILS = "trails", CANDS = "candidates", TRIP = "trip", FEATS = "features", JCTS = "junctions";

export interface TripMap {
  setBasemap(b: Basemap): void;
  fitBounds(bbox: [number, number, number, number]): void;
  setTrails(fc: TrailFeatureCollection | null): void;
  setTrailheads(ths: Trailhead[], onPick: (th: Trailhead) => void): void;
  onMapTap(fn: (lon: number, lat: number) => void): void;
  setCandidates(cands: Candidate[], selected: number, onSelect: (i: number) => void): void;
  clearCandidates(): void;
  setTrip(c: Candidate | null, popupHtml?: (f: RouteFeature) => string): void;
  setJunctions(c: Candidate | null, show: boolean): void;
  destroy(): void;
}

function removeAll(map: MLMap, prefix: string) {
  for (const l of map.getStyle().layers ?? []) if (l.id.startsWith(prefix)) map.removeLayer(l.id);
  for (const id of Object.keys(map.getStyle().sources ?? {})) if (id.startsWith(prefix)) map.removeSource(id);
}

export function createMap(container: HTMLElement, basemap: Basemap, center: [number, number], zoom: number): TripMap {
  const map = new maplibregl.Map({ container, style: baseStyle(basemap), center, zoom, attributionControl: { compact: false } });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
  const ready = new Promise<void>((resolve) => map.once("load", () => resolve()));
  const thMarkers: maplibregl.Marker[] = [];
  const tripMarkers: maplibregl.Marker[] = [];
  const popups: maplibregl.Popup[] = [];
  let tapHandler: ((lon: number, lat: number) => void) | null = null;
  map.on("click", (e) => {
    if ((e.originalEvent.target as HTMLElement)?.closest(".marker")) return;
    if (map.queryRenderedFeatures(e.point, { layers: [`${CANDS}-dim`, `${FEATS}-dots`].filter((l) => map.getLayer(l)) }).length) return;
    tapHandler?.(e.lngLat.lng, e.lngLat.lat);
  });

  function badge(text: string, color: string, title: string, onClick?: () => void): HTMLElement {
    const el = document.createElement("div");
    el.className = "marker";
    el.textContent = text;
    el.title = title;
    el.style.cssText = `background:${color};color:#fff;font:700 11px/18px sans-serif;padding:0 6px;border-radius:4px;border:1px solid #fff;box-shadow:0 0 2px rgba(0,0,0,.6);cursor:pointer`;
    if (onClick) el.addEventListener("click", (ev) => { ev.stopPropagation(); onClick(); });
    return el;
  }

  function clear(list: maplibregl.Marker[]) { list.splice(0).forEach((m) => m.remove()); }

  const api: TripMap = {
    setBasemap(b) {
      ready.then(() => { for (const key of Object.keys(BASEMAPS)) map.setLayoutProperty(`base-${key}`, "visibility", key === b ? "visible" : "none"); });
    },
    fitBounds(bbox) { map.fitBounds(bbox as LngLatBoundsLike, { padding: 40, duration: 0 }); },
    setTrails(fc) {
      ready.then(() => {
        removeAll(map, TRAILS);
        if (!fc) return;
        map.addSource(TRAILS, { type: "geojson", data: fc });
        map.addLayer({ id: `${TRAILS}-road`, type: "line", source: TRAILS, filter: ["==", ["get", "is_road"], true],
          paint: { "line-color": "#6d4c41", "line-width": 1.5, "line-dasharray": [3, 2], "line-opacity": 0.7 } });
        map.addLayer({ id: `${TRAILS}-trail`, type: "line", source: TRAILS, filter: ["==", ["get", "is_road"], false],
          paint: { "line-color": "#5d4037", "line-width": 1.5, "line-opacity": 0.8 } });
      });
    },
    setTrailheads(ths, onPick) {
      ready.then(() => {
        clear(thMarkers);
        for (const th of ths) {
          if (th.lat == null || th.lon == null || !th.in_graph) continue;
          const el = badge(th.source === "yaml" ? "TH" : "th", th.source === "yaml" ? "#1b5e20" : "#78909c",
            `${th.name ?? th.id}${th.verified ? "" : " (unverified)"}`, () => onPick(th));
          thMarkers.push(new maplibregl.Marker({ element: el }).setLngLat([th.lon, th.lat]).addTo(map));
        }
      });
    },
    onMapTap(fn) { tapHandler = fn; },
    setCandidates(cands, selected, onSelect) {
      ready.then(() => {
        removeAll(map, CANDS);
        if (!cands.length) return;
        map.addSource(CANDS, { type: "geojson", data: { type: "FeatureCollection", features: cands.map((c) => ({
          type: "Feature", geometry: c.geometry, properties: { index: c.index, color: candidateColor(c.index) } })) } });
        map.addLayer({ id: `${CANDS}-dim`, type: "line", source: CANDS, filter: ["!=", ["get", "index"], selected],
          paint: { "line-color": ["get", "color"], "line-width": 4, "line-opacity": 0.35 } });
        map.addLayer({ id: `${CANDS}-sel`, type: "line", source: CANDS, filter: ["==", ["get", "index"], selected],
          paint: { "line-color": ["get", "color"], "line-width": 6, "line-opacity": 0.95 } });
        map.on("click", `${CANDS}-dim`, (e) => { const i = e.features?.[0]?.properties?.index; if (typeof i === "number") onSelect(i); });
      });
    },
    clearCandidates() { ready.then(() => removeAll(map, CANDS)); },
    setTrip(c, popupHtml) {
      ready.then(() => {
        removeAll(map, TRIP); removeAll(map, FEATS); removeAll(map, JCTS);
        clear(tripMarkers); popups.splice(0).forEach((p) => p.remove());
        if (!c) return;
        map.addSource(TRIP, { type: "geojson", data: { type: "FeatureCollection", features: c.pieces.map((p, i) => ({
          type: "Feature", geometry: { type: "LineString", coordinates: p.points.map(([lon, lat]) => [lon, lat]) },
          properties: { color: dayColor(i + 1), name: p.name } })) } });
        map.addLayer({ id: `${TRIP}-line`, type: "line", source: TRIP, paint: { "line-color": ["get", "color"], "line-width": 5, "line-opacity": 0.9 } });
        const w0 = c.waypoints[0], w1 = c.waypoints[1];
        const isLoop = !!(w0 && w1 && w0.lat === w1.lat && w0.lon === w1.lon);
        for (const w of c.waypoints) {
          if (w.kind === "junction" || (isLoop && w.kind === "end")) continue;
          const text = w.kind === "start" ? (isLoop ? "S/E" : "S") : w.kind === "end" ? "E" : `C${w.name.split(" ")[1]}`;
          tripMarkers.push(new maplibregl.Marker({ element: badge(text, "#1b5e20", `${w.name}: ${w.desc}`) }).setLngLat([w.lon, w.lat]).addTo(map));
        }
        if (c.features.length) {
          map.addSource(FEATS, { type: "geojson", data: { type: "FeatureCollection", features: c.features.map((f, i) => ({
            type: "Feature", geometry: { type: "Point", coordinates: [f.lon, f.lat] },
            properties: { i, camp: f.kind === "camp_site" || f.kind === "camp_pitch" || f.kind.startsWith("usfs") } })) } });
          map.addLayer({ id: `${FEATS}-dots`, type: "circle", source: FEATS, paint: {
            "circle-radius": 5, "circle-color": ["case", ["get", "camp"], "#8d5524", "#1565c0"], "circle-stroke-color": "#fff", "circle-stroke-width": 1 } });
          map.on("click", `${FEATS}-dots`, (e) => {
            const i = e.features?.[0]?.properties?.i; const f = typeof i === "number" ? c.features[i] : undefined;
            if (!f || !popupHtml) return;
            popups.push(new maplibregl.Popup({ maxWidth: "340px" }).setLngLat([f.lon, f.lat]).setHTML(popupHtml(f)).addTo(map));
          });
        }
      });
    },
    setJunctions(c, show) {
      ready.then(() => {
        removeAll(map, JCTS);
        if (!c || !show) return;
        const jcts = c.waypoints.filter((w) => w.kind === "junction");
        if (!jcts.length) return;
        map.addSource(JCTS, { type: "geojson", data: { type: "FeatureCollection", features: jcts.map((w) => ({
          type: "Feature", geometry: { type: "Point", coordinates: [w.lon, w.lat] }, properties: { name: w.name } })) } });
        map.addLayer({ id: `${JCTS}-dots`, type: "circle", source: JCTS, paint: { "circle-radius": 4, "circle-color": "#333", "circle-stroke-color": "#fff", "circle-stroke-width": 1 } });
        map.addLayer({ id: `${JCTS}-labels`, type: "symbol", source: JCTS, layout: { "text-field": ["get", "name"], "text-size": 10, "text-offset": [0, 1.1], "text-anchor": "top" },
          paint: { "text-color": "#333", "text-halo-color": "#fff", "text-halo-width": 1 } });
      });
    },
    destroy() { clear(thMarkers); clear(tripMarkers); popups.splice(0).forEach((p) => p.remove()); map.remove(); },
  };
  return api;
}
