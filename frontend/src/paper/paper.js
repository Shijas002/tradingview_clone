// Paper trading client helpers (v1.1).
import { getPaperAccount, modifyPaperOrder, modifyPaperPosition } from "../api/client.js";
import { useStore } from "../store/useStore.js";

export async function loadPaper() {
  try {
    const paper = await getPaperAccount();
    useStore.getState().setPaper(paper);
    return paper;
  } catch {
    return null;
  }
}

let timer = 0;
export function loadPaperSoon(delay = 150) {
  clearTimeout(timer);
  timer = setTimeout(loadPaper, delay);
}

/** Live P&L push from the backend (paper_pnl) merged into the account snapshot. */
export function applyPaperPnl(data) {
  const st = useStore.getState();
  const p = st.paper;
  if (!p || !data) return;
  const live = new Map((data.positions || []).map((x) => [x.id, x]));
  st.setPaper({
    ...p,
    equity: data.equity,
    unrealized_pnl: data.unrealized_pnl,
    balance: data.balance,
    positions: p.positions.map((pos) => (live.has(pos.id) ? { ...pos, ...live.get(pos.id) } : pos)),
  });
}

const sameSymbol = (a, b) => String(a || "").toLowerCase() === String(b || "").toLowerCase();

export function fmtPnl(v, digits = 2) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

/** Lines the chart should draw for this pane: position entry, SL/TP and working orders. */
export function tradeLinesFor(pane, paper) {
  if (!paper || !pane || !pane.provider) return [];
  const lines = [];
  for (const p of paper.positions || []) {
    if (p.provider !== pane.provider || !sameSymbol(p.symbol, pane.symbol)) continue;
    const long = p.side === "long";
    lines.push({
      id: `pos:${p.id}`, kind: "position", price: p.avg_price, color: long ? "#2962FF" : "#F23645", draggable: false,
      title: `${long ? "LONG" : "SHORT"} ${p.qty} ${p.unrealized_pnl !== null && p.unrealized_pnl !== undefined ? fmtPnl(p.unrealized_pnl) : ""}`,
    });
    if (p.stop_loss) lines.push({ id: `sl:${p.id}`, kind: "sl", ref: p.id, price: p.stop_loss, color: "#F23645", draggable: true, title: "SL" });
    if (p.take_profit) lines.push({ id: `tp:${p.id}`, kind: "tp", ref: p.id, price: p.take_profit, color: "#089981", draggable: true, title: "TP" });
  }
  for (const o of paper.orders || []) {
    if (o.provider !== pane.provider || !sameSymbol(o.symbol, pane.symbol) || o.type === "market") continue;
    lines.push({
      id: `ord:${o.id}`, kind: "order", ref: o.id, price: o.price, color: o.side === "buy" ? "#2962FF" : "#F23645", draggable: true,
      dashed: true, title: `${o.side.toUpperCase()} ${o.type === "limit" ? "LMT" : "STP"} ${o.qty}`,
    });
  }
  return lines;
}

/** A line was dragged on the chart: move the order / SL / TP to the new price. */
export async function moveTradeLine(line, price, digits = 5) {
  const st = useStore.getState();
  const px = Number(Number(price).toFixed(digits));
  try {
    if (line.kind === "order") await modifyPaperOrder(line.ref, { price: px });
    else if (line.kind === "sl") await modifyPaperPosition(line.ref, { stop_loss: px });
    else if (line.kind === "tp") await modifyPaperPosition(line.ref, { take_profit: px });
    st.toast(`${line.kind === "order" ? "Order" : line.kind.toUpperCase()} moved to ${px}`, "ok", 1800);
  } catch (err) {
    st.toast(err.message);
  }
  loadPaper();
}
