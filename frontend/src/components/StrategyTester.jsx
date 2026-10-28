// Strategy Tester: performance summary, equity curve and trade list of a strategy on the active chart.
import React, { useEffect, useMemo, useRef, useState } from "react";
import { AreaSeries, ColorType, createChart } from "lightweight-charts";
import { useStore } from "../store/useStore.js";
import { formatStamp } from "../chart/timezone.js";
import { useChartZone } from "../chart/useChartZone.js";

const EMPTY = [];

function money(v, cur = "") {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const s = Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return `${v < 0 ? "-" : ""}${s}${cur ? " " + cur : ""}`;
}

function stats(st) {
  const trades = st.trades || [];
  const wins = trades.filter((t) => t.profit > 0);
  const losses = trades.filter((t) => t.profit < 0);
  const gp = wins.reduce((a, t) => a + t.profit, 0);
  const gl = losses.reduce((a, t) => a + t.profit, 0);
  const avgWin = wins.length ? gp / wins.length : 0;
  const avgLoss = losses.length ? gl / losses.length : 0;
  // equity curve & max drawdown from closed trades
  let eq = st.initialCapital || 0;
  let peak = eq;
  let maxDD = 0;
  const curve = [];
  for (const t of [...trades].sort((a, b) => a.exitTime - b.exitTime)) {
    eq += t.profit;
    peak = Math.max(peak, eq);
    maxDD = Math.max(maxDD, peak - eq);
    curve.push({ time: t.exitTime, value: eq });
  }
  const net = gp + gl;
  return {
    net,
    netPct: st.initialCapital ? (net / st.initialCapital) * 100 : 0,
    total: trades.length,
    winRate: trades.length ? (wins.length / trades.length) * 100 : 0,
    profitFactor: gl !== 0 ? gp / Math.abs(gl) : gp > 0 ? Infinity : 0,
    maxDD,
    maxDDPct: peak ? (maxDD / peak) * 100 : 0,
    avgTrade: trades.length ? net / trades.length : 0,
    avgWin,
    avgLoss,
    ratio: avgLoss !== 0 ? avgWin / Math.abs(avgLoss) : 0,
    largestWin: wins.length ? Math.max(...wins.map((t) => t.profit)) : 0,
    largestLoss: losses.length ? Math.min(...losses.map((t) => t.profit)) : 0,
    curve,
  };
}

function EquityChart({ curve, initial }) {
  const ref = useRef(null);
  useEffect(() => {
    if (!ref.current) return undefined;
    const chart = createChart(ref.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: "#B2B5BE", fontSize: 11, attributionLogo: false },
      grid: { vertLines: { visible: false }, horzLines: { color: "rgba(42,46,57,0.5)" } },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false, timeVisible: true },
      handleScroll: false,
      handleScale: false,
    });
    const s = chart.addSeries(AreaSeries, { lineColor: "#2962FF", topColor: "rgba(41,98,255,0.35)", bottomColor: "rgba(41,98,255,0.02)", lineWidth: 2, priceLineVisible: false });
    const data = [];
    let last = -Infinity;
    if (curve.length) data.push({ time: curve[0].time - 1, value: initial });
    for (const p of curve) {
      if (p.time <= last) continue;
      data.push(p);
      last = p.time;
    }
    s.setData(data.filter((d, i) => i === 0 || d.time > data[i - 1].time));
    chart.timeScale().fitContent();
    // the tab may be hidden (display:none) when created: refit whenever it gets a real size
    const ro = new ResizeObserver(() => chart.timeScale().fitContent());
    ro.observe(ref.current);
    return () => {
      ro.disconnect();
      chart.remove();
    };
  }, [curve, initial]);
  return <div className="equity-chart" ref={ref} />;
}

export default function StrategyTester() {
  const pane = useStore((s) => s.panes.find((p) => p.id === s.activePaneId) || s.panes[0]);
  const zone = useChartZone(pane ? pane.id : null);
  const infos = useStore((s) => (pane ? s.paneRuntime[pane.id]?.indicators : null)) || EMPTY;
  const strategies = infos.filter((i) => i.declaration && i.declaration.type === "strategy");
  const [selId, setSelId] = useState(null);
  const [tab, setTab] = useState("overview");
  const sel = strategies.find((x) => x.id === selId) || strategies[0];
  const st = sel ? sel.strategy : null;
  const m = useMemo(() => (st ? stats(st) : null), [st]);

  if (!strategies.length) {
    return (
      <div className="tester empty-state">
        <p>No strategy on the active chart.</p>
        <p className="muted">
          Add one from <b>Indicators → Strategies</b>, or write a <code>strategy()</code> script in the Pine Editor.
        </p>
        <button className="btn primary" onClick={() => useStore.getState().openDialog({ type: "indicators", paneId: pane.id })}>
          Browse strategies
        </button>
      </div>
    );
  }

  const cur = st && st.currency && st.currency !== "NONE" ? st.currency : "";
  return (
    <div className="tester">
      <div className="tester-head">
        <select value={sel.id} onChange={(e) => setSelId(e.target.value)}>
          {strategies.map((x) => (
            <option key={x.id} value={x.id}>
              {x.name}
            </option>
          ))}
        </select>
        <div className="tabs-inline">
          <button className={tab === "overview" ? "active" : ""} onClick={() => setTab("overview")}>
            Overview
          </button>
          <button className={tab === "trades" ? "active" : ""} onClick={() => setTab("trades")}>
            List of trades
          </button>
        </div>
        {sel.error && <span className="legend-error">⚠ {sel.error}</span>}
        <span className="flex1" />
        <span className="muted small">{pane.symbol} · {pane.timeframe}</span>
      </div>
      {!st && <div className="empty">{sel.error ? "The strategy failed to run." : "Calculating…"}</div>}
      {st && m && tab === "overview" && (
        <div className="tester-overview">
          <div className="metrics">
            <div className="metric">
              <span>Net profit</span>
              <b className={m.net >= 0 ? "up" : "down"}>
                {money(m.net, cur)} <small>({m.netPct.toFixed(2)}%)</small>
              </b>
            </div>
            <div className="metric">
              <span>Total closed trades</span>
              <b>{m.total}</b>
            </div>
            <div className="metric">
              <span>Percent profitable</span>
              <b>{m.winRate.toFixed(2)}%</b>
            </div>
            <div className="metric">
              <span>Profit factor</span>
              <b>{Number.isFinite(m.profitFactor) ? m.profitFactor.toFixed(3) : "∞"}</b>
            </div>
            <div className="metric">
              <span>Max drawdown</span>
              <b className="down">
                {money(m.maxDD, cur)} <small>({m.maxDDPct.toFixed(2)}%)</small>
              </b>
            </div>
            <div className="metric">
              <span>Avg trade</span>
              <b className={m.avgTrade >= 0 ? "up" : "down"}>{money(m.avgTrade, cur)}</b>
            </div>
            <div className="metric">
              <span>Avg win / avg loss</span>
              <b>{m.ratio.toFixed(3)}</b>
            </div>
            <div className="metric">
              <span>Largest win / loss</span>
              <b>
                <span className="up">{money(m.largestWin)}</span> / <span className="down">{money(m.largestLoss)}</span>
              </b>
            </div>
            <div className="metric">
              <span>Open P&L</span>
              <b className={(st.openProfit || 0) >= 0 ? "up" : "down"}>{money(st.openProfit || 0, cur)}</b>
            </div>
            <div className="metric">
              <span>Buy & hold</span>
              <b className={(st.buyAndHold || 0) >= 0 ? "up" : "down"}>{money(st.buyAndHold, cur)}</b>
            </div>
            <div className="metric">
              <span>Sharpe / Sortino</span>
              <b>
                {Number.isFinite(st.sharpe) ? st.sharpe.toFixed(3) : "—"} / {Number.isFinite(st.sortino) ? st.sortino.toFixed(3) : "—"}
              </b>
            </div>
            <div className="metric">
              <span>Initial capital</span>
              <b>{money(st.initialCapital, cur)}</b>
            </div>
          </div>
          {m.curve.length ? <EquityChart curve={m.curve} initial={st.initialCapital} /> : <div className="empty">No closed trades yet on the loaded history.</div>}
        </div>
      )}
      {st && tab === "trades" && (
        <div className="trades-table-wrap">
          <table className="trades-table">
            <thead>
              <tr>
                <th>#</th>
                <th>Type</th>
                <th>Entry time</th>
                <th>Entry</th>
                <th>Exit time</th>
                <th>Exit</th>
                <th>Size</th>
                <th>Profit</th>
                <th>Run-up</th>
                <th>Drawdown</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {[...st.trades].reverse().map((t, i) => (
                <tr key={i}>
                  <td>{st.trades.length - i}</td>
                  <td className={t.size >= 0 ? "up" : "down"}>{t.size >= 0 ? "Long" : "Short"}</td>
                  <td>{formatStamp(t.entryTime, zone.tz, zone.shift)}</td>
                  <td>{t.entryPrice.toFixed(pane.digits ?? 5)}</td>
                  <td>{formatStamp(t.exitTime, zone.tz, zone.shift)}</td>
                  <td>{t.exitPrice.toFixed(pane.digits ?? 5)}</td>
                  <td>{Math.abs(t.size).toFixed(4)}</td>
                  <td className={t.profit >= 0 ? "up" : "down"}>{money(t.profit)}</td>
                  <td>{money(t.maxRunup)}</td>
                  <td>{money(t.maxDrawdown)}</td>
                  <td>
                    <button
                      className="icon-btn"
                      title="Log this trade in the journal"
                      onClick={() => useStore.getState().openDialog({ type: "journal", paneId: pane.id, trade: t, strategyName: sel.name, capture: false })}
                    >
                      📓
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!st.trades.length && <div className="empty">No closed trades.</div>}
        </div>
      )}
    </div>
  );
}
