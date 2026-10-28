// Drawing tool catalogue: names, point counts, default styles, icons.

export const FIB_LEVELS = [
  { level: 0, color: "#787B86" },
  { level: 0.236, color: "#F23645" },
  { level: 0.382, color: "#FF9800" },
  { level: 0.5, color: "#4CAF50" },
  { level: 0.618, color: "#089981" },
  { level: 0.786, color: "#00BCD4" },
  { level: 1, color: "#787B86" },
  { level: 1.618, color: "#2962FF" },
];

// kind: how many clicks create it. "free" = press-drag-release (brush), "position" = 1 click with defaults
export const TOOLS = {
  trendline: { label: "Trend line", kind: 2, hotkey: "Alt+T", group: "lines" },
  ray: { label: "Ray", kind: 2, group: "lines" },
  extended: { label: "Extended line", kind: 2, group: "lines" },
  arrow: { label: "Arrow", kind: 2, group: "lines" },
  hline: { label: "Horizontal line", kind: 1, hotkey: "Alt+H", group: "lines" },
  hray: { label: "Horizontal ray", kind: 1, group: "lines" },
  vline: { label: "Vertical line", kind: 1, hotkey: "Alt+V", group: "lines" },
  cross: { label: "Cross line", kind: 1, group: "lines" },
  channel: { label: "Parallel channel", kind: 3, group: "channels" },
  fib: { label: "Fib retracement", kind: 2, hotkey: "Alt+F", group: "fib" },
  rect: { label: "Rectangle", kind: 2, hotkey: "Alt+R", group: "shapes" },
  brush: { label: "Brush", kind: "free", group: "shapes" },
  text: { label: "Text", kind: 1, group: "text" },
  pricerange: { label: "Price range", kind: 2, group: "measure" },
  long: { label: "Long position", kind: "position", group: "measure" },
  short: { label: "Short position", kind: "position", group: "measure" },
};

export const LINE_STYLES = [
  { v: 0, l: "Solid" },
  { v: 2, l: "Dashed" },
  { v: 1, l: "Dotted" },
];

export function defaultStyle(type) {
  const base = { color: "#2962FF", width: 2, lineStyle: 0, extendLeft: false, extendRight: false };
  switch (type) {
    case "ray":
      return { ...base, extendRight: true };
    case "extended":
      return { ...base, extendLeft: true, extendRight: true };
    case "hline":
    case "hray":
      return { ...base, color: "#F7525F", width: 1 };
    case "vline":
    case "cross":
      return { ...base, color: "#787B86", width: 1 };
    case "rect":
      return { ...base, color: "#9C27B0", width: 1, fill: "rgba(156,39,176,0.15)" };
    case "channel":
      return { ...base, color: "#2962FF", width: 1, fill: "rgba(41,98,255,0.12)" };
    case "fib":
      return { ...base, color: "#787B86", width: 1, extendRight: false, showPrices: true };
    case "text":
      return { ...base, color: "#D1D4DC", text: "Text", fontSize: 14, background: "rgba(41,98,255,0.0)" };
    case "brush":
      return { ...base, color: "#FF9800", width: 2 };
    case "pricerange":
      return { ...base, color: "#2962FF", width: 1, fill: "rgba(41,98,255,0.18)" };
    case "long":
    case "short":
      return { ...base, width: 1, qty: 1 };
    case "arrow":
      return { ...base, color: "#2962FF", width: 2 };
    default:
      return base;
  }
}

export const DASH = { 0: [], 1: [2, 3], 2: [8, 5] };
