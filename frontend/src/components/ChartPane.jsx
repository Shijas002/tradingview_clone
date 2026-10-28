// One chart in the layout grid: hosts a ChartController and its legend overlay.
import React, { useEffect, useMemo, useRef, useState } from "react";
import { ChartController } from "../chart/ChartController.js";
import { controllers } from "../chart/registry.js";
import { symbolKey, useStore } from "../store/useStore.js";
import { TF, fmtNumber } from "../utils/market.js";
import { Icon } from "./Icons.jsx";
import DrawingStyleBar from "./DrawingStyleBar.jsx";
import { useChartZone } from "../chart/useChartZone.js";
import { BrokerLoginButton } from "../broker/BrokerLogin.jsx";

const EMPTY = [];

function inputsSummary(meta, inputs) {
  if (!meta || !meta.length) return "";
  return meta
    .filter((m) => m.display !== "none" && m.type !== "color" && m.type !== "bool")
    .slice(0, 5)
    .map((m) => {
      const v = inputs && inputs[m.id] !== undefined ? inputs[m.id] : m.defval;
      return v === "" ? "chart" : String(v);
    })
    .join(" ");
}

export default function ChartPane({ pane, active }) {
  const hostRef = useRef(null);
  const ctrlRef = useRef(null);
  const digitsRef = useRef(pane.digits);
  digitsRef.current = pane.digits; // read by the load effect below (same render)
  const [legend, setLegend] = useState({ bar: null, prev: null, indicators: [] });
  const [quote, setQuote] = useState(null);
  const [replay, setReplay] = useState({ armed: false, active: false });

  const store = useStore;
  const theme = useStore((s) => s.settings.theme);
  const showVolume = useStore((s) => s.settings.showVolume);
  const grid = useStore((s) => s.settings.grid);
  const crosshair = useStore((s) => s.settings.crosshair);
  const tool = useStore((s) => s.tool);
  const magnet = useStore((s) => s.magnet);
  const locked = useStore((s) => s.drawingsLocked);
  const hidden = useStore((s) => s.drawingsHidden);
  const dkey = pane.provider ? symbolKey(pane.provider, pane.symbol) : null;
  const drawings = useStore((s) => (dkey ? s.drawingsBySymbol[dkey] : null)) || EMPTY;
  const runtime = useStore((s) => s.paneRuntime[pane.id]) || {};
  const selected = useStore((s) => s.selectedDrawing);
  const providerStatus = useStore((s) => s.providers.find((p) => p.name === pane.provider));

  // ---- create / destroy controller
  useEffect(() => {
    const paneId = pane.id;
    const cb = {
      onLegend: (l) => setLegend(l),
      onQuote: (q) => setQuote(q),
      onState: (patch) => store.getState().setPaneRuntime(paneId, patch),
      onIndicators: (list) => store.getState().setPaneRuntime(paneId, { indicators: list }),
      onDrawingsChange: (list) => {
        const p = store.getState().panes.find((x) => x.id === paneId);
        if (p && p.provider) store.getState().setDrawings(symbolKey(p.provider, p.symbol), list);
      },
      onDrawingSelect: (d) => store.getState().setSelectedDrawing(d ? { paneId, drawing: d } : null),
      onToolDone: () => {
        if (!store.getState().settings.stayInDrawingMode) store.getState().setTool("cursor");
      },
      onEditText: (d) => store.getState().openDialog({ type: "text", paneId, drawingId: d.id }),
      onFocus: () => store.getState().setActivePane(paneId),
      onDigits: (digits) => store.getState().updatePane(paneId, { digits }),
      onReplay: (r) => {
        setReplay(r);
        store.getState().setPaneRuntime(paneId, { replay: r });
      },
      onContextMenu: (info) => {
        store.getState().setActivePane(paneId);
        store.getState().setContextMenu({ ...info, paneId });
      },
    };
    const ctrl = new ChartController(hostRef.current, cb);
    ctrlRef.current = ctrl;
    controllers.set(paneId, ctrl);
    return () => {
      controllers.delete(paneId);
      ctrl.destroy();
      ctrlRef.current = null;
    };
  }, [pane.id, store]);

  // ---- data source
  useEffect(() => {
    const ctrl = ctrlRef.current;
    if (!ctrl || !pane.provider || !pane.symbol) return;
    setQuote(null);
    ctrl.load({ provider: pane.provider, symbol: pane.symbol, timeframe: pane.timeframe, digits: digitsRef.current });
  }, [pane.provider, pane.symbol, pane.timeframe]);
  useEffect(() => {
    if (ctrlRef.current && typeof pane.digits === "number") ctrlRef.current.setDigits(pane.digits);
  }, [pane.digits]);

  // reload when the "auto" provider switches source (MT5 <-> OANDA)
  const activeSource = providerStatus && providerStatus.details ? providerStatus.details.active_source : null;
  const connected = providerStatus ? providerStatus.connected : null;
  const prevSource = useRef(activeSource);
  const prevConnected = useRef(connected);
  useEffect(() => {
    const ctrl = ctrlRef.current;
    const switched = prevSource.current && activeSource && prevSource.current !== activeSource;
    const recovered = prevConnected.current === false && connected === true;
    prevSource.current = activeSource;
    prevConnected.current = connected;
    if (ctrl && (switched || recovered)) ctrl.reload();
  }, [activeSource, connected]);

  useEffect(() => ctrlRef.current && ctrlRef.current.setChartType(pane.chartType), [pane.chartType]);
  useEffect(() => ctrlRef.current && ctrlRef.current.setIndicators(pane.indicators), [pane.indicators]);
  useEffect(() => ctrlRef.current && ctrlRef.current.setDrawings(drawings), [drawings]);
  useEffect(() => ctrlRef.current && ctrlRef.current.setTool(active ? tool : "cursor"), [tool, active]);
  useEffect(() => ctrlRef.current && ctrlRef.current.setMagnet(magnet), [magnet]);
  useEffect(() => ctrlRef.current && ctrlRef.current.setDrawingsLocked(locked), [locked]);
  useEffect(() => ctrlRef.current && ctrlRef.current.setDrawingsHidden(hidden), [hidden]);
  useEffect(() => ctrlRef.current && ctrlRef.current.setTheme(theme), [theme]);
  useEffect(() => ctrlRef.current && ctrlRef.current.setShowVolume(showVolume), [showVolume]);
  useEffect(() => ctrlRef.current && ctrlRef.current.setGrid(grid), [grid]);
  useEffect(() => ctrlRef.current && ctrlRef.current.setCrosshairMode(crosshair), [crosshair]);
  useEffect(() => ctrlRef.current && ctrlRef.current.setPriceScale(pane.priceScale || {}), [pane.priceScale]);
  const zone = useChartZone(pane.id);
  useEffect(() => ctrlRef.current && ctrlRef.current.setTimezone(zone.tz, zone.shift), [zone.tz, zone.shift, pane.id]);

  // active price alerts of this symbol -> dashed lines on the chart
  const alerts = useStore((s) => s.alerts);
  const alertLevels = useMemo(() => {
    const out = [];
    for (const a of alerts) {
      if (!a.active || a.line || a.provider !== pane.provider || String(a.symbol).toLowerCase() !== String(pane.symbol).toLowerCase()) continue;
      if (a.price !== null && a.price !== undefined) out.push({ price: a.price, title: a.name && a.name.length < 14 ? a.name : "" });
      if (a.price2 !== null && a.price2 !== undefined) out.push({ price: a.price2, title: "" });
    }
    return out;
  }, [alerts, pane.provider, pane.symbol]);
  useEffect(() => ctrlRef.current && ctrlRef.current.setAlertLines(alertLevels), [alertLevels]);

  const indInfo = useMemo(() => new Map((runtime.indicators || []).map((i) => [i.id, i])), [runtime.indicators]);
  const legendVals = useMemo(() => new Map((legend.indicators || []).map((i) => [i.id, i.values])), [legend.indicators]);

  const bar = legend.bar;
  const chg = bar && legend.prev ? bar.close - legend.prev.close : bar ? bar.close - bar.open : 0;
  const chgPct = bar ? (chg / (legend.prev ? legend.prev.close : bar.open)) * 100 : 0;
  const cls = chg >= 0 ? "up" : "down";
  const d = pane.digits ?? 5;
  const s = useStore.getState();

  return (
    <div className={`chart-pane ${active ? "active" : ""}`} onMouseDown={() => s.setActivePane(pane.id)}>
      <div className="chart-host" ref={hostRef} />
      <div className="legend">
        <div className="legend-row legend-main">
          <button className="legend-symbol" onClick={() => s.openDialog({ type: "symbol", paneId: pane.id })} title="Change symbol">
            {pane.symbol || "—"}
          </button>
          <span className="legend-meta">
            {TF[pane.timeframe] ? TF[pane.timeframe].l : pane.timeframe} · {pane.provider || "no provider"}
            {activeSource ? ` → ${activeSource}` : ""}
          </span>
          {bar && (
            <span className="legend-ohlc">
              <i>O</i>
              <b className={cls}>{bar.open.toFixed(d)}</b>
              <i>H</i>
              <b className={cls}>{bar.high.toFixed(d)}</b>
              <i>L</i>
              <b className={cls}>{bar.low.toFixed(d)}</b>
              <i>C</i>
              <b className={cls}>{bar.close.toFixed(d)}</b>
              <b className={cls}>
                {chg >= 0 ? "+" : ""}
                {chg.toFixed(d)} ({chgPct >= 0 ? "+" : ""}
                {chgPct.toFixed(2)}%)
              </b>
              <i>Vol</i>
              <b>{fmtNumber(bar.volume || 0, 0)}</b>
            </span>
          )}
        </div>
        {pane.indicators.map((ind) => {
          const info = indInfo.get(ind.id);
          const vals = legendVals.get(ind.id) || [];
          return (
            <div key={ind.id} className={`legend-row legend-ind ${ind.visible === false ? "muted" : ""}`}>
              <span className="legend-ind-name">
                {info ? info.name : ind.name} <span className="legend-inputs">{info ? inputsSummary(info.inputsMeta, ind.inputs) : ""}</span>
              </span>
              <span className="legend-ind-actions">
                <button title={ind.visible === false ? "Show" : "Hide"} onClick={() => s.updateIndicator(pane.id, ind.id, { visible: ind.visible === false })}>
                  {ind.visible === false ? <Icon.eyeOff /> : <Icon.eye />}
                </button>
                <button title="Settings" onClick={() => s.openDialog({ type: "indicatorSettings", paneId: pane.id, indicatorId: ind.id })}>
                  <Icon.gear />
                </button>
                <button
                  title="Source code"
                  onClick={() => {
                    s.setEditor({ scriptId: ind.scriptId, source: ind.source, name: info ? info.declaration?.title || ind.name : ind.name, dirty: false, openNonce: Date.now() });
                    s.updateSettings({ bottomPanel: true, bottomTab: "pine" });
                  }}
                >
                  <Icon.code />
                </button>
                <button title="Remove" onClick={() => s.removeIndicator(pane.id, ind.id)}>
                  <Icon.close />
                </button>
              </span>
              {info && info.error ? (
                <span className="legend-error" title={info.error}>
                  ⚠ {info.error.split("\n")[0].slice(0, 90)}
                </span>
              ) : (
                <span className="legend-values">
                  {vals.map((v, i) => (
                    <b key={i} style={{ color: v.color }}>
                      {v.text}
                    </b>
                  ))}
                  {info && info.running && !vals.length ? <i className="spinner-sm" /> : null}
                </span>
              )}
            </div>
          );
        })}
      </div>
      {quote && (
        <div className="pane-quote">
          <span className="q-bid">{quote.bid ? quote.bid.toFixed(d) : "—"}</span>
          <span className="q-spread">{quote.bid && quote.ask ? Math.round((quote.ask - quote.bid) * 10 ** d) : ""}</span>
          <span className="q-ask">{quote.ask ? quote.ask.toFixed(d) : "—"}</span>
        </div>
      )}
      {runtime.loading && <div className="pane-overlay">Loading {pane.symbol}…</div>}
      {!runtime.loading && runtime.error && (
        <div className="pane-error">
          <span>{runtime.error}</span>
          <button onClick={() => s.openDialog({ type: "symbol", paneId: pane.id })}>Change symbol</button>
          <button onClick={() => ctrlRef.current && ctrlRef.current.reload()}>Retry</button>
          {pane.provider === "upstox" && <BrokerLoginButton />}
        </div>
      )}
      {!pane.provider && <div className="pane-overlay">Waiting for a data provider…</div>}
      {active && selected && selected.paneId === pane.id && <DrawingStyleBar paneId={pane.id} drawing={selected.drawing} />}
      {(replay.armed || replay.active) && <ReplayBar replay={replay} ctrl={ctrlRef.current} />}
    </div>
  );
}

function ReplayBar({ replay, ctrl }) {
  if (!ctrl) return null;
  if (replay.armed && !replay.active) {
    return (
      <div className="replay-bar" onMouseDown={(e) => e.stopPropagation()}>
        <span>Bar Replay — click on the chart to choose the starting bar</span>
        <button onClick={() => ctrl.stopReplay()}>Cancel</button>
      </div>
    );
  }
  return (
    <div className="replay-bar" onMouseDown={(e) => e.stopPropagation()}>
      <span className="replay-label">Replay</span>
      <button title="Choose a new starting bar" onClick={() => ctrl.armReplay()}>
        ⇤ Select bar
      </button>
      {replay.playing ? (
        <button title="Pause" onClick={() => ctrl.replayPause()}>
          ⏸
        </button>
      ) : (
        <button title="Play" onClick={() => ctrl.replayPlay()}>
          ▶
        </button>
      )}
      <button title="Step forward one bar" onClick={() => ctrl.replayStep()}>
        ⏭
      </button>
      <select value={replay.speed} onChange={(e) => ctrl.setReplaySpeed(Number(e.target.value))} title="Bars per second">
        {[0.5, 1, 2, 3, 5, 10].map((v) => (
          <option key={v} value={v}>
            {v}x
          </option>
        ))}
      </select>
      <span className="muted small">{replay.remaining} bars left</span>
      <button className="replay-exit" title="Back to real-time" onClick={() => ctrl.stopReplay()}>
        Exit replay
      </button>
    </div>
  );
}
