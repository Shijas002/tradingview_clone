// Floating toolbar shown above the chart when a drawing is selected.
import React from "react";
import { controllers } from "../chart/registry.js";
import { LINE_STYLES, TOOLS } from "../chart/drawings/tools.js";
import { symbolKey, useStore } from "../store/useStore.js";
import { uid } from "../utils/market.js";
import { Icon } from "./Icons.jsx";
import { toHex6 } from "./ui.jsx";

const LINE_TYPES = new Set(["trendline", "ray", "extended", "arrow", "channel"]);
const FILL_TYPES = new Set(["rect", "channel"]);
const ALERTABLE = new Set(["trendline", "ray", "extended", "arrow", "hline", "hray", "rect", "pricerange"]);

export default function DrawingStyleBar({ paneId, drawing }) {
  const pane = useStore((s) => s.panes.find((p) => p.id === paneId));
  const key = pane && pane.provider ? symbolKey(pane.provider, pane.symbol) : null;
  const live = useStore((s) => (key ? (s.drawingsBySymbol[key] || []).find((d) => d.id === drawing.id) : null));
  if (!live || !key) return null;
  const st = live.style || {};
  const ctrl = controllers.get(paneId);

  const update = (patch) => ctrl && ctrl.updateSelectedDrawingStyle(patch);
  const setDrawingProps = (patch) => {
    const s = useStore.getState();
    const list = (s.drawingsBySymbol[key] || []).map((d) => (d.id === live.id ? { ...d, ...patch } : d));
    s.setDrawings(key, list);
  };
  const remove = () => ctrl && ctrl.deleteSelectedDrawing();
  const clone = () => {
    const s = useStore.getState();
    const copy = { ...live, id: uid("drw"), points: live.points.map((p) => ({ ...p, price: p.price * 1.002 })), style: { ...live.style } };
    s.setDrawings(key, [...(s.drawingsBySymbol[key] || []), copy]);
  };

  return (
    <div className="style-bar" onMouseDown={(e) => e.stopPropagation()}>
      <span className="style-bar-title">{TOOLS[live.type] ? TOOLS[live.type].label : live.type}</span>
      {live.type !== "long" && live.type !== "short" && live.type !== "pricerange" && (
        <label className="style-color" title="Color">
          <input type="color" value={toHex6(st.color)} onChange={(e) => update({ color: e.target.value })} />
        </label>
      )}
      {FILL_TYPES.has(live.type) && (
        <label className="style-color" title="Fill color">
          <input type="color" value={toHex6(st.fill)} onChange={(e) => update({ fill: e.target.value + "26" })} />
          <span className="style-fill-tag">fill</span>
        </label>
      )}
      {live.type !== "text" && live.type !== "long" && live.type !== "short" && (
        <>
          <select value={st.width || 1} onChange={(e) => update({ width: Number(e.target.value) })} title="Line width">
            {[1, 2, 3, 4].map((w) => (
              <option key={w} value={w}>
                {w}px
              </option>
            ))}
          </select>
          <select value={st.lineStyle || 0} onChange={(e) => update({ lineStyle: Number(e.target.value) })} title="Line style">
            {LINE_STYLES.map((l) => (
              <option key={l.v} value={l.v}>
                {l.l}
              </option>
            ))}
          </select>
        </>
      )}
      {LINE_TYPES.has(live.type) && (
        <>
          <button className={st.extendLeft ? "active" : ""} onClick={() => update({ extendLeft: !st.extendLeft })} title="Extend left">
            ⟵
          </button>
          <button className={st.extendRight ? "active" : ""} onClick={() => update({ extendRight: !st.extendRight })} title="Extend right">
            ⟶
          </button>
        </>
      )}
      {live.type === "fib" && (
        <>
          <button className={st.extendRight ? "active" : ""} onClick={() => update({ extendRight: !st.extendRight })} title="Extend levels right">
            ⟶
          </button>
          <button className={st.showPrices !== false ? "active" : ""} onClick={() => update({ showPrices: st.showPrices === false })} title="Show prices">
            $
          </button>
        </>
      )}
      {live.type === "text" && (
        <>
          <select value={st.fontSize || 14} onChange={(e) => update({ fontSize: Number(e.target.value) })} title="Font size">
            {[10, 12, 14, 16, 20, 24, 32].map((f) => (
              <option key={f} value={f}>
                {f}
              </option>
            ))}
          </select>
          <button onClick={() => useStore.getState().openDialog({ type: "text", paneId, drawingId: live.id })}>Edit text</button>
        </>
      )}
      {(live.type === "long" || live.type === "short") && (
        <label className="style-qty">
          Qty
          <input type="number" min="0" step="any" value={st.qty ?? 1} onChange={(e) => update({ qty: Number(e.target.value) || 0 })} />
        </label>
      )}
      {(live.type === "long" || live.type === "short") && (
        <button onClick={() => useStore.getState().openDialog({ type: "journal", paneId, drawing: live })} title="Log this position in the trade journal">
          📓
        </button>
      )}
      {ALERTABLE.has(live.type) && (
        <button onClick={() => useStore.getState().openDialog({ type: "alert", paneId, drawing: live })} title="Create alert on this drawing">
          <Icon.bell />
        </button>
      )}
      <button className={live.locked ? "active" : ""} onClick={() => setDrawingProps({ locked: !live.locked })} title={live.locked ? "Unlock" : "Lock"}>
        <Icon.lock />
      </button>
      <button onClick={clone} title="Clone">
        <Icon.copy />
      </button>
      <button onClick={remove} title="Delete (Del)" className="danger">
        <Icon.trash />
      </button>
    </div>
  );
}
