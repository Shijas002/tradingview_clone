// Log / edit a trade in the journal (Phase 5).
import React, { useEffect, useMemo, useState } from "react";
import { createJournal, updateJournal } from "../../api/client.js";
import { captureChart, draftFromDrawing, draftFromPane, draftFromTrade, fmtMoney, loadJournal, previewMetrics } from "../../journal/journal.js";
import { useStore } from "../../store/useStore.js";
import { TIMEFRAMES } from "../../utils/market.js";
import { Modal } from "../ui.jsx";

function toLocalInput(sec) {
  if (!sec) return "";
  const d = new Date(sec * 1000);
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
const fromLocalInput = (v) => (v ? Math.floor(new Date(v).getTime() / 1000) : null);
const num = (v) => (v === "" || v === null || v === undefined || Number.isNaN(Number(v)) ? null : Number(v));

export default function JournalDialog({ dialog }) {
  const pane = useStore((s) => s.panes.find((p) => p.id === dialog.paneId) || s.panes.find((p) => p.id === s.activePaneId) || s.panes[0]);
  const journal = useStore((s) => s.journal);
  const notion = useStore((s) => s.notion);
  const existing = dialog.entry || null;
  const digits = pane?.digits ?? 5;

  const initial = useMemo(() => {
    if (existing) return { ...existing, ...(dialog.close || {}), tagsText: (existing.tags || []).join(", ") };
    let draft;
    if (dialog.drawing) draft = draftFromDrawing(pane, dialog.drawing);
    else if (dialog.trade) draft = draftFromTrade(pane, dialog.trade, dialog.strategyName || "");
    else draft = draftFromPane(pane, dialog.price ?? null);
    if (dialog.close) draft = { ...draft, ...dialog.close };
    return { ...draft, tagsText: (draft.tags || []).join(", ") };
  }, [existing, dialog, pane]);

  const [form, setForm] = useState(initial);
  const [shot, setShot] = useState(null); // new image (data URL)
  const [removeShot, setRemoveShot] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const s = useStore.getState();
  const set = (patch) => setForm((f) => ({ ...f, ...patch }));

  // New trades from the chart get a screenshot automatically (TradingView-style "snapshot")
  useEffect(() => {
    if (!existing && dialog.capture !== false && pane) {
      const url = captureChart(pane.id);
      if (url) setShot(url);
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const setups = useMemo(() => [...new Set(journal.map((j) => j.setup).filter(Boolean))].slice(0, 50), [journal]);
  const { pnl, r } = previewMetrics(form);
  const risk = num(form.stop_loss) && num(form.entry_price) ? Math.abs(num(form.entry_price) - num(form.stop_loss)) * (num(form.quantity) || 0) : null;
  const rr =
    num(form.stop_loss) && num(form.take_profit) && num(form.entry_price) && Math.abs(num(form.entry_price) - num(form.stop_loss)) > 0
      ? Math.abs(num(form.take_profit) - num(form.entry_price)) / Math.abs(num(form.entry_price) - num(form.stop_loss))
      : null;
  const existingShotUrl = existing && existing.has_screenshot && !removeShot ? `/api/journal/${existing.id}/screenshot?v=${existing.screenshot_version}` : null;
  const preview = shot || existingShotUrl;

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const body = {
        provider: form.provider || "",
        symbol: String(form.symbol || "").trim(),
        timeframe: form.timeframe || "",
        side: form.side,
        status: form.status,
        entry_time: form.entry_time || null,
        entry_price: num(form.entry_price),
        quantity: num(form.quantity),
        exit_time: form.status === "closed" ? form.exit_time || null : null,
        exit_price: form.status === "closed" ? num(form.exit_price) : null,
        stop_loss: num(form.stop_loss),
        take_profit: num(form.take_profit),
        fees: num(form.fees) || 0,
        setup: (form.setup || "").trim(),
        tags: String(form.tagsText || "").split(",").map((t) => t.trim()).filter(Boolean),
        rating: form.rating ? Number(form.rating) : null,
        notes: form.notes || "",
      };
      if (!body.symbol) throw new Error("Enter a symbol");
      if (!body.entry_price || body.entry_price <= 0) throw new Error("Enter the entry price");
      if (!body.quantity || body.quantity <= 0) throw new Error("Enter a quantity above 0");
      if (body.status === "closed" && !body.exit_price) throw new Error("A closed trade needs an exit price");
      if (existing) {
        const clear = ["exit_time", "exit_price", "stop_loss", "take_profit", "rating", "entry_time"].filter((k) => body[k] === null);
        const patch = Object.fromEntries(Object.entries(body).filter(([, v]) => v !== null));
        await updateJournal(existing.id, { ...patch, clear, screenshot: shot || undefined, remove_screenshot: removeShot && !shot });
      } else {
        await createJournal({ ...body, screenshot: shot || undefined });
      }
      await loadJournal();
      s.toast(existing ? "Journal entry saved" : `Trade logged${notion && notion.configured ? " - syncing to Notion" : ""}`, "ok", 2500);
      s.closeDialog();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const closed = form.status === "closed";
  return (
    <Modal
      title={existing ? `Edit trade · ${existing.symbol}` : `Log trade · ${form.symbol}`}
      onClose={() => s.closeDialog()}
      width={720}
      className="journal-dialog"
      footer={
        <>
          {error && <span className="form-error">{error}</span>}
          <span className="flex1" />
          <button className="btn" onClick={() => s.closeDialog()}>
            Cancel
          </button>
          <button className="btn primary" onClick={submit} disabled={busy}>
            {busy ? "Saving…" : existing ? "Save" : "Log trade"}
          </button>
        </>
      }
    >
      <div className="journal-form">
        <div className="form-grid">
          <div className="form-group-title">Trade</div>
          <label className="form-row">
            <span>Symbol</span>
            <input type="text" value={form.symbol} onChange={(e) => set({ symbol: e.target.value })} />
          </label>
          <label className="form-row">
            <span>Timeframe</span>
            <select value={form.timeframe} onChange={(e) => set({ timeframe: e.target.value })}>
              <option value="">—</option>
              {TIMEFRAMES.map((t) => (
                <option key={t.v} value={t.v}>
                  {t.l}
                </option>
              ))}
            </select>
          </label>
          <div className="form-row">
            <span>Side</span>
            <span className="seg">
              <button className={form.side === "long" ? "active up" : ""} onClick={() => set({ side: "long" })}>
                Long
              </button>
              <button className={form.side === "short" ? "active down" : ""} onClick={() => set({ side: "short" })}>
                Short
              </button>
            </span>
          </div>
          <div className="form-row">
            <span>Status</span>
            <span className="seg">
              <button className={!closed ? "active" : ""} onClick={() => set({ status: "open" })}>
                Open
              </button>
              <button className={closed ? "active" : ""} onClick={() => set({ status: "closed", exit_time: form.exit_time || Math.floor(Date.now() / 1000) })}>
                Closed
              </button>
            </span>
          </div>
          <label className="form-row">
            <span>Entry price</span>
            <input type="number" step="any" value={form.entry_price ?? ""} onChange={(e) => set({ entry_price: e.target.value })} />
          </label>
          <label className="form-row">
            <span>Quantity</span>
            <input type="number" step="any" min="0" value={form.quantity ?? ""} onChange={(e) => set({ quantity: e.target.value })} />
          </label>
          <label className="form-row">
            <span>Entry time</span>
            <input type="datetime-local" value={toLocalInput(form.entry_time)} onChange={(e) => set({ entry_time: fromLocalInput(e.target.value) })} />
          </label>
          <label className="form-row">
            <span>Stop loss</span>
            <input type="number" step="any" value={form.stop_loss ?? ""} onChange={(e) => set({ stop_loss: e.target.value })} />
          </label>
          <label className="form-row">
            <span>Take profit</span>
            <input type="number" step="any" value={form.take_profit ?? ""} onChange={(e) => set({ take_profit: e.target.value })} />
          </label>
          {closed && (
            <>
              <label className="form-row">
                <span>Exit price</span>
                <input type="number" step="any" value={form.exit_price ?? ""} onChange={(e) => set({ exit_price: e.target.value })} autoFocus={!!dialog.close} />
              </label>
              <label className="form-row">
                <span>Exit time</span>
                <input type="datetime-local" value={toLocalInput(form.exit_time)} onChange={(e) => set({ exit_time: fromLocalInput(e.target.value) })} />
              </label>
            </>
          )}
          <label className="form-row">
            <span>Fees / commission</span>
            <input type="number" step="any" min="0" value={form.fees ?? 0} onChange={(e) => set({ fees: e.target.value })} />
          </label>
          <div className="journal-metrics">
            {rr !== null && <span>Planned R:R {rr.toFixed(2)}</span>}
            {risk !== null && <span>Risk {fmtMoney(risk).replace("+", "")}</span>}
            {closed && pnl !== null && <span className={pnl >= 0 ? "up" : "down"}>P&amp;L {fmtMoney(pnl)}</span>}
            {closed && r !== null && <span className={r >= 0 ? "up" : "down"}>{r >= 0 ? "+" : ""}{r.toFixed(2)} R</span>}
          </div>
        </div>
        <div className="form-grid">
          <div className="form-group-title">Review</div>
          <label className="form-row">
            <span>Setup</span>
            <input type="text" list="journal-setups" value={form.setup} onChange={(e) => set({ setup: e.target.value })} placeholder="e.g. Breakout" />
            <datalist id="journal-setups">
              {setups.map((x) => (
                <option key={x} value={x} />
              ))}
            </datalist>
          </label>
          <label className="form-row">
            <span>Tags</span>
            <input type="text" value={form.tagsText} onChange={(e) => set({ tagsText: e.target.value })} placeholder="trend, A+, news" />
          </label>
          <label className="form-row">
            <span>Execution</span>
            <select value={form.rating ?? ""} onChange={(e) => set({ rating: e.target.value ? Number(e.target.value) : null })}>
              <option value="">—</option>
              {[5, 4, 3, 2, 1].map((n) => (
                <option key={n} value={n}>
                  {"★".repeat(n) + "☆".repeat(5 - n)}
                </option>
              ))}
            </select>
          </label>
          <label className="form-row form-row-top">
            <span>Notes</span>
            <textarea rows={5} value={form.notes} onChange={(e) => set({ notes: e.target.value })} placeholder="Why did you take it? What went well / badly?" />
          </label>
          <div className="form-group-title">Chart screenshot</div>
          <div className="journal-shot">
            {preview ? <img src={preview} alt="chart screenshot" /> : <div className="muted small">No screenshot</div>}
            <div className="btn-row">
              <button
                className="btn small"
                onClick={() => {
                  const url = captureChart(pane.id);
                  if (url) {
                    setShot(url);
                    setRemoveShot(false);
                  }
                }}
                title={`Capture ${pane.symbol} as it is now`}
              >
                📷 {preview ? "Retake" : "Capture chart"}
              </button>
              {preview && (
                <button
                  className="btn small"
                  onClick={() => {
                    setShot(null);
                    setRemoveShot(true);
                  }}
                >
                  Remove
                </button>
              )}
            </div>
          </div>
          {notion && notion.configured && <div className="muted small">Saved locally and copied to Notion ({notion.database_title || "journal database"}).</div>}
        </div>
      </div>
    </Modal>
  );
}
