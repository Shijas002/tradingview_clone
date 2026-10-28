// Create / edit an alert (price level, channel or trend line).
import React, { useMemo, useState } from "react";
import { createAlert, updateAlert } from "../../api/client.js";
import { CONDITIONS, TRIGGERS, alertBody, loadAlerts, requestNotificationPermission } from "../../alerts/alerts.js";
import { useStore } from "../../store/useStore.js";
import { Modal } from "../ui.jsx";

function toLocalInput(ms) {
  if (!ms) return "";
  const d = new Date(ms);
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export default function AlertDialog({ dialog }) {
  const pane = useStore((s) => s.panes.find((p) => p.id === dialog.paneId) || s.panes.find((p) => p.id === s.activePaneId) || s.panes[0]);
  const existing = dialog.alert || null;
  const drawing = dialog.drawing || null;
  const digits = pane?.digits ?? 5;

  const initial = useMemo(() => {
    if (existing) return { ...existing };
    const base = { name: "", provider: pane.provider, symbol: pane.symbol, condition: "crossing", price: dialog.price ?? null, price2: null, line: null, trigger: "once", message: "", expires_at: null, drawing_id: null, active: true };
    if (drawing) {
      base.drawing_id = drawing.id;
      if (["trendline", "ray", "extended", "arrow"].includes(drawing.type)) {
        const [a, b] = drawing.points;
        base.line = { t1: a.time, p1: a.price, t2: b.time, p2: b.price, extendLeft: !!drawing.style.extendLeft, extendRight: !!drawing.style.extendRight || drawing.type === "ray" };
        base.price = null;
        base.name = `${pane.symbol} trend line`;
      } else if (drawing.type === "rect" || drawing.type === "pricerange") {
        base.price = Math.max(drawing.points[0].price, drawing.points[1].price);
        base.price2 = Math.min(drawing.points[0].price, drawing.points[1].price);
        base.condition = "entering_channel";
        base.name = `${pane.symbol} zone`;
      } else {
        base.price = drawing.points[0].price;
        base.name = `${pane.symbol} level`;
      }
    }
    if (base.price !== null && base.price !== undefined) base.price = Number(Number(base.price).toFixed(digits));
    if (base.price2 !== null && base.price2 !== undefined) base.price2 = Number(Number(base.price2).toFixed(digits));
    return base;
  }, [existing, drawing, pane, dialog.price, digits]);

  const [form, setForm] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const s = useStore.getState();
  const set = (patch) => setForm((f) => ({ ...f, ...patch }));
  const channel = form.condition === "entering_channel" || form.condition === "exiting_channel";
  const isLine = !!form.line;

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const body = alertBody(form, {
        price: isLine ? null : form.price === "" || form.price === null ? null : Number(form.price),
        price2: channel ? Number(form.price2) : null,
        name: form.name || `${form.symbol} ${CONDITIONS.find((c) => c.v === form.condition)?.l.toLowerCase()}`,
        active: true,
      });
      if (!isLine && (body.price === null || Number.isNaN(body.price))) throw new Error("Enter a price");
      if (channel && Number.isNaN(body.price2)) throw new Error("Enter the second price of the channel");
      if (existing) await updateAlert(existing.id, body);
      else await createAlert(body);
      requestNotificationPermission();
      await loadAlerts();
      s.toast(existing ? "Alert updated" : "Alert created", "ok", 2500);
      s.closeDialog();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      title={existing ? "Edit alert" : `Create alert on ${form.symbol}`}
      onClose={() => s.closeDialog()}
      width={460}
      footer={
        <>
          {error && <span className="form-error">{error}</span>}
          <span className="flex1" />
          <button className="btn" onClick={() => s.closeDialog()}>
            Cancel
          </button>
          <button className="btn primary" onClick={submit} disabled={busy}>
            {existing ? "Save" : "Create"}
          </button>
        </>
      }
    >
      <div className="form-grid">
        <label className="form-row">
          <span>Symbol</span>
          <b>
            {form.provider}:{form.symbol}
          </b>
        </label>
        <label className="form-row">
          <span>Condition</span>
          <select value={form.condition} onChange={(e) => set({ condition: e.target.value })}>
            {CONDITIONS.filter((c) => !isLine || !c.v.includes("channel")).map((c) => (
              <option key={c.v} value={c.v}>
                {c.l}
              </option>
            ))}
          </select>
        </label>
        {isLine ? (
          <label className="form-row">
            <span>Target</span>
            <span className="muted">Trend line ({form.line.extendRight ? "extended right" : "segment only"})</span>
          </label>
        ) : (
          <label className="form-row">
            <span>{channel ? "Upper price" : "Price"}</span>
            <input type="number" step="any" value={form.price ?? ""} onChange={(e) => set({ price: e.target.value })} />
          </label>
        )}
        {channel && !isLine && (
          <label className="form-row">
            <span>Lower price</span>
            <input type="number" step="any" value={form.price2 ?? ""} onChange={(e) => set({ price2: e.target.value })} />
          </label>
        )}
        <label className="form-row">
          <span>Trigger</span>
          <select value={form.trigger} onChange={(e) => set({ trigger: e.target.value })}>
            {TRIGGERS.map((t) => (
              <option key={t.v} value={t.v}>
                {t.l}
              </option>
            ))}
          </select>
        </label>
        <label className="form-row">
          <span>Expiration</span>
          <input type="datetime-local" value={toLocalInput(form.expires_at)} onChange={(e) => set({ expires_at: e.target.value ? new Date(e.target.value).getTime() : null })} />
        </label>
        <label className="form-row">
          <span>Alert name</span>
          <input type="text" value={form.name} onChange={(e) => set({ name: e.target.value })} placeholder="optional" />
        </label>
        <label className="form-row form-row-top">
          <span>Message</span>
          <textarea rows={3} value={form.message} onChange={(e) => set({ message: e.target.value })} placeholder="{{ticker}} crossed {{level}} — price {{close}}" />
        </label>
        <div className="muted small">Alerts run on the backend, so they fire even when this page is closed. Placeholders: {"{{ticker}} {{close}} {{level}}"}.</div>
      </div>
    </Modal>
  );
}
