// Inputs dialog generated from the script's input.*() declarations.
import React, { useState } from "react";
import { useStore } from "../../store/useStore.js";
import { TIMEFRAMES } from "../../utils/market.js";
import { Modal, toHex6 } from "../ui.jsx";

const SOURCES = ["open", "high", "low", "close", "hl2", "hlc3", "ohlc4", "hlcc4"];

function InputField({ meta, value, onChange }) {
  const v = value === undefined ? meta.defval : value;
  switch (meta.type) {
    case "int":
    case "float":
      return (
        <input
          type="number"
          value={v}
          min={meta.minval}
          max={meta.maxval}
          step={meta.step || (meta.type === "int" ? 1 : "any")}
          onChange={(e) => {
            const n = meta.type === "int" ? parseInt(e.target.value, 10) : parseFloat(e.target.value);
            onChange(Number.isNaN(n) ? meta.defval : n);
          }}
        />
      );
    case "bool":
      return <input type="checkbox" checked={!!v} onChange={(e) => onChange(e.target.checked)} />;
    case "source":
      return (
        <select value={v} onChange={(e) => onChange(e.target.value)}>
          {(meta.options && meta.options.length ? meta.options : SOURCES).map((o) => (
            <option key={o} value={o}>
              {o}
            </option>
          ))}
        </select>
      );
    case "color":
      return <input type="color" value={toHex6(v)} onChange={(e) => onChange(e.target.value.toUpperCase() + "FF")} />;
    case "timeframe":
      return (
        <select value={v} onChange={(e) => onChange(e.target.value)}>
          <option value="">Chart</option>
          {TIMEFRAMES.map((t) => (
            <option key={t.v} value={t.pine}>
              {t.l}
            </option>
          ))}
        </select>
      );
    case "string":
    case "enum":
      if (meta.options && meta.options.length) {
        return (
          <select value={v} onChange={(e) => onChange(e.target.value)}>
            {meta.options.map((o) => (
              <option key={String(o)} value={o}>
                {String(o)}
              </option>
            ))}
          </select>
        );
      }
      return <input type="text" value={v ?? ""} onChange={(e) => onChange(e.target.value)} />;
    case "text_area":
      return <textarea value={v ?? ""} onChange={(e) => onChange(e.target.value)} rows={3} />;
    default:
      return <input type="text" value={v ?? ""} onChange={(e) => onChange(e.target.value)} />;
  }
}

export default function IndicatorSettings({ dialog }) {
  const pane = useStore((s) => s.panes.find((p) => p.id === dialog.paneId));
  const info = useStore((s) => (s.paneRuntime[dialog.paneId]?.indicators || []).find((i) => i.id === dialog.indicatorId));
  const ind = pane ? pane.indicators.find((i) => i.id === dialog.indicatorId) : null;
  const [draft, setDraft] = useState(() => ({ ...(ind ? ind.inputs : {}) }));
  const s = useStore.getState();
  if (!pane || !ind) return null;
  const meta = info && info.inputsMeta ? info.inputsMeta : [];

  const apply = (close) => {
    s.updateIndicator(pane.id, ind.id, { inputs: draft });
    if (close) s.closeDialog();
  };

  const groups = [];
  for (const m of meta) {
    const g = m.group || "";
    let grp = groups.find((x) => x.name === g);
    if (!grp) groups.push((grp = { name: g, items: [] }));
    grp.items.push(m);
  }

  return (
    <Modal
      title={`${info ? info.name : ind.name} — settings`}
      onClose={() => s.closeDialog()}
      width={460}
      footer={
        <>
          <button className="btn" onClick={() => setDraft({})}>
            Defaults
          </button>
          <span className="flex1" />
          <button className="btn" onClick={() => s.closeDialog()}>
            Cancel
          </button>
          <button className="btn primary" onClick={() => apply(true)}>
            Ok
          </button>
        </>
      }
    >
      {!meta.length && <div className="empty">{info && info.error ? `Script error: ${info.error}` : "This script has no inputs."}</div>}
      <div className="form-grid">
        {groups.map((g) => (
          <React.Fragment key={g.name || "_"}>
            {g.name && <div className="form-group-title">{g.name}</div>}
            {g.items.map((m) => (
              <label key={m.id} className="form-row" title={m.tooltip || ""}>
                <span>{m.title || m.name}</span>
                <InputField meta={m} value={draft[m.id]} onChange={(val) => setDraft((d) => ({ ...d, [m.id]: val }))} />
              </label>
            ))}
          </React.Fragment>
        ))}
      </div>
      <label className="form-row">
        <span>Visible</span>
        <input type="checkbox" checked={ind.visible !== false} onChange={(e) => s.updateIndicator(pane.id, ind.id, { visible: e.target.checked })} />
      </label>
      {info && info.elapsedMs !== null && info.elapsedMs !== undefined && <div className="muted small">Last calculation: {info.elapsedMs} ms</div>}
    </Modal>
  );
}
