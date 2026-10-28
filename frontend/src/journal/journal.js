// Trade journal helpers: loading, chart capture, and pre-filled drafts.
import { getJournal, getJournalStats, getNotionStatus } from "../api/client.js";
import { controllers, lastPrice } from "../chart/registry.js";
import { resolveTimezone, sourceShiftSeconds, formatStamp } from "../chart/timezone.js";
import { useStore } from "../store/useStore.js";
import { TF } from "../utils/market.js";

export async function loadJournal() {
  try {
    const [journal, journalStats, notion] = await Promise.all([getJournal({ limit: 2000 }), getJournalStats(), getNotionStatus()]);
    useStore.getState().setJournal({ journal, journalStats, notion });
  } catch {
    /* backend offline - keep what we have */
  }
}

let timer = 0;
export function loadJournalSoon(delay = 250) {
  clearTimeout(timer);
  timer = setTimeout(loadJournal, delay);
}

/** Chart image with a small title bar (symbol, timeframe, time) as a PNG data URL. */
export function captureChart(paneId) {
  const st = useStore.getState();
  const pane = st.panes.find((p) => p.id === paneId) || st.activePane();
  const ctrl = pane && controllers.get(pane.id);
  if (!ctrl) return null;
  const shot = ctrl.screenshot();
  const header = 30;
  const canvas = document.createElement("canvas");
  canvas.width = shot.width;
  canvas.height = shot.height + header;
  const ctx = canvas.getContext("2d");
  const dark = st.settings.theme !== "light";
  ctx.fillStyle = dark ? "#131722" : "#ffffff";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(shot, 0, header);
  ctx.fillStyle = dark ? "#d1d4dc" : "#131722";
  ctx.font = "600 14px -apple-system, Segoe UI, Roboto, sans-serif";
  const tf = TF[pane.timeframe] ? TF[pane.timeframe].l : pane.timeframe;
  ctx.fillText(`${pane.symbol} · ${tf} · ${pane.provider}`, 10, 20);
  ctx.font = "12px -apple-system, Segoe UI, Roboto, sans-serif";
  ctx.textAlign = "right";
  ctx.fillText(new Date().toLocaleString(), canvas.width - 10, 20);
  let url = canvas.toDataURL("image/png");
  if (url.length > 10_000_000) url = canvas.toDataURL("image/jpeg", 0.85);
  return url;
}

const round = (v, d) => (v === null || v === undefined || Number.isNaN(Number(v)) ? null : Number(Number(v).toFixed(d)));

function chartShift(provider) {
  // MT5 / auto chart times are broker server time; the journal stores real UTC.
  if (provider !== "mt5" && provider !== "auto") return 0;
  return sourceShiftSeconds(useStore.getState().providers.find((p) => p.name === provider));
}

export function draftFromPane(pane, price = null) {
  const d = pane.digits ?? 5;
  return {
    provider: pane.provider || "",
    symbol: pane.symbol,
    timeframe: pane.timeframe || "",
    side: "long",
    status: "open",
    entry_price: round(price ?? lastPrice(pane.id), d),
    quantity: 1,
    entry_time: Math.floor(Date.now() / 1000),
    exit_price: null,
    exit_time: null,
    stop_loss: null,
    take_profit: null,
    fees: 0,
    setup: "",
    tags: [],
    rating: null,
    notes: "",
  };
}

/** Long/Short position drawing -> entry, target, stop and quantity. */
export function draftFromDrawing(pane, drawing) {
  const d = pane.digits ?? 5;
  const [entry, target, stop] = drawing.points;
  return {
    ...draftFromPane(pane, entry.price),
    side: drawing.type === "short" ? "short" : "long",
    take_profit: round(target.price, d),
    stop_loss: round(stop.price, d),
    quantity: Number(drawing.style?.qty) || 1,
    entry_time: Math.round(entry.time - chartShift(pane.provider)),
  };
}

/** Closed trade from the Strategy Tester. */
export function draftFromTrade(pane, trade, strategyName = "") {
  const d = pane.digits ?? 5;
  const shift = chartShift(pane.provider);
  return {
    ...draftFromPane(pane, trade.entryPrice),
    side: trade.size >= 0 ? "long" : "short",
    status: "closed",
    quantity: Math.abs(trade.size) || 1,
    entry_time: Math.round(trade.entryTime - shift),
    exit_time: Math.round(trade.exitTime - shift),
    exit_price: round(trade.exitPrice, d),
    setup: strategyName,
    tags: ["backtest"],
  };
}

/** Client-side P&L / R preview (same formula as the backend). */
export function previewMetrics(f) {
  const entry = Number(f.entry_price);
  const exit = Number(f.exit_price);
  if (f.status !== "closed" || !entry || !exit) return { pnl: null, r: null };
  const dir = f.side === "short" ? -1 : 1;
  const move = (exit - entry) * dir;
  const pnl = move * (Number(f.quantity) || 0) - (Number(f.fees) || 0);
  const stop = Number(f.stop_loss);
  const r = stop && Math.abs(entry - stop) > 0 ? move / Math.abs(entry - stop) : null;
  return { pnl, r };
}

/** Entry/exit time in the chart time-zone setting for the entry's provider. */
export function formatEntryTime(ts, provider) {
  if (!ts) return "—";
  const st = useStore.getState();
  const zone = resolveTimezone(st.settings.timezone || "exchange", provider, st.providers.find((p) => p.name === provider));
  return zone.tz === "SERVER" ? formatStamp(ts + (zone.sourceShift || 0), zone.tz, 0) : formatStamp(ts, zone.tz, 0);
}

export function fmtMoney(v) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const sign = v > 0 ? "+" : v < 0 ? "−" : "";
  return sign + Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
