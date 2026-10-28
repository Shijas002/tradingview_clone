// Right-click menu on a chart.
import React, { useRef } from "react";
import { controllers } from "../chart/registry.js";
import { symbolKey, useStore } from "../store/useStore.js";
import { useOutsideClick } from "./ui.jsx";
import { downloadScreenshot } from "./TopBar.jsx";

export default function ContextMenu({ extraItems }) {
  const menu = useStore((s) => s.contextMenu);
  const ref = useRef(null);
  const close = () => useStore.getState().setContextMenu(null);
  useOutsideClick(ref, close, !!menu);
  if (!menu) return null;
  const s = useStore.getState();
  const pane = s.panes.find((p) => p.id === menu.paneId);
  if (!pane) return null;
  const ctrl = controllers.get(pane.id);
  const ps = pane.priceScale || { mode: "normal", inverted: false };
  const run = (fn) => () => {
    fn();
    close();
  };
  const left = Math.min(menu.clientX, window.innerWidth - 240);
  const top = Math.min(menu.clientY, window.innerHeight - 360);
  const price = menu.price !== null && menu.price !== undefined ? menu.price.toFixed(pane.digits ?? 5) : null;

  return (
    <div className="context-menu" ref={ref} style={{ left, top }}>
      <button onClick={run(() => ctrl && ctrl.resetView())}>Reset chart view</button>
      {typeof extraItems === "function" && extraItems({ pane, price: menu.price, close })}
      {price && <button onClick={run(() => s.openDialog({ type: "alert", paneId: pane.id, price: menu.price }))}>🔔 Add alert at {price}</button>}
      {price && <button onClick={run(() => s.openDialog({ type: "journal", paneId: pane.id, price: Number(price) }))}>📓 Log trade at {price}</button>}
      {price && <button onClick={run(() => navigator.clipboard && navigator.clipboard.writeText(price))}>Copy price {price}</button>}
      <div className="cm-sep" />
      <button className={ps.mode === "log" ? "checked" : ""} onClick={run(() => s.updatePane(pane.id, { priceScale: { ...ps, mode: ps.mode === "log" ? "normal" : "log" } }))}>
        Log scale
      </button>
      <button className={ps.mode === "percent" ? "checked" : ""} onClick={run(() => s.updatePane(pane.id, { priceScale: { ...ps, mode: ps.mode === "percent" ? "normal" : "percent" } }))}>
        Percent scale
      </button>
      <button className={ps.inverted ? "checked" : ""} onClick={run(() => s.updatePane(pane.id, { priceScale: { ...ps, inverted: !ps.inverted } }))}>
        Invert scale
      </button>
      <div className="cm-sep" />
      <button onClick={run(() => s.openDialog({ type: "indicators", paneId: pane.id }))}>Add indicator…</button>
      <button onClick={run(downloadScreenshot)}>Download image</button>
      <div className="cm-sep" />
      <button
        className="danger"
        onClick={run(() => {
          if (pane.provider) s.setDrawings(symbolKey(pane.provider, pane.symbol), []);
        })}
      >
        Remove drawings
      </button>
      <button className="danger" onClick={run(() => s.updatePane(pane.id, { indicators: [] }))}>
        Remove indicators
      </button>
    </div>
  );
}
