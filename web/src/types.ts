// The piece-1 API's JSON shapes, field names as the API sends them.
export type SearchResult =
  | { kind: "region"; id: string; name: string; type_label: string; state: null }
  | { kind: "area" | "place"; ref: string; name: string; type_label: string; state: string | null;
      bbox: [number, number, number, number]; center: [number, number] };
export interface RecentArea { id: string; name: string; type_label: string | null; state: string | null; bbox: Region["bbox"] }
export interface AreaStatus {
  status: "ready" | "preparing" | "queued" | "trails" | "failed" | "unknown"; message: string | null; name?: string; bbox?: Region["bbox"];
}
export interface AreaOpened { area_id: string; name: string; status: AreaStatus["status"]; message: string; bbox: Region["bbox"] }

export interface Region {
  id: string; name: string; crs: string;
  bbox: { south: number; west: number; north: number; east: number };
  osm_retrieved_at: string | null; dem: boolean; usfs: boolean; trailheads: number;
}

export interface Trailhead {
  id: string; name: string | null; node: number | null; source: "yaml" | "data"; verified: boolean;
  in_graph: boolean; lat: number | null; lon: number | null; component: string | null;
  usfs?: string | null; note?: string | null; kinds?: string[]; snap_m?: number;
}

export interface TrailProps {
  name: string | null; highway: string; is_road: boolean; last_resort: boolean; source: string;
  length_m: number; component: string; key: string;
}
export interface TrailFeatureCollection {
  type: "FeatureCollection"; attribution: string;
  features: { type: "Feature"; geometry: { type: "LineString"; coordinates: [number, number][] }; properties: TrailProps }[];
}

export interface MapFeature {
  source: string; kind: string; name: string | null; lat: number; lon: number;
  tags: Record<string, string>; meta: { timestamp: string | null; version: number | null }; url: string | null;
}
export interface FeaturesInView {
  camp_sites: MapFeature[]; water_points: MapFeature[];
  streams: { source: string; kind: string; name: string | null; coords: [number, number][]; url: string | null }[];
}

export interface GenerateRequest {
  trailhead: string; total: string | [number, number]; nights?: number | null; daily?: string | [number, number] | null;
  max_gain_day?: number | null; type?: "loop" | "lollipop" | "any"; count?: number; no_roads?: boolean;
  road_weight?: number; repeat_weight?: number; camps?: string[]; camps_at_sites?: boolean;
  must_visit?: string[]; avoid?: string[]; direction?: "cw" | "ccw" | null;
  days?: DayTarget[]; level?: Level | null;
}

export type Level = "easy" | "moderate" | "hard" | "strenuous";
export interface DayTarget { day: number; level?: Level | null; miles?: [number, number] | null; max_gain_ft?: number | null; max_grade_pct?: number | null }
export interface Steep { grade_pct: number; start_m: number; end_m: number }
export interface TerrainInterval { start_m: number; end_m: number; reason: string | null; sources: string[] }
export interface Rating {
  level: Level | null; reasons: string[]; effort: number | null; steep: Steep | null;
  tagged: TerrainInterval[]; untagged_share: number;
}

export interface Segment { label: string; highway: string; is_road: boolean; last_resort: boolean; length_m: number; sources: string[] }
export interface Piece { name: string; points: [number, number, number | null][]; start_m: number; end_m: number; length_m: number; gain_ft: number | null; loss_ft: number | null; day: number | null }
export interface Waypoint { name: string; desc: string; lat: number; lon: number; dist_m: number; ele: number | null; kind: "start" | "end" | "camp" | "junction" }
export interface Day { index: number; start_m: number; end_m: number; length_m: number; gain_ft: number | null; loss_ft: number | null; start_label: string; end_label: string; rating?: Rating | null }
export interface NearbySite { source: string; kind: string; name: string | null; url?: string | null; distance_m: number }
export interface Camp {
  index: number; dist_m: number; lat: number; lon: number; on_source: string; on_name: string | null; nearby: NearbySite[];
  note: string; pinned: boolean; site: string | null;
  nearest_water: { source: string; kind: string; name: string | null; distance_m: number } | null;
  moved?: boolean;
}
export interface Plan {
  nights: number; daily_min_m: number; daily_max_m: number; max_gain_ft: number | null; days: Day[]; camps: Camp[];
  ok: boolean; violations: string[]; notes: string[]; penalty: number;
}
/** One re-checked setting change that clears every miss (planning.suggest_fix). */
export interface Suggestion { text: string; days: DayTarget[] }
export interface RouteFeature extends MapFeature { dist_m: number; off_m: number; day?: number | null }
export interface Candidate {
  index: number; kind: "loop" | "lollipop"; length_m: number; gain_ft: number | null; loss_ft: number | null; road_m: number;
  last_resort_m: number; repeated_m: number; penalty: number; segments: Segment[];
  geometry: { type: "LineString"; coordinates: [number, number][] }; pieces: Piece[]; waypoints: Waypoint[];
  route?: { nodes: number[]; keys: string[] };  // OSM nodes and graph edges walked: to follow it on newer data
  plan: Plan | null; features: RouteFeature[]; profile: { d_m: number[]; z_m: (number | null)[] } | null;
  title: string; description: string; notes: string[];
  /** Runs saved before levels existed have none of these. */
  level?: Level | null; rating?: Rating | null; terrain?: TerrainInterval[]; suggestion?: Suggestion | null;
}
export interface Run {
  run_id: string; region: string; request: GenerateRequest & { total: [number, number]; daily: [number, number] | null };
  generated_at: string; start: { node: number; how: string; snap_m: number };
  search: { loops_found: number; lollipops_found: number; stems_tried: number; via_pairs_tried: number; scored: number;
    seconds: number; widened: boolean; truncated: boolean; avoided_edges: number; notes: string[]; must_visit_notes: string[] };
  candidates: Candidate[];
  data_sources: { name: string; retrieved_at: string | null; attribution: string }[];
  data_cut?: string | null;  // generated areas: the extract date the trip was planned on
  known_gaps: string[]; diff: string | null;
  difficulty_rules?: DifficultyRules;
  partial?: boolean;  // the first trips found while the search goes on (not saved)
}
export interface PlanJob { status: "searching" | "done" | "failed"; run: Run | null; message?: string | null; field?: string | null }
export interface DifficultyRules {
  version: number;
  scales: Record<"day_hike" | "backpacking", { cutoffs: [number, number, number] }>;
  steep: { window_m: number; tag_pct: number; raise_pct: number };
  terrain: { min_m: number; sac_scale: Record<string, string>; trail_visibility: Record<string, string> };
}
export interface ApiErrorBody { error: string; field?: string }

export interface ChatProposal { request: GenerateRequest; changes: string[] }
export interface ChatAnswer { reply: string; kind: "change" | "explain" | "unclear"; proposal: ChatProposal | null; note: string | null; withheld: string | null; model: string }
export interface ChatMessage { role: "user" | "assistant"; text: string; proposal?: ChatProposal | null; note?: string | null; withheld?: string | null; status?: "open" | "ran" | "dismissed"; runId?: string }
