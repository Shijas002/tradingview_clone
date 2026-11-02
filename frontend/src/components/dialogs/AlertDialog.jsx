// Create / edit an alert: price level, channel or trend line - or (v1.1) an indicator /
// Pine Script condition that the backend evaluates with the same Pine engine.
import React, { useEffect, useMemo, useState } from "react";
import { createAlert, pineCheck, updateAlert } from "../../api/client.js";
import { CONDITIONS, FREQUENCIES, TRIGGERS, alertBody, loadAlerts, requestNotificationPermission } from "../../alerts/alerts.js";
import { useStore } from "../../store/useStore.js";
import { TF } from "../../utils/market.js";
import { Modal } from "../ui.jsx";

function toLocalInput(ms) {
  if (!ms) return "";
  const d = new Date(ms);
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

const fmtVal = (v) => (typeof v === "number" ? (Math.abs(v) >= 1000 ? v.toFixed(2) : Number(v.toPrecision(6)).toString()) : v === true ? "true" : v === false ? "false" : "—");

export default function AlertDialog({ dialog }) {
  const pane = useStore((s) => s.panes.find((p) => p.id === dialog.paneId) || s.panes.find((p) => p.id === s.activePaneId) || s.panes[0]);
  const runtimeInfos = useStore((s) => (pane ? s.paneRuntime[pane.id]?.indicators : null)) || [];
  const existing = dialog.alert || null;
  const drawing = dialog.drawing || null;
  const digits = pane?.digits ?? 5;
  const indicators = pane ? pane.indicators || [] : [];
  const nameOf = (ind) => (runtimeInfos.find((r) => r.id === ind.id) || {}).name || ind.name || "Script";

  const initial = useMemo(() => {
    if (existing) return { ...existing };
    const base = {
      name: "", provider: pane.provider, symbol: pane.symbol, condition: "crossing", price: dialog.price ?? null, price2: null, line: null,
      trigger: "once", message: "", expires_at: null, drawing_id: null, active: true, notify_telegram: true, kind: "price",
      timeframe: pane.timeframe, frequency: "once_per_bar_close", target: null, target2: null,
    };
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

  // "price" or an indicator id on this chart ("__existing__" when editing an indicator alert)
  const [source, setSource] = useState(() => {
    if (existing && existing.kind === "indicator") return "__existing__";
    if (dialog.indicatorId) return dialog.indicatorId;
    return "price";
  });
  const [form, setForm] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [check, setCheck] = useState(null); // {plots, alertconditions, uses_alert}
  const [checking, setChecking] = useState(false);
  const [compare, setCompare] = useState(existing && existing.target2 ? "plot" : "value");
  const s = useStore.getState();
  const set = (patch) => setForm((f) => ({ ...f, ...patch }));
  const isIndicator = source !== "price";
  const channel = form.condition === "entering_channel" || form.condition === "exiting_channel";
  const isLine = !!form.line;

  const script = useMemo(() => {
    if (source === "__existing__") return { name: existing.script_name, source: existing.script_source, inputs: existing.script_inputs || {}, timeframe: existing.timeframe };
    const ind = indicators.find((i) => i.id === source);
    return ind ? { name: nameOf(ind), source: ind.source, inputs: ind.inputs || {}, timeframe: pane.timeframe } : null;
  }, [source]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!isIndicator || !script) return;
    let cancelled = false;
    setChecking(true);
    setCheck(null);
    setError(null);
    pineCheck({ provider: form.provider, symbol: form.symbol, timeframe: script.timeframe, source: script.source, inputs: script.inputs })
      .then((res) => {
        if (cancelled) return;
        setCheck(res);
        if (!form.target || source !== "__existing__") {
          const first = res.alertconditions.length ? `alertcondition:${res.alertconditions[0]}` : res.plots.length ? `plot:${res.plots[0].title}` : res.uses_alert ? "alert" : null;
          const plot0 = res.plots[0];
          set({
            target: first,
            target2: null,
            condition: "crossing_up",
            price: plot0 && typeof plot0.value === "number" ? Number(plot0.value.toPrecision(6)) : null,
            name: form.name || `${script.name} on ${form.symbol}`,
          });
          setCompare("value");
        }
      })
      .catch((err) => !cancelled && setError(err.message))
      .finally(() => !cancelled && setChecking(false));
    return () => {
      cancelled = true;
    };
  }, [source, script]); // eslint-disable-line react-hooks/exhaustive-deps

  const targetKind = String(form.target || "").split(":")[0];

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      let body;
      if (isIndicator) {
        if (!script) throw new Error("Choose an indicator");
        if (!form.target) throw new Error("This script has no plots, alertcondition() or alert() to alert on");
        const plotTarget = targetKind === "plot";
        body = alertBody(form, {
          kind: "indicator",
          line: null,
          drawing_id: null,
          timeframe: script.timeframe,
          script_name: script.name,
          script_source: script.source,
          script_inputs: script.inputs,
          target: form.target,
          target2: plotTarget && compare === "plot" ? form.target2 : null,
          condition: plotTarget ? form.condition : "signal",
          price: plotTarget && (compare === "value" || channel) ? Number(form.price) : null,
          price2: plotTarget && channel ? Number(form.price2) : null,
          trigger: "once",
          frequency: form.frequency || "once_per_bar_close",
          name: form.name || `${script.name} on ${form.symbol}`,
          active: true,
        });
        if (plotTarget && compare === "value" && (body.price === null || Number.isNaN(body.price))) throw new Error("Enter a value");
        if (plotTarget && compare === "plot" && !body.target2) throw new Error("Choose the plot to compare with");
      } else {
        body = alertBody(form, {
          kind: "price",
          price: isLine ? null : form.price === "" || form.price === null ? null : Number(form.price),
          price2: channel ? Number(form.price2) : null,
          name: form.name || `${form.symbol} ${CONDITIONS.find((c) => c.v === form.condition)?.l.toLowerCase()}`,
          active: true,
          condition: form.condition === "signal" ? "crossing" : form.condition,
        });
        if (!isLine && (body.price === null || Number.isNaN(body.price))) throw new Error("Enter a price");
        if (channel && Number.isNaN(body.price2)) throw new Error("Enter the second price of the channel");
      }
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

  const plots = check ? check.plots : [];
  return (
    <Modal
      title={existing ? "Edit alert" : `Create alert on ${form.symbol}`}
      onClose={() => s.closeDialog()}
      width={500}
      footer={
        <>
          {error && <span className="form-error">{error}</span>}
          <span className="flex1" />
          <button className="btn" onClick={() => s.closeDialog()}>
            Cancel
          </button>
          <button className="btn primary" onClick={submit} disabled={busy || checking}>
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
        {!drawing && (!existing || existing.kind === "indicator" || !existing.line) && (
          <label className="form-row">
            <span>Condition on</span>
            <select value={source} onChange={(e) => setSource(e.target.value)} disabled={!!existing && existing.kind !== "indicator"}>
              <option value="price">Price</option>
              {source === "__existing__" && <option value="__existing__">{existing.script_name || "Script"} (saved)</option>}
              {indicators.map((ind) => (
                <option key={ind.id} value={ind.id}>
                  {nameOf(ind)}
                </option>
              ))}
            </select>
          </label>
        )}

        {isIndicator ? (
          <>
            <label className="form-row">
              <span>Timeframe</span>
              <span className="muted">{TF[script?.timeframe] ? TF[script.timeframe].l : script?.timeframe} (the chart's)</span>
            </label>
            {checking && <div className="muted small">Running the script on the server…</div>}
            {check && (
              <label className="form-row">
                <span>Alert when</span>
                <select value={form.target || ""} onChange={(e) => set({ target: e.target.value })}>
                  {check.alertconditions.map((t) => (
                    <option key={"ac" + t} value={`alertcondition:${t}`}>
                      alertcondition: {t}
                    </option>
                  ))}
                  {check.uses_alert && <option value="alert">Any alert() function call</option>}
                  {plots.map((p) => (
                    <option key={"p" + p.title} value={`plot:${p.title}`}>
                      Plot: {p.title} (now {fmtVal(p.value)})
                    </option>
                  ))}
                </select>
              </label>
            )}
            {check && targetKind === "plot" && (
              <>
                <label className="form-row">
                  <span>Condition</span>
                  <select value={form.condition} onChange={(e) => set({ condition: e.target.value })}>
                    {CONDITIONS.map((c) => (
                      <option key={c.v} value={c.v}>
                        {c.l}
                      </option>
                    ))}
                  </select>
                </label>
                {!channel && (
                  <label className="form-row">
                    <span>Compare with</span>
                    <select value={compare} onChange={(e) => setCompare(e.target.value)}>
                      <option value="value">A value</option>
                      {plots.length > 1 && <option value="plot">Another plot</option>}
                    </select>
                  </label>
                )}
                {compare === "plot" && !channel ? (
                  <label className="form-row">
                    <span>Plot</span>
                    <select value={form.target2 || ""} onChange={(e) => set({ target2: e.target.value })}>
                      <option value="">—</option>
                      {plots
                        .filter((p) => `plot:${p.title}` !== form.target)
                        .map((p) => (
                          <option key={p.title} value={`plot:${p.title}`}>
                            {p.title} (now {fmtVal(p.value)})
                          </option>
                        ))}
                    </select>
                  </label>
                ) : (
                  <>
                    <label className="form-row">
                      <span>{channel ? "Upper value" : "Value"}</span>
                      <input type="number" step="any" value={form.price ?? ""} onChange={(e) => set({ price: e.target.value })} />
                    </label>
                    {channel && (
                      <label className="form-row">
                        <span>Lower value</span>
                        <input type="number" step="any" value={form.price2 ?? ""} onChange={(e) => set({ price2: e.target.value })} />
                      </label>
                    )}
                  </>
                )}
              </>
            )}
            <label className="form-row">
              <span>Frequency</span>
              <select value={form.frequency || "once_per_bar_close"} onChange={(e) => set({ frequency: e.target.value })}>
                {FREQUENCIES.map((f) => (
                  <option key={f.v} value={f.v}>
                    {f.l}
                  </option>
                ))}
              </select>
            </label>
          </>
        ) : (
          <>
            <label className="form-row">
              <span>Condition</span>
              <select value={form.condition === "signal" ? "crossing" : form.condition} onChange={(e) => set({ condition: e.target.value })}>
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
          </>
        )}
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
          <textarea
            rows={3}
            value={form.message}
            onChange={(e) => set({ message: e.target.value })}
            placeholder={isIndicator ? "{{ticker}} {{interval}}: value {{value}} (close {{close}})" : "{{ticker}} crossed {{level}} — price {{close}}"}
          />
        </label>
        <label className="form-row">
          <span>Send to Telegram</span>
          <input type="checkbox" checked={form.notify_telegram !== false} onChange={(e) => set({ notify_telegram: e.target.checked })} />
        </label>
        <div className="muted small">
          Alerts run on the backend, so they fire even when this page is closed.{" "}
          {isIndicator
            ? "Placeholders: {{ticker}} {{interval}} {{close}} {{value}} {{level}}. An empty message uses the script's alertcondition/alert() message."
            : "Placeholders: {{ticker}} {{close}} {{level}}."}
        </div>
      </div>
    </Modal>
  );
}
