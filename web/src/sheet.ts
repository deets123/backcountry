// The bottom sheet's three heights and how a tap or a vertical drag moves between them.
export type SheetState = "collapsed" | "half" | "full";

export function nextSheet(current: SheetState, gesture: "tap" | "up" | "down"): SheetState {
  const order: SheetState[] = ["collapsed", "half", "full"];
  const i = order.indexOf(current);
  if (gesture === "tap") return order[(i + 1) % 3]!;
  if (gesture === "up") return order[Math.min(i + 1, 2)]!;
  return order[Math.max(i - 1, 0)]!;
}
