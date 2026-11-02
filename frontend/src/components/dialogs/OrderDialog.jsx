// Paper-trading order ticket (v1.1): market / limit / stop with optional SL & TP.
import React, { useMemo, useState } from "react";
import { placePaperOrder } from "../../api/client.js";
import { lastPrice } from "../../chart/registry.js";
import { fmtPnl, loadPaper } from "../../paper/paper.js";
import { symbolKey, useStore } from "../../store/useStore.js";
import { Modal } from "../ui.jsx";

const num = (v) => (v === "" || v === null || v === undefined || Number.isNaN(Number(v)) ? null : Number(v));

export default function OrderDialog({ dialog }) {
  const pane = useStore((s) => s.panes.find((p) => p.id === dialog.paneId) || s.panes.find((p) => p.id === s.activePaneId) || s.panes[0]);
  const paper = useStore((s) => s.paper);
  const settings = useStore((s) => s.settings);
  const s = useStore.getState();
  const digits = pane?.digits ?? 5;
  const last = lastPrice(pane.id);
  const key = symbolKey(pane.provider || "", pane.symbol);
  const step = 1 / 10 ** digits;

  const initial = useMemo(() => {
    const qtyMemory = (settings.paperQty || {})[key];
    const f = {
      side: dialog.side || "buy",
      type: dialog.orderType || "market",
      qty: qtyMemory || 1,
      price: dialog.price ?? last ?? "",
      useSl: false,
      sl: "",
      useTp: false,
      tp: "",
    };
    if (dialog.drawing) {
      const [entry, target, stop] = dialog.drawing.points;
      f.side = dialog.drawing.type === "short" ? "sell" : "buy";
      f.qty = Number(dialog.drawing.style?.qty) || f.qty;
      f.useSl = true;
      f.sl = Number(stop.price.toFixed(digits));
      f.useTp = true;
      f.tp = Number(target.price.toFixed(digits));
      const far = last && Math.abs(entry.price - last) / last > 0.0005;
      f.type = far ? ((f.side === "buy") === entry.price < last ? "limit" : "stop") : "market";
      f.price = Number(entry.price.toFixed(digits));
    }
    if (f.price !== "" && f.price !== null) f.price = Number(Number(f.price).toFixed(digits));
    return f;
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const [form, setForm] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const set = (patch) => setForm((f) => ({ ...f, ...patch }));
  const buy = form.side === "buy";
  const entry = form.type === "market" ? last : num(form.price);
  const qty = num(form.qty) || 0;
  const risk = form.useSl && entry && num(form.sl) ? Math.abs(entry - num(form.sl)) * qty : null;
  const reward = form.useTp && entry && num(form.tp) ? Math.abs(num(form.tp) - entry) * qty : null;
  const position = (paper?.positions || []).find((p) => p.provider === pane.provider && p.symbol.toLowerCase() === pane.symbol.toLowerCase());

  const defaultSl = () => (entry ? Number((buy ? entry * 0.99 : entry * 1.01).toFixed(digits)) : "");
  const defaultTp = () => (entry ? Number((buy ? entry * 1.02 : entry * 0.98).toFixed(digits)) : "");

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      if (!pane.provider) throw new Error("This chart has no data provider");
      if (!qty || qty <= 0) throw new Error("Enter a quantity above 0");
      const body = {
        provider: pane.provider,
        symbol: pane.symbol,
        side: form.side,
        type: form.type,
        qty,
        price: form.type === "market" ? null : num(form.price),
        stop_loss: form.useSl ? num(form.sl) : null,
        take_profit: form.useTp ? num(form.tp) : null,
      };
      if (form.type !== "market" && !body.price) throw new Error(`Enter the ${form.type} price`);
      if (form.useSl && !body.stop_loss) throw new Error("Enter the stop-loss price");
      if (form.useTp && !body.take_profit) throw new Error("Enter the take-profit price");
      const o = await placePaperOrder(body);
      s.updateSettings({ paperQty: { ...(settings.paperQty || {}), [key]: qty } });
      await loadPaper();
      s.toast(
        o.status === "filled" ? `${o.side.toUpperCase()} ${o.qty} ${o.symbol} filled at ${o.fill_price}` : `${o.side.toUpperCase()} ${o.type} ${o.qty} @ ${o.price} placed`,
        "ok",
        3000,
      );
      if (!settings.bottomPanel || settings.bottomTab !== "paper") s.updateSettings({ bottomPanel: true, bottomTab: "paper" });
      s.closeDialog();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      title={`Paper order · ${pane.symbol}`}
      onClose={() => s.closeDialog()}
      width={420}
      className="order-dialog"
      footer={
        <>
          {error && <span className="form-error">{error}</span>}
          <span className="flex1" />
          <button className="btn" onClick={() => s.closeDialog()}>
            Cancel
          </button>
          <button className={`btn primary ${buy ? "buy" : "sell"}`} onClick={submit} disabled={busy}>
            {busy ? "Sending…" : `${buy ? "Buy" : "Sell"} ${qty || ""} ${form.type === "market" ? "MKT" : form.type === "limit" ? "LMT" : "STP"}`}
          </button>
        </>
      }
    >
      <div className="form-grid">
        <div className="order-sides">
          <button className={`order-side sell ${!buy ? "active" : ""}`} onClick={() => set({ side: "sell" })}>
            SELL
            <small>{last ? last.toFixed(digits) : "—"}</small>
          </button>
          <button className={`order-side buy ${buy ? "active" : ""}`} onClick={() => set({ side: "buy" })}>
            BUY
            <small>{last ? last.toFixed(digits) : "—"}</small>
          </button>
        </div>
        <div className="form-row">
          <span>Order type</span>
          <span className="seg">
            {["market", "limit", "stop"].map((t) => (
              <button key={t} className={form.type === t ? "active" : ""} onClick={() => set({ type: t, price: form.price || (last ? Number(last.toFixed(digits)) : "") })}>
                {t[0].toUpperCase() + t.slice(1)}
              </button>
            ))}
          </span>
        </div>
        {form.type !== "market" && (
          <label className="form-row">
            <span>{form.type === "limit" ? "Limit price" : "Stop price"}</span>
            <input type="number" step={step} value={form.price ?? ""} onChange={(e) => set({ price: e.target.value })} />
          </label>
        )}
        <label className="form-row">
          <span>Quantity</span>
          <input type="number" step="any" min="0" value={form.qty} onChange={(e) => set({ qty: e.target.value })} autoFocus />
        </label>
        <label className="form-row">
          <span>
            <input type="checkbox" checked={form.useSl} onChange={(e) => set({ useSl: e.target.checked, sl: form.sl || defaultSl() })} /> Stop loss
          </span>
          <input type="number" step={step} value={form.sl} disabled={!form.useSl} onChange={(e) => set({ sl: e.target.value })} />
        </label>
        <label className="form-row">
          <span>
            <input type="checkbox" checked={form.useTp} onChange={(e) => set({ useTp: e.target.checked, tp: form.tp || defaultTp() })} /> Take profit
          </span>
          <input type="number" step={step} value={form.tp} disabled={!form.useTp} onChange={(e) => set({ tp: e.target.value })} />
        </label>
        <div className="journal-metrics">
          {risk !== null && <span className="down">Risk {fmtPnl(-risk)}</span>}
          {reward !== null && <span className="up">Reward {fmtPnl(reward)}</span>}
          {risk && reward ? <span>R:R {(reward / risk).toFixed(2)}</span> : null}
          {entry && qty ? <span>Value {(entry * qty).toLocaleString(undefined, { maximumFractionDigits: 2 })}</span> : null}
        </div>
        {position && (
          <div className="muted small">
            Open position: {position.side} {position.qty} @ {position.avg_price} ({fmtPnl(position.unrealized_pnl)}). Orders on the other side reduce or reverse it.
          </div>
        )}
        <div className="muted small">
          Simulated with live prices · balance {paper ? `${paper.balance.toLocaleString(undefined, { maximumFractionDigits: 2 })} ${paper.currency}` : "—"} ·
          P&amp;L = price difference × quantity (Forex: use units, 100000 = 1 lot).
        </div>
      </div>
    </Modal>
  );
}
