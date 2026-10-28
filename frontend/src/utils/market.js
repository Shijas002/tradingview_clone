// Shared market helpers: timeframes, formatting, ids, chart-type transforms.

export const TIMEFRAMES = [
  { v: "M1", l: "1m", s: 60, pine: "1" },
  { v: "M5", l: "5m", s: 300, pine: "5" },
  { v: "M15", l: "15m", s: 900, pine: "15" },
  { v: "M30", l: "30m", s: 1800, pine: "30" },
  { v: "H1", l: "1h", s: 3600, pine: "60" },
  { v: "H4", l: "4h", s: 14400, pine: "240" },
  { v: "D1", l: "1D", s: 86400, pine: "D" },
  { v: "W1", l: "1W", s: 604800, pine: "W" },
  { v: "MN1", l: "1M", s: 2592000, pine: "M" },
];
export const TF = Object.fromEntries(TIMEFRAMES.map((t) => [t.v, t]));

export const CHART_TYPES = [
  { v: "candles", l: "Candles" },
  { v: "hollow", l: "Hollow candles" },
  { v: "heikin", l: "Heikin Ashi" },
  { v: "bars", l: "Bars" },
  { v: "line", l: "Line" },
  { v: "step", l: "Step line" },
  { v: "area", l: "Area" },
  { v: "baseline", l: "Baseline" },
  { v: "columns", l: "Columns" },
];

let idCounter = 0;
export function uid(prefix = "id") {
  idCounter += 1;
  return `${prefix}_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 7)}${idCounter}`;
}

export function fmtPrice(value, digits = 5) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return Number(value).toFixed(digits);
}

export function fmtNumber(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const abs = Math.abs(value);
  if (abs >= 1e9) return (value / 1e9).toFixed(2) + "B";
  if (abs >= 1e6) return (value / 1e6).toFixed(2) + "M";
  if (abs >= 1e4) return (value / 1e3).toFixed(2) + "K";
  return Number(value).toFixed(digits);
}

/** Digits to show for an indicator value of a given magnitude. */
export function autoDigits(value) {
  const abs = Math.abs(value);
  if (!Number.isFinite(abs) || abs === 0) return 2;
  if (abs >= 1000) return 2;
  if (abs >= 10) return 2;
  if (abs >= 1) return 4;
  return 5;
}

/** Heikin Ashi transform (keeps volume and time). */
export function toHeikinAshi(bars) {
  const out = [];
  let prevO = null;
  let prevC = null;
  for (const b of bars) {
    const c = (b.open + b.high + b.low + b.close) / 4;
    const o = prevO === null ? (b.open + b.close) / 2 : (prevO + prevC) / 2;
    out.push({ time: b.time, open: o, high: Math.max(b.high, o, c), low: Math.min(b.low, o, c), close: c, volume: b.volume });
    prevO = o;
    prevC = c;
  }
  return out;
}

/** Index of the last bar whose time <= t (binary search), -1 if before the first bar. */
export function bisectTime(bars, t) {
  let lo = 0;
  let hi = bars.length - 1;
  if (hi < 0 || t < bars[0].time) return -1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (bars[mid].time <= t) lo = mid;
    else hi = mid - 1;
  }
  return lo;
}

/** Continuous (fractional) bar index for a time, extrapolating outside the data. */
export function timeToLogical(bars, t, tfSeconds) {
  const n = bars.length;
  if (n === 0) return null;
  if (t <= bars[0].time) return (t - bars[0].time) / tfSeconds;
  if (t >= bars[n - 1].time) return n - 1 + (t - bars[n - 1].time) / tfSeconds;
  const i = bisectTime(bars, t);
  const t0 = bars[i].time;
  const t1 = bars[i + 1].time;
  return i + (t - t0) / (t1 - t0);
}

/** Inverse of timeToLogical. */
export function logicalToTime(bars, logical, tfSeconds) {
  const n = bars.length;
  if (n === 0) return null;
  if (logical <= 0) return Math.round(bars[0].time + logical * tfSeconds);
  if (logical >= n - 1) return Math.round(bars[n - 1].time + (logical - (n - 1)) * tfSeconds);
  const i = Math.floor(logical);
  const f = logical - i;
  return Math.round(bars[i].time + f * (bars[i + 1].time - bars[i].time));
}

export function clamp(v, lo, hi) {
  return Math.max(lo, Math.min(hi, v));
}

/** Parse "#RRGGBBAA", "#RRGGBB" or "rgba()" into {r,g,b,a}. */
export function parseColor(c) {
  if (!c || typeof c !== "string") return null;
  if (c.startsWith("#")) {
    const hex = c.slice(1);
    if (hex.length === 3) return { r: parseInt(hex[0] + hex[0], 16), g: parseInt(hex[1] + hex[1], 16), b: parseInt(hex[2] + hex[2], 16), a: 1 };
    if (hex.length === 6 || hex.length === 8) {
      return {
        r: parseInt(hex.slice(0, 2), 16),
        g: parseInt(hex.slice(2, 4), 16),
        b: parseInt(hex.slice(4, 6), 16),
        a: hex.length === 8 ? parseInt(hex.slice(6, 8), 16) / 255 : 1,
      };
    }
  }
  const m = c.match(/rgba?\(([^)]+)\)/);
  if (m) {
    const [r, g, b, a = "1"] = m[1].split(",").map((s) => s.trim());
    return { r: +r, g: +g, b: +b, a: +a };
  }
  return null;
}

export function withAlpha(color, alpha) {
  const p = parseColor(color);
  if (!p) return color;
  return `rgba(${p.r},${p.g},${p.b},${alpha})`;
}

/** Pine colors come as #RRGGBBAA; canvas understands that natively, but normalise nulls. */
export function cssColor(c, fallback = "#2962ff") {
  if (!c) return fallback;
  return c;
}
