// Left vertical toolbar with drawing tools.
import React from "react";
import { TOOLS } from "../chart/drawings/tools.js";
import { activeController } from "../chart/registry.js";
import { symbolKey, useStore } from "../store/useStore.js";
import { Icon } from "./Icons.jsx";

const GROUPS = [["cursor"], ["trendline", "ray", "extended", "arrow"], ["hline", "hray", "vline", "cross"], ["channel", "fib"], ["rect", "brush", "text"], ["pricerange", "long", "short"]];

export default function DrawingToolbar() {
  const tool = useStore((s) => s.tool);
  const magnet = useStore((s) => s.magnet);
  const locked = useStore((s) => s.drawingsLocked);
  const hidden = useStore((s) => s.drawingsHidden);
  const stay = useStore((s) => s.settings.stayInDrawingMode);
  const s = useStore.getState();

  const removeAll = () => {
    const st = useStore.getState();
    const pane = st.activePane();
    if (!pane || !pane.provider) return;
    const key = symbolKey(pane.provider, pane.symbol);
    if ((st.drawingsBySymbol[key] || []).length && window.confirm(`Remove all drawings on ${pane.symbol}?`)) {
      st.setDrawings(key, []);
      st.setSelectedDrawing(null);
    }
  };

  return (
    <nav className="draw-toolbar" aria-label="Drawing tools">
      {GROUPS.map((g, gi) => (
        <div className="draw-group" key={gi}>
          {g.map((t) => {
            const I = Icon[t];
            const def = TOOLS[t];
            const label = t === "cursor" ? "Cursor (Esc)" : `${def.label}${def.hotkey ? ` (${def.hotkey})` : ""}`;
            return (
              <button key={t} className={`draw-btn ${tool === t ? "active" : ""}`} title={label} onClick={() => s.setTool(tool === t && t !== "cursor" ? "cursor" : t)}>
                <I />
              </button>
            );
          })}
        </div>
      ))}
      <div className="draw-group">
        <button className={`draw-btn ${magnet ? "active" : ""}`} title="Magnet mode: snap to OHLC" onClick={() => s.toggle("magnet")}>
          <Icon.magnet />
        </button>
        <button className={`draw-btn ${stay ? "active" : ""}`} title="Stay in drawing mode" onClick={() => s.updateSettings({ stayInDrawingMode: !stay })}>
          <Icon.plus />
        </button>
        <button className={`draw-btn ${locked ? "active" : ""}`} title="Lock all drawings" onClick={() => s.toggle("drawingsLocked")}>
          <Icon.lock />
        </button>
        <button className={`draw-btn ${hidden ? "active" : ""}`} title="Hide all drawings" onClick={() => s.toggle("drawingsHidden")}>
          {hidden ? <Icon.eyeOff /> : <Icon.eye />}
        </button>
        <button className="draw-btn" title="Remove all drawings" onClick={removeAll}>
          <Icon.trash />
        </button>
      </div>
      <div className="draw-group draw-bottom">
        <button
          className="draw-btn"
          title="Reset chart view"
          onClick={() => {
            const c = activeController(useStore.getState());
            if (c) c.resetView();
          }}
        >
          ⟲
        </button>
      </div>
    </nav>
  );
}
