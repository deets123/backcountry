import { FT_PER_M, M_PER_MI } from "./format";
import type { Day } from "./types";

export function profilePath(d_m: number[], z_m: number[], W: number, H: number, pad: number): string {
  const dMax = d_m[d_m.length - 1] || 1;
  const zMin = Math.min(...z_m), zMax = Math.max(...z_m);
  const span = zMax - zMin || 1;
  const x = (d: number) => pad + ((W - 2 * pad) * d) / dMax;
  const y = (z: number) => H - pad - ((H - 2 * pad) * (z - zMin)) / span;
  return d_m.map((d, i) => `${i ? "L" : "M"}${x(d).toFixed(1)},${y(z_m[i]!).toFixed(1)}`).join(" ");
}

export function profileSvg(profile: { d_m: number[]; z_m: (number | null)[] } | null, days: Day[] | null, W = 600, H = 140, markerM: number | null = null): string {
  if (!profile || profile.z_m.some((z) => z == null) || profile.d_m.length < 2) return `<p class="note">Elevation unknown for this route.</p>`;
  const z = profile.z_m as number[];
  const pad = 22;
  const path = profilePath(profile.d_m, z, W, H, pad);
  const dMax = profile.d_m[profile.d_m.length - 1]!;
  const zMin = Math.min(...z), zMax = Math.max(...z);
  const x = (d: number) => pad + ((W - 2 * pad) * d) / dMax;
  const area = `${path} L${(W - pad).toFixed(1)},${H - pad} L${pad},${H - pad} Z`;
  const camps = (days ?? []).slice(0, -1).map((d) => `<line x1="${x(d.end_m).toFixed(1)}" y1="${pad}" x2="${x(d.end_m).toFixed(1)}" y2="${H - pad}" stroke="#1b5e20" stroke-dasharray="3 3"/>`
    + `<text x="${(x(d.end_m) + 3).toFixed(1)}" y="${pad + 10}" font-size="10" fill="#1b5e20">${d.end_label}</text>`).join("");
  const ticks = [0, 0.5, 1].map((t) => {
    const mi = (dMax * t) / M_PER_MI;
    const anchor = t === 0 ? "start" : t === 1 ? "end" : "middle";
    return `<text x="${x(dMax * t).toFixed(1)}" y="${H - 6}" font-size="10" text-anchor="${anchor}" fill="#666">${mi.toFixed(1)} mi</text>`;
  }).join("");
  const fmt = (m: number) => Math.round(m * FT_PER_M).toLocaleString("en-US");
  const yl = `<text x="2" y="${pad + 4}" font-size="10" fill="#666">${fmt(zMax)} ft</text><text x="2" y="${H - pad}" font-size="10" fill="#666">${fmt(zMin)} ft</text>`;
  const you = markerM === null ? "" : `<g class="you"><line x1="${x(markerM).toFixed(1)}" y1="${pad}" x2="${x(markerM).toFixed(1)}" y2="${H - pad}" stroke="#1565c0" stroke-width="2"/><text x="${(x(markerM) + 3).toFixed(1)}" y="${H - pad - 4}" font-size="10" fill="#1565c0">you</text></g>`;
  return `<svg class="profile" viewBox="0 0 ${W} ${H}" role="img" aria-label="Elevation profile"><path d="${area}" fill="#c8e6c9"/><path d="${path}" fill="none" stroke="#2e7d32" stroke-width="1.5"/>${camps}${you}${ticks}${yl}</svg>`;
}
