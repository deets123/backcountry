// Owns the MapLibre instance. Draws only what the API sends.
import maplibregl, { type GeoJSONSource, type GeoJSONSourceSpecification, type LngLatBoundsLike, type Map as MLMap } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { BASEMAPS, baseStyle, candidateColor, dayColor } from "./basemaps";
import type { Fix } from "./gps";
import type { Box } from "./offline/tiles";
import type { Basemap } from "./store";
import type { Candidate, RouteFeature, TrailFeatureCollection, Trailhead } from "./types";

const TRAILS = "trails", STARTS = "starts", CANDS = "candidates", TRIP = "trip", FEATS = "features", JCTS = "junctions", POS = "position";

export interface TripMap {
  setBasemap(b: Basemap): void;
  fitBounds(bbox: [number, number, number, number]): void;
  setTrails(fc: TrailFeatureCollection | null): void;
  setTrailheads(ths: Trailhead[], onPick: (th: Trailhead) => void): void;
  onMapTap(fn: (lon: number, lat: number, trailName: string | null) => void): void;
  setCandidates(cands: Candidate[], selected: number, onSelect: (i: number) => void): void;
  clearCandidates(): void;
  setTrip(c: Candidate | null, popupHtml?: (f: RouteFeature) => string): void;
  setJunctions(c: Candidate | null, show: boolean): void;
  setPosition(fix: Fix | null): void;
  setCampDrag(onDrop: ((index: number, lon: number, lat: number) => void) | null): void;
  onMove(fn: (view: Box, zoom: number) => void): void;
  project(lon: number, lat: number): [number, number];
  destroy(): void;
}

function removeAll(map: MLMap, prefix: string) {
  for (const l of map.getStyle().layers ?? []) if (l.id.startsWith(prefix)) map.removeLayer(l.id);
  for (const id of Object.keys(map.getStyle().sources ?? {})) if (id.startsWith(prefix)) map.removeSource(id);
}

// The accuracy circle around a GPS fix, as a polygon (48 points, local flat approximation).
function circle(lon: number, lat: number, radiusM: number): [number, number][] {
  const dLat = radiusM / 111195.08, dLon = dLat / Math.cos((lat * Math.PI) / 180);
  return Array.from({ length: 49 }, (_, i) => {
    const a = (i / 48) * 2 * Math.PI;
    return [lon + dLon * Math.cos(a), lat + dLat * Math.sin(a)] as [number, number];
  });
}

export function createMap(container: HTMLElement, basemap: Basemap, center: [number, number], zoom: number, basemapSelect?: HTMLElement): TripMap {
  const map = new maplibregl.Map({ container, style: baseStyle(basemap), center, zoom, attributionControl: false });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
  if (basemapSelect) {  // the app's base-map <select>, as a map control so it sits with the map's other controls
    const wrap = document.createElement("div");
    wrap.className = "maplibregl-ctrl basemap-ctrl";
    wrap.appendChild(basemapSelect);
    map.addControl({ onAdd: () => wrap, onRemove: () => wrap.remove() }, "top-left");
  }
  map.addControl(new maplibregl.AttributionControl(), "top-left");  // top-left: never under the bottom sheet
  const ready = new Promise<void>((resolve) => map.once("load", () => resolve()));
  const thMarkers: maplibregl.Marker[] = [];
  const tripMarkers: maplibregl.Marker[] = [];
  const popups: maplibregl.Popup[] = [];
  // What the delegated click handlers act on. They are registered once, below: MapLibre keeps a delegate
  // keyed by layer id even after removeLayer, so registering per setTrip/setCandidates would fire every
  // earlier handler again once a layer with the same id is re-added.
  let tapHandler: ((lon: number, lat: number, trailName: string | null) => void) | null = null;
  let onPickStart: ((th: Trailhead) => void) | null = null;
  let onSelectCandidate: ((i: number) => void) | null = null;
  let trip: { c: Candidate; popupHtml?: (f: RouteFeature) => string } | null = null;
  let campDrop: ((index: number, lon: number, lat: number) => void) | null = null;
  let moveHandler: ((view: Box, zoom: number) => void) | null = null;
  map.on("moveend", () => {
    const b = map.getBounds();
    moveHandler?.([b.getWest(), b.getSouth(), b.getEast(), b.getNorth()], map.getZoom());
  });
  let starts: Trailhead[] = [];
  const DELEGATED = [`${STARTS}-dots`, `${CANDS}-dim`, `${FEATS}-dots`];
  map.on("click", (e) => {
    if ((e.originalEvent.target as HTMLElement)?.closest(".marker")) return;
    if (map.queryRenderedFeatures(e.point, { layers: DELEGATED.filter((l) => map.getLayer(l)) }).length) return;
    const pad = 6;  // a thumb-sized box around the tap, to name the trail it landed on
    const box: [maplibregl.PointLike, maplibregl.PointLike] = [[e.point.x - pad, e.point.y - pad], [e.point.x + pad, e.point.y + pad]];
    const hit = map.getLayer(`${TRAILS}-trail`) ? map.queryRenderedFeatures(box, { layers: [`${TRAILS}-trail`, `${TRAILS}-road`] }) : [];
    const name = hit.find((f) => f.properties?.name)?.properties?.name;
    tapHandler?.(e.lngLat.lng, e.lngLat.lat, typeof name === "string" ? name : null);
  });
  map.on("click", `${STARTS}-dots`, (e) => {
    const i = e.features?.[0]?.properties?.i; const th = typeof i === "number" ? starts[i] : undefined;
    if (th) onPickStart?.(th);
  });
  map.on("click", `${CANDS}-dim`, (e) => { const i = e.features?.[0]?.properties?.index; if (typeof i === "number") onSelectCandidate?.(i); });
  map.on("click", `${FEATS}-dots`, (e) => {
    const i = e.features?.[0]?.properties?.i; const f = typeof i === "number" ? trip?.c.features[i] : undefined;
    if (!f || !trip?.popupHtml) return;
    popups.push(new maplibregl.Popup({ maxWidth: "340px" }).setLngLat([f.lon, f.lat]).setHTML(trip.popupHtml(f)).addTo(map));
  });
  for (const id of DELEGATED) {
    map.on("mouseenter", id, () => { map.getCanvas().style.cursor = "pointer"; });
    map.on("mouseleave", id, () => { map.getCanvas().style.cursor = ""; });
  }

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
        clear(thMarkers); removeAll(map, STARTS);
        onPickStart = onPick;
        const usable = ths.filter((th) => th.lat != null && th.lon != null && th.in_graph);
        for (const th of usable.filter((t) => t.source === "yaml")) {
          const el = badge("TH", "#1b5e20", `${th.name ?? th.id}${th.verified ? "" : " (unverified)"}`, () => onPick(th));
          thMarkers.push(new maplibregl.Marker({ element: el }).setLngLat([th.lon!, th.lat!]).addTo(map));
        }
        starts = usable.filter((t) => t.source !== "yaml");
        if (!starts.length) return;
        map.addSource(STARTS, { type: "geojson", data: { type: "FeatureCollection", features: starts.map((th, i) => ({
          type: "Feature", geometry: { type: "Point", coordinates: [th.lon!, th.lat!] }, properties: { i, name: th.name ?? th.id } })) } });
        map.addLayer({ id: `${STARTS}-dots`, type: "circle", source: STARTS, paint: {
          "circle-radius": 5, "circle-color": "#78909c", "circle-stroke-color": "#fff", "circle-stroke-width": 1.5 } });
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
        onSelectCandidate = onSelect;
      });
    },
    clearCandidates() { ready.then(() => removeAll(map, CANDS)); },
    setTrip(c, popupHtml) {
      ready.then(() => {
        removeAll(map, TRIP); removeAll(map, FEATS); removeAll(map, JCTS);
        clear(tripMarkers); popups.splice(0).forEach((p) => p.remove());
        trip = c ? { c, popupHtml } : null;
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
          const draggable = w.kind === "camp" && campDrop !== null;
          const m = new maplibregl.Marker({ element: badge(text, "#1b5e20", `${w.name}: ${w.desc}`), draggable }).setLngLat([w.lon, w.lat]).addTo(map);
          if (draggable) {
            const index = Number(w.name.split(" ")[1]);
            m.on("dragend", () => { const p = m.getLngLat(); campDrop?.(index, p.lng, p.lat); });
          }
          tripMarkers.push(m);
        }
        if (c.features.length) {
          map.addSource(FEATS, { type: "geojson", data: { type: "FeatureCollection", features: c.features.map((f, i) => ({
            type: "Feature", geometry: { type: "Point", coordinates: [f.lon, f.lat] },
            properties: { i, camp: f.kind === "camp_site" || f.kind === "camp_pitch" || f.kind.startsWith("usfs") } })) } });
          map.addLayer({ id: `${FEATS}-dots`, type: "circle", source: FEATS, paint: {
            "circle-radius": 5, "circle-color": ["case", ["get", "camp"], "#8d5524", "#1565c0"], "circle-stroke-color": "#fff", "circle-stroke-width": 1 } });
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
    setPosition(fix) {
      ready.then(() => {
        if (!fix) { removeAll(map, POS); return; }
        const data: Extract<GeoJSONSourceSpecification["data"], { type: "FeatureCollection" }> = { type: "FeatureCollection", features: [
          { type: "Feature", geometry: { type: "Polygon", coordinates: [circle(fix.lon, fix.lat, fix.accuracyM)] }, properties: {} },
          { type: "Feature", geometry: { type: "Point", coordinates: [fix.lon, fix.lat] }, properties: {} }] };
        const src = map.getSource(POS) as GeoJSONSource | undefined;
        if (src) { src.setData(data); return; }
        map.addSource(POS, { type: "geojson", data });
        map.addLayer({ id: `${POS}-accuracy`, type: "fill", source: POS, filter: ["==", ["geometry-type"], "Polygon"], paint: { "fill-color": "#1565c0", "fill-opacity": 0.15 } });
        map.addLayer({ id: `${POS}-dot`, type: "circle", source: POS, filter: ["==", ["geometry-type"], "Point"],
          paint: { "circle-radius": 7, "circle-color": "#1565c0", "circle-stroke-color": "#fff", "circle-stroke-width": 2 } });
      });
    },
    setCampDrag(onDrop) { campDrop = onDrop; },
    onMove(fn) { moveHandler = fn; },
    project(lon, lat) { const p = map.project([lon, lat]); return [p.x, p.y]; },
    destroy() { clear(thMarkers); clear(tripMarkers); popups.splice(0).forEach((p) => p.remove()); map.remove(); },
  };
  return api;
}
