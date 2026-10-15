export const M_PER_MI = 1609.344;
export const FT_PER_M = 3.28084;

function isNum(x: unknown): x is number {
  return typeof x === "number" && Number.isFinite(x);
}

export function miles(m: number | null | undefined): string {
  return isNum(m) ? `${(m / M_PER_MI).toFixed(2)} mi` : "unknown";
}

export function feet(ft: number | null | undefined): string {
  return isNum(ft) ? `${Math.round(ft).toLocaleString("en-US")} ft` : "unknown";
}

export function feetFromMeters(m: number | null | undefined): string {
  return isNum(m) ? feet(m * FT_PER_M) : "unknown";
}

export function signedFeet(gain: number | null | undefined, loss: number | null | undefined): string {
  return isNum(gain) && isNum(loss)
    ? `+${Math.round(gain).toLocaleString("en-US")} / −${Math.round(loss).toLocaleString("en-US")} ft`
    : "unknown";
}

export function shortDate(iso: string | null | undefined): string {
  if (!iso) return "unknown";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "unknown" : d.toLocaleDateString("en-US", { timeZone: "UTC", month: "short", day: "numeric", year: "numeric" });
}
