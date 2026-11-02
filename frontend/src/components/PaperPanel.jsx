// Paper trading panel (bottom tab, v1.1): account, positions, working orders, history.
import React, { useEffect, useState } from "react";
import { cancelPaperOrder, closeAllPaper, closePaperPosition, getPaperHistory, modifyPaperOrder, modifyPaperPosition, resetPaper } from "../api/client.js";
import { formatEntryTime } from "../journal/journal.js";
import { fmtPnl, loadPaper } from "../paper/paper.js";
import { useStore } from "../store/useStore.js";
import { Icon } from "./Icons.jsx";

function EditablePrice({ value, onSave, placeholder = "add" }) {
  const [editing, setEditing] = useState(false);
  const [v, setV] = useState(value ?? "");
  if (!editing) {
    return (
      <button className="linklike muted-link" onClick={() => { setV(value ?? ""); setEditing(true); }} title="Click to change">
        {value ?? <span className="muted">{placeholder}</span>}
      </button>
    );
  }
  const commit = () => {
    setEditing(false);
    const n = v === "" ? null : Number(v);
    if (n !== value && (n === null || n > 0)) onSave(n);
  };
  return (
    <input
      className="inline-num"
      type="number"
      step="any"
      autoFocus
      value={v}
      onChange={(e) => setV(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") commit();
        if (e.key === "Escape") setEditing(false);
      }}
    />
  );
}

export default function PaperPanel() {
  const paper = useStore((s) => s.paper);
  const pane = useStore((s) => s.panes.find((p) => p.id === s.activePaneId) || s.panes[0]);
  useStore((s) => s.settings.timezone);
  const [tab, setTab] = useState("positions");
  const [history, setHistory] = useState(null);
  const s = useStore.getState();

  useEffect(() => {
    loadPaper();
  }, []);
  useEffect(() => {
    if (tab === "history") getPaperHistory(300).then(setHistory).catch((e) => s.toast(e.message));
  }, [tab, paper]); // eslint-disable-line react-hooks/exhaustive-deps

  const run = async (fn, ok) => {
    try {
      await fn();
      await loadPaper();
      if (ok) s.toast(ok, "ok", 2000);
    } catch (err) {
      s.toast(err.message);
    }
  };
  const order = (side) => pane && s.openDialog({ type: "order", paneId: pane.id, side });
  const reset = () => {
    const v = window.prompt("Reset the paper account. Starting balance:", String(paper ? paper.initial_balance : 100000));
    if (v === null) return;
    const balance = Number(v);
    if (!balance || balance <= 0) return s.toast("Enter a positive balance");
    run(() => resetPaper({ balance }), "Paper account reset");
  };

  if (!paper) return <div className="empty">Loading paper account…</div>;
  const cur = paper.currency;
  const st = paper.stats || {};
  return (
    <div className="paper-panel">
      <div className="journal-head">
        <div className="journal-stats">
          <span>
            Balance <b>{paper.balance.toLocaleString(undefined, { maximumFractionDigits: 2 })}</b> {cur}
          </span>
          <span>
            Equity <b>{paper.equity.toLocaleString(undefined, { maximumFractionDigits: 2 })}</b>
          </span>
          <span>
            Open P&amp;L <b className={paper.unrealized_pnl >= 0 ? "up" : "down"}>{fmtPnl(paper.unrealized_pnl)}</b>
          </span>
          <span>
            Realized <b className={paper.realized_pnl >= 0 ? "up" : "down"}>{fmtPnl(paper.realized_pnl)}</b>
          </span>
          <span>
            Win rate <b>{st.win_rate !== null && st.win_rate !== undefined ? `${st.win_rate.toFixed(1)}%` : "—"}</b> ({st.closed || 0})
          </span>
          <span>
            Profit factor <b>{st.profit_factor ? st.profit_factor.toFixed(2) : "—"}</b>
          </span>
        </div>
        <span className="flex1" />
        <button className="btn small sell-btn" onClick={() => order("sell")} title="Sell on the active chart (Alt+S)">
          Sell
        </button>
        <button className="btn small buy-btn" onClick={() => order("buy")} title="Buy on the active chart (Alt+B)">
          Buy
        </button>
      </div>
      <div className="journal-toolbar">
        <span className="seg">
          <button className={tab === "positions" ? "active" : ""} onClick={() => setTab("positions")}>
            Positions {paper.positions.length ? `(${paper.positions.length})` : ""}
          </button>
          <button className={tab === "orders" ? "active" : ""} onClick={() => setTab("orders")}>
            Orders {paper.orders.length ? `(${paper.orders.length})` : ""}
          </button>
          <button className={tab === "history" ? "active" : ""} onClick={() => setTab("history")}>
            History
          </button>
        </span>
        <span className="flex1" />
        {paper.positions.length > 0 && (
          <button className="btn small" onClick={() => window.confirm("Close every open position at market?") && run(() => closeAllPaper(), "All positions closed")}>
            Close all
          </button>
        )}
        <button className="btn small" onClick={reset} title="Start over with a fresh balance">
          Reset account
        </button>
      </div>
      <div className="journal-table-wrap">
        {tab === "positions" && (
          <table className="journal-table">
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Side</th>
                <th>Qty</th>
                <th>Avg price</th>
                <th>Last</th>
                <th>Open P&amp;L</th>
                <th>Stop loss</th>
                <th>Take profit</th>
                <th>Opened</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {paper.positions.map((p) => (
                <tr key={p.id}>
                  <td>
                    <button className="linklike" onClick={() => pane && s.updatePane(pane.id, { provider: p.provider, symbol: p.symbol, digits: undefined })}>
                      {p.symbol}
                    </button>
                    <span className="muted"> {p.provider}</span>
                  </td>
                  <td className={p.side === "long" ? "up" : "down"}>{p.side === "long" ? "Long" : "Short"}</td>
                  <td>{p.qty}</td>
                  <td>{p.avg_price}</td>
                  <td>{p.last ?? "—"}</td>
                  <td className={(p.unrealized_pnl || 0) >= 0 ? "up" : "down"}>{fmtPnl(p.unrealized_pnl)}</td>
                  <td>
                    <EditablePrice value={p.stop_loss} onSave={(v) => run(() => modifyPaperPosition(p.id, v === null ? { clear: ["stop_loss"] } : { stop_loss: v }), "Stop loss updated")} />
                  </td>
                  <td>
                    <EditablePrice value={p.take_profit} onSave={(v) => run(() => modifyPaperPosition(p.id, v === null ? { clear: ["take_profit"] } : { take_profit: v }), "Take profit updated")} />
                  </td>
                  <td className="nowrap">{formatEntryTime(Math.floor(p.opened_at / 1000), p.provider)}</td>
                  <td className="journal-actions">
                    <button
                      className="btn tiny"
                      onClick={() => {
                        const v = window.prompt(`Close how much of ${p.qty}? (empty = all)`, "");
                        if (v === null) return;
                        run(() => closePaperPosition(p.id, v ? Number(v) : null), "Position closed");
                      }}
                    >
                      Close
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {tab === "positions" && !paper.positions.length && (
          <div className="empty">No open positions. Click BUY / SELL on a chart's price box, press Alt+B / Alt+S, or use the buttons above.</div>
        )}
        {tab === "orders" && (
          <table className="journal-table">
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Side</th>
                <th>Type</th>
                <th>Qty</th>
                <th>Price</th>
                <th>SL</th>
                <th>TP</th>
                <th>Placed</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {paper.orders.map((o) => (
                <tr key={o.id}>
                  <td>{o.symbol}</td>
                  <td className={o.side === "buy" ? "up" : "down"}>{o.side.toUpperCase()}</td>
                  <td>{o.type}</td>
                  <td>
                    <EditablePrice value={o.qty} onSave={(v) => v && run(() => modifyPaperOrder(o.id, { qty: v }), "Order updated")} />
                  </td>
                  <td>
                    <EditablePrice value={o.price} onSave={(v) => v && run(() => modifyPaperOrder(o.id, { price: v }), "Order updated")} />
                  </td>
                  <td>
                    <EditablePrice value={o.stop_loss} onSave={(v) => run(() => modifyPaperOrder(o.id, v === null ? { clear: ["stop_loss"] } : { stop_loss: v }), "Order updated")} />
                  </td>
                  <td>
                    <EditablePrice value={o.take_profit} onSave={(v) => run(() => modifyPaperOrder(o.id, v === null ? { clear: ["take_profit"] } : { take_profit: v }), "Order updated")} />
                  </td>
                  <td className="nowrap">{formatEntryTime(Math.floor(o.created_at / 1000), o.provider)}</td>
                  <td className="journal-actions">
                    <button className="icon-btn danger" title="Cancel order" onClick={() => run(() => cancelPaperOrder(o.id), "Order cancelled")}>
                      <Icon.trash />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {tab === "orders" && !paper.orders.length && <div className="empty">No working orders. Limit and stop orders wait here and show as dashed lines you can drag on the chart.</div>}
        {tab === "history" && history && (
          <table className="journal-table">
            <thead>
              <tr>
                <th>Time</th>
                <th>Symbol</th>
                <th>Side</th>
                <th>Type</th>
                <th>Qty</th>
                <th>Fill / price</th>
                <th>Status</th>
                <th>Realized</th>
                <th>Note</th>
              </tr>
            </thead>
            <tbody>
              {history.orders.map((o) => (
                <tr key={o.id}>
                  <td className="nowrap">{formatEntryTime(Math.floor((o.filled_at || o.updated_at) / 1000), o.provider)}</td>
                  <td>{o.symbol}</td>
                  <td className={o.side === "buy" ? "up" : "down"}>{o.side.toUpperCase()}</td>
                  <td>{o.type}</td>
                  <td>{o.qty}</td>
                  <td>{o.fill_price ?? o.price ?? "—"}</td>
                  <td>{o.status}</td>
                  <td className={o.realized_pnl > 0 ? "up" : o.realized_pnl < 0 ? "down" : ""}>{o.realized_pnl ? fmtPnl(o.realized_pnl) : ""}</td>
                  <td className="muted">{o.note || o.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {tab === "history" && history && !history.orders.length && <div className="empty">No filled or cancelled orders yet.</div>}
      </div>
    </div>
  );
}
