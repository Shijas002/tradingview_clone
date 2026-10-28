// Top toolbar: symbol, timeframe, chart type, indicators, layout, undo/redo, screenshot, settings.
import React from "react";
import { activeController, lastPrice } from "../chart/registry.js";
import { LAYOUTS, useStore } from "../store/useStore.js";
import { CHART_TYPES, TIMEFRAMES } from "../utils/market.js";
import { Icon } from "./Icons.jsx";
import { Dropdown, IconButton, MenuItem } from "./ui.jsx";

export function downloadScreenshot() {
  const s = useStore.getState();
  const ctrl = activeController(s);
  const pane = s.activePane();
  if (!ctrl) return;
  const canvas = ctrl.screenshot();
  const a = document.createElement("a");
  const ts = new Date().toISOString().replace(/[:T]/g, "-").slice(0, 19);
  a.download = `${pane.symbol}_${pane.timeframe}_${ts}.png`;
  a.href = canvas.toDataURL("image/png");
  a.click();
}

export default function TopBar() {
  const panes = useStore((s) => s.panes);
  const activePaneId = useStore((s) => s.activePaneId);
  const layout = useStore((s) => s.layout);
  const settings = useStore((s) => s.settings);
  const hist = useStore((s) => s.drawingHistory);
  const pane = panes.find((p) => p.id === activePaneId) || panes[0];
  const s = useStore.getState();
  if (!pane) return null;
  const chartType = CHART_TYPES.find((c) => c.v === pane.chartType) || CHART_TYPES[0];

  return (
    <header className="topbar">
      <div className="brand" title="TradingView Clone">
        <svg viewBox="0 0 24 24" width="22" height="22">
          <path d="M2 18 L8 10 L12 14 L20 4" stroke="#2962ff" strokeWidth="2.4" fill="none" strokeLinecap="round" />
        </svg>
      </div>
      <button className="tb-btn symbol-btn" onClick={() => s.openDialog({ type: "symbol", paneId: pane.id })} title="Symbol search">
        <Icon.search />
        <span>{pane.symbol || "Symbol"}</span>
      </button>
      <span className="tb-sep" />
      <div className="tf-group">
        {TIMEFRAMES.map((tf) => (
          <button key={tf.v} className={`tb-btn tf ${pane.timeframe === tf.v ? "active" : ""}`} onClick={() => s.updatePane(pane.id, { timeframe: tf.v })} title={tf.v}>
            {tf.l}
          </button>
        ))}
      </div>
      <span className="tb-sep" />
      <Dropdown
        title="Chart type"
        label={
          <>
            <Icon.candles />
            <span className="hide-sm">{chartType.l}</span>
          </>
        }
      >
        {CHART_TYPES.map((c) => (
          <MenuItem key={c.v} active={pane.chartType === c.v} onClick={() => s.updatePane(pane.id, { chartType: c.v })}>
            {c.l}
          </MenuItem>
        ))}
      </Dropdown>
      <span className="tb-sep" />
      <button className="tb-btn" onClick={() => s.openDialog({ type: "indicators", paneId: pane.id })} title="Indicators & strategies ( / )">
        <Icon.fx />
        <span className="hide-sm">Indicators</span>
      </button>
      <button
        className="tb-btn"
        title="Create alert (Alt+A)"
        onClick={() => {
          useStore.getState().openDialog({ type: "alert", paneId: pane.id, price: lastPrice(pane.id) });
        }}
      >
        <Icon.bell />
        <span className="hide-sm">Alert</span>
      </button>
      <button
        className="tb-btn"
        title="Bar Replay: replay history bar by bar"
        onClick={() => {
          const c = activeController(useStore.getState());
          if (c) c.armReplay();
        }}
      >
        <span className="replay-icon">⏪</span>
        <span className="hide-sm">Replay</span>
      </button>
      <span className="tb-sep" />
      <IconButton icon="undo" title="Undo drawing (Ctrl+Z)" onClick={() => s.undoDrawings()} disabled={!hist.past.length} />
      <IconButton icon="redo" title="Redo drawing (Ctrl+Y)" onClick={() => s.redoDrawings()} disabled={!hist.future.length} />
      <div className="tb-spacer" />
      <Dropdown title="Layout" align="right" label={<Icon.layout />}>
        {Object.entries(LAYOUTS).map(([k, v]) => (
          <MenuItem key={k} active={layout === k} onClick={() => s.setLayout(k)}>
            {v.label}
          </MenuItem>
        ))}
      </Dropdown>
      <IconButton icon="camera" title="Download chart image" onClick={downloadScreenshot} />
      <IconButton icon="gear" title="Chart settings" onClick={() => s.openDialog({ type: "chartSettings" })} />
      <IconButton icon="panelBottom" title="Toggle bottom panel (Pine Editor, Strategy Tester)" active={settings.bottomPanel} onClick={() => s.updateSettings({ bottomPanel: !settings.bottomPanel })} />
      <IconButton icon="panelRight" title="Toggle watchlist" active={settings.rightPanel} onClick={() => s.updateSettings({ rightPanel: !settings.rightPanel })} />
    </header>
  );
}
