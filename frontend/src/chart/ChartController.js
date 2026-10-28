// ChartController: one lightweight-charts instance (one chart pane in the layout).
//
// Owns: the main price series + volume, history loading (incl. scroll-back),
// live candle streaming, chart types, indicators (IndicatorRuntime) and drawings
// (DrawingManager). React components drive it through plain method calls and
// receive updates through the callbacks passed to the constructor.

import {
  AreaSeries,
  BarSeries,
  BaselineSeries,
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  LineType,
  PriceScaleMode,
  createChart,
} from "lightweight-charts";
import { getHistory } from "../api/client.js";
import { stream } from "../api/stream.js";
import { pine } from "../pine/pineClient.js";
import { TF, bisectTime, logicalToTime, timeToLogical, toHeikinAshi, uid } from "../utils/market.js";
import { DrawingManager } from "./drawings/DrawingManager.js";
import { DrawingsPrimitive } from "./drawings/DrawingsPrimitive.js";
import { IndicatorRuntime } from "./pine/IndicatorRuntime.js";
import { chartTimeFormatters } from "./timezone.js";

export const THEMES = {
  dark: {
    bg: "#131722",
    text: "#D1D4DC",
    grid: "#1E222D",
    border: "#2A2E39",
    up: "#089981",
    down: "#F23645",
    crosshair: "#758696",
  },
  light: {
    bg: "#FFFFFF",
    text: "#131722",
    grid: "#F0F3FA",
    border: "#E0E3EB",
    up: "#089981",
    down: "#F23645",
    crosshair: "#9598A1",
  },
};

const HISTORY_PAGE = 1000;

/** Guess price precision from the data when the symbol's digits are unknown. */
export function digitsFromBars(bars) {
  let d = 0;
  for (const b of bars.slice(-200)) {
    for (const v of [b.open, b.high, b.low, b.close]) {
      const str = String(v);
      const i = str.indexOf(".");
      if (i >= 0) d = Math.max(d, Math.min(5, str.length - i - 1));
    }
  }
  return d || 2;
}
const LIVE_RECALC_MS = 1000;

export class ChartController {
  constructor(container, callbacks = {}) {
    this.container = container;
    this.cb = callbacks;
    this.datasetId = uid("ds");
    this.provider = null;
    this.symbol = null;
    this.timeframe = "M1";
    this.chartType = "candles";
    this.digits = 5;
    this.theme = "dark";
    this.showVolume = true;
    this.bars = [];
    this.loadToken = 0;
    this.loadingOlder = false;
    this.noMoreHistory = false;
    this.unsubscribe = null;
    this.indicators = new Map();
    this.barColorLayers = new Map();
    this.recalcTimer = null;
    this.destroyed = false;
    this.lastCrosshairTime = null;
    this.legendRaf = 0;

    const t = THEMES[this.theme];
    this.chart = createChart(container, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: t.bg }, textColor: t.text, fontSize: 12, attributionLogo: true, panes: { separatorColor: t.border, separatorHoverColor: "rgba(41,98,255,0.3)", enableResize: true } },
      grid: { vertLines: { color: t.grid }, horzLines: { color: t.grid } },
      crosshair: { mode: CrosshairMode.Normal, vertLine: { color: t.crosshair, labelBackgroundColor: "#363A45" }, horzLine: { color: t.crosshair, labelBackgroundColor: "#363A45" } },
      rightPriceScale: { borderColor: t.border, scaleMargins: { top: 0.08, bottom: 0.08 } },
      timeScale: { borderColor: t.border, timeVisible: true, secondsVisible: false, rightOffset: 10, barSpacing: 7 },
      localization: { priceFormatter: (p) => this._formatPrice(p) },
    });

    this.drawingManager = new DrawingManager(this._conv(), {
      onChange: (list) => this.cb.onDrawingsChange && this.cb.onDrawingsChange(list),
      onSelect: (d) => this.cb.onDrawingSelect && this.cb.onDrawingSelect(d),
      onToolDone: () => this.cb.onToolDone && this.cb.onToolDone(),
      onEditText: (d) => this.cb.onEditText && this.cb.onEditText(d),
      requestRedraw: () => this.drawingsPrimitive && this.drawingsPrimitive.requestRedraw(),
    });
    this.drawingsPrimitive = new DrawingsPrimitive(this.drawingManager, () => this.digits);

    this._createMainSeries();
    this._bindEvents();
  }

  // ================================================================ series
  _formatPrice(p) {
    if (p === null || p === undefined || Number.isNaN(p)) return "";
    const abs = Math.abs(p);
    const d = abs >= 100000 ? Math.min(this.digits, 2) : this.digits;
    return p.toFixed(d);
  }

  _seriesFor(type) {
    const t = THEMES[this.theme];
    const pf = { type: "price", precision: this.digits, minMove: 1 / 10 ** this.digits };
    const base = { priceFormat: pf, priceLineVisible: true, lastValueVisible: true };
    switch (type) {
      case "bars":
        return [BarSeries, { ...base, upColor: t.up, downColor: t.down, thinBars: false }];
      case "line":
        return [LineSeries, { ...base, color: "#2962FF", lineWidth: 2 }];
      case "step":
        return [LineSeries, { ...base, color: "#2962FF", lineWidth: 2, lineType: LineType.WithSteps }];
      case "area":
        return [AreaSeries, { ...base, lineColor: "#2962FF", topColor: "rgba(41,98,255,0.4)", bottomColor: "rgba(41,98,255,0.02)", lineWidth: 2 }];
      case "baseline":
        return [BaselineSeries, { ...base, baseValue: { type: "price", price: 0 }, topLineColor: t.up, bottomLineColor: t.down, topFillColor1: "rgba(8,153,129,0.28)", topFillColor2: "rgba(8,153,129,0.05)", bottomFillColor1: "rgba(242,54,69,0.05)", bottomFillColor2: "rgba(242,54,69,0.28)" }];
      case "columns":
        return [HistogramSeries, { ...base, color: "#2962FF" }];
      case "hollow":
        return [CandlestickSeries, { ...base, upColor: "rgba(0,0,0,0)", downColor: t.down, borderUpColor: t.up, borderDownColor: t.down, wickUpColor: t.up, wickDownColor: t.down }];
      case "heikin":
      case "candles":
      default:
        return [CandlestickSeries, { ...base, upColor: t.up, downColor: t.down, borderVisible: false, wickUpColor: t.up, wickDownColor: t.down }];
    }
  }

  _createMainSeries() {
    const [def, opts] = this._seriesFor(this.chartType);
    this.mainSeries = this.chart.addSeries(def, opts, 0);
    this.mainSeries.attachPrimitive(this.drawingsPrimitive);
    if (!this.volumeSeries) {
      this.volumeSeries = this.chart.addSeries(HistogramSeries, { priceFormat: { type: "volume" }, priceScaleId: "vol", lastValueVisible: false, priceLineVisible: false }, 0);
      this.volumeSeries.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    }
  }

  _isOhlcType() {
    return ["candles", "hollow", "heikin", "bars"].includes(this.chartType);
  }

  _displayBars() {
    return this.chartType === "heikin" ? toHeikinAshi(this.bars) : this.bars;
  }

  _mainData() {
    const disp = this._displayBars();
    const colors = this._mergedBarColors();
    if (this._isOhlcType()) {
      if (!colors) return disp.map((b) => ({ time: b.time, open: b.open, high: b.high, low: b.low, close: b.close }));
      return disp.map((b) => {
        const c = colors.get(b.time);
        return c ? { time: b.time, open: b.open, high: b.high, low: b.low, close: b.close, color: c, wickColor: c, borderColor: c } : { time: b.time, open: b.open, high: b.high, low: b.low, close: b.close };
      });
    }
    return disp.map((b) => ({ time: b.time, value: b.close }));
  }

  _volumeData() {
    const t = THEMES[this.theme];
    return this.bars.map((b) => ({ time: b.time, value: b.volume || 0, color: b.close >= b.open ? t.up + "66" : t.down + "66" }));
  }

  _refreshMain() {
    this.mainSeries.setData(this._mainData());
    this.volumeSeries.setData(this.showVolume ? this._volumeData() : []);
    if (this.chartType === "baseline" && this.bars.length) {
      const mid = this.bars[Math.max(0, this.bars.length - 200)].close;
      this.mainSeries.applyOptions({ baseValue: { type: "price", price: mid } });
    }
  }

  // ================================================================= load
  get tfSeconds() {
    return TF[this.timeframe] ? TF[this.timeframe].s : 60;
  }

  async load({ provider, symbol, timeframe, digits }) {
    const token = ++this.loadToken;
    if (this.unsubscribe) {
      this.unsubscribe();
      this.unsubscribe = null;
    }
    if (this.unsubTick) {
      this.unsubTick();
      this.unsubTick = null;
    }
    this._stopReplayTimer();
    if (this.replay || this._replayArmed) {
      this.replay = null;
      this._replayArmed = false;
      this._emitReplay();
    }
    this.provider = provider;
    this.symbol = symbol;
    this.timeframe = timeframe;
    if (typeof digits === "number") this.digits = digits;
    this.mainSeries.applyOptions({ priceFormat: { type: "price", precision: this.digits, minMove: 1 / 10 ** this.digits } });
    this.noMoreHistory = false;
    this._emitState({ loading: true, error: null });
    try {
      const bars = await getHistory(provider, symbol, timeframe, HISTORY_PAGE);
      if (token !== this.loadToken || this.destroyed) return;
      this.bars = bars;
      if (typeof digits !== "number") {
        this.digits = digitsFromBars(bars);
        this.mainSeries.applyOptions({ priceFormat: { type: "price", precision: this.digits, minMove: 1 / 10 ** this.digits } });
        if (this.cb.onDigits) this.cb.onDigits(this.digits);
      }
      this._refreshMain();
      this.chart.timeScale().scrollToRealTime();
      this._pushDataset();
      this._runAllIndicators();
      this.drawingsPrimitive.requestRedraw();
      this.unsubscribe = stream.subscribeCandles(provider, symbol, timeframe, (c) => this._onCandle(c), (msg) => this._emitState({ error: msg }));
      this.unsubTick = stream.onTick(provider, symbol, (t) => this.cb.onQuote && this.cb.onQuote(t));
      this._emitState({ loading: false, error: null });
      this._emitLegend(null);
    } catch (err) {
      if (token !== this.loadToken || this.destroyed) return;
      this.bars = [];
      this._refreshMain();
      this._emitState({ loading: false, error: err.message });
    }
  }

  setDigits(d) {
    if (typeof d !== "number" || d === this.digits) return;
    this.digits = d;
    this.mainSeries.applyOptions({ priceFormat: { type: "price", precision: d, minMove: 1 / 10 ** d } });
  }

  reload() {
    if (this.provider) this.load({ provider: this.provider, symbol: this.symbol, timeframe: this.timeframe, digits: this.digits });
  }

  async _loadOlder() {
    if (this.loadingOlder || this.noMoreHistory || !this.bars.length || this.replay) return;
    this.loadingOlder = true;
    const token = this.loadToken;
    const oldest = this.bars[0].time;
    try {
      const older = (await getHistory(this.provider, this.symbol, this.timeframe, HISTORY_PAGE, oldest)).filter((b) => b.time < oldest);
      if (token !== this.loadToken || this.destroyed) return;
      if (!older.length) {
        this.noMoreHistory = true;
        return;
      }
      const ts = this.chart.timeScale();
      const range = ts.getVisibleLogicalRange();
      this.bars = older.concat(this.bars);
      this._refreshMain();
      if (range) ts.setVisibleLogicalRange({ from: range.from + older.length, to: range.to + older.length });
      this._pushDataset();
      this._runAllIndicators();
    } catch (err) {
      this._emitState({ error: `Older history: ${err.message}` });
    } finally {
      this.loadingOlder = false;
    }
  }

  _onCandle(c) {
    if (this.destroyed || !this.bars.length) return;
    if (this.replay) return; // live data is paused while replaying history
    const last = this.bars[this.bars.length - 1];
    if (c.time < last.time) return;
    const isNewBar = c.time > last.time;
    if (isNewBar) this.bars.push(c);
    else this.bars[this.bars.length - 1] = c;

    if (this.chartType === "heikin" || this.barColorLayers.size) {
      // HA bars and script bar colours depend on history: update the last few points only
      const data = this._mainData();
      this.mainSeries.update(data[data.length - 1]);
    } else if (this._isOhlcType()) {
      this.mainSeries.update({ time: c.time, open: c.open, high: c.high, low: c.low, close: c.close });
    } else {
      this.mainSeries.update({ time: c.time, value: c.close });
    }
    if (this.showVolume) {
      const t = THEMES[this.theme];
      this.volumeSeries.update({ time: c.time, value: c.volume || 0, color: c.close >= c.open ? t.up + "66" : t.down + "66" });
    }
    pine.updateBar(this.datasetId, c);
    this._scheduleRecalc(isNewBar);
    if (this.lastCrosshairTime === null) this._emitLegend(null);
    if (this.cb.onLastBar) this.cb.onLastBar(c, this.bars.length > 1 ? this.bars[this.bars.length - 2] : null);
  }

  _pushDataset() {
    pine.setData(this.datasetId, this.bars, {
      tickerId: this.symbol,
      timeframe: TF[this.timeframe].pine,
      backendTf: this.timeframe,
      tfSeconds: this.tfSeconds,
      provider: this.provider,
      digits: this.digits,
    });
  }

  _scheduleRecalc(immediate) {
    if (!this.indicators.size) return;
    if (immediate) {
      clearTimeout(this.recalcTimer);
      this.recalcTimer = null;
      this._runAllIndicators();
      return;
    }
    if (this.recalcTimer) return;
    this.recalcTimer = setTimeout(() => {
      this.recalcTimer = null;
      this._runAllIndicators();
    }, LIVE_RECALC_MS);
  }

  _runAllIndicators() {
    for (const ind of this.indicators.values()) ind.run();
  }

  // ============================================================ bar replay
  /** Arm replay: the next click on the chart chooses the starting bar. */
  armReplay() {
    this._replayArmed = true;
    this._emitReplay();
  }

  startReplay(fromTime) {
    const all = this.replay ? this.replay.all : this.bars.slice();
    if (all.length < 10) return;
    let idx = bisectTime(all, fromTime);
    idx = Math.max(5, Math.min(all.length - 2, idx < 0 ? 5 : idx));
    this._replayArmed = false;
    this._stopReplayTimer();
    this.replay = { all, index: idx, playing: false, speed: this.replay ? this.replay.speed : 1 };
    this.bars = all.slice(0, idx + 1);
    this._refreshMain();
    this._pushDataset();
    this._runAllIndicators();
    this.chart.timeScale().scrollToRealTime();
    this._emitReplay();
    this._emitLegend(null);
  }

  replayStep() {
    const r = this.replay;
    if (!r) return false;
    if (r.index >= r.all.length - 1) {
      this.replayPause();
      return false;
    }
    r.index += 1;
    const bar = r.all[r.index];
    this.bars.push(bar);
    const data = this._mainData();
    this.mainSeries.update(data[data.length - 1]);
    if (this.showVolume) {
      const t = THEMES[this.theme];
      this.volumeSeries.update({ time: bar.time, value: bar.volume || 0, color: bar.close >= bar.open ? t.up + "66" : t.down + "66" });
    }
    pine.updateBar(this.datasetId, bar);
    this._scheduleRecalc(true);
    this._emitLegend(null);
    this._emitReplay();
    return true;
  }

  replayPlay() {
    const r = this.replay;
    if (!r || r.playing) return;
    r.playing = true;
    const tick = () => {
      if (!this.replay || !this.replay.playing) return;
      if (this.replayStep()) this._replayTimer = setTimeout(tick, 1000 / this.replay.speed);
    };
    this._replayTimer = setTimeout(tick, 1000 / r.speed);
    this._emitReplay();
  }

  replayPause() {
    if (!this.replay) return;
    this.replay.playing = false;
    this._stopReplayTimer();
    this._emitReplay();
  }

  setReplaySpeed(speed) {
    if (!this.replay) return;
    this.replay.speed = speed;
    if (this.replay.playing) {
      this._stopReplayTimer();
      this.replay.playing = false;
      this.replayPlay();
    } else this._emitReplay();
  }

  stopReplay() {
    this._replayArmed = false;
    this._stopReplayTimer();
    const was = !!this.replay;
    this.replay = null;
    this._emitReplay();
    if (was) this.reload();
  }

  _stopReplayTimer() {
    clearTimeout(this._replayTimer);
    this._replayTimer = null;
  }

  _emitReplay() {
    if (!this.cb.onReplay) return;
    const r = this.replay;
    this.cb.onReplay({
      armed: !!this._replayArmed,
      active: !!r,
      playing: !!(r && r.playing),
      speed: r ? r.speed : 1,
      remaining: r ? r.all.length - 1 - r.index : 0,
      time: r ? r.all[r.index].time : null,
    });
  }

  // ============================================================ indicators
  /** Reconcile with the store list [{id, source, inputs, visible, name}] */
  setIndicators(list) {
    const want = new Map((list || []).map((c) => [c.id, c]));
    for (const [id, rt] of this.indicators) {
      if (!want.has(id)) {
        rt.destroy();
        this.indicators.delete(id);
      }
    }
    for (const cfg of list || []) {
      const rt = this.indicators.get(cfg.id);
      if (!rt) {
        const nrt = new IndicatorRuntime(this, cfg);
        this.indicators.set(cfg.id, nrt);
        if (this.bars.length) nrt.run();
      } else if (rt.configure(cfg) && this.bars.length) rt.run();
    }
    this._emitIndicators();
  }

  _indicatorUpdated() {
    this._layoutPanes();
    this._emitIndicators();
    this._emitLegend(this.lastCrosshairTime);
  }

  _emitIndicators() {
    if (!this.cb.onIndicators) return;
    const out = [];
    for (const rt of this.indicators.values()) {
      out.push({ id: rt.id, name: rt.name, error: rt.error, overlay: rt.overlay, declaration: rt.result ? rt.result.declaration : null, inputsMeta: rt.result ? rt.result.inputsMeta : null, strategy: rt.result ? rt.result.strategy : null, warnings: rt.result ? rt.result.warnings : [], elapsedMs: rt.result ? rt.result.elapsedMs : null, running: rt.running });
    }
    this.cb.onIndicators(out);
  }

  _setBarColors(id, map) {
    const had = this.barColorLayers.has(id);
    if (map) this.barColorLayers.set(id, map);
    else this.barColorLayers.delete(id);
    if (map || had) this.mainSeries.setData(this._mainData());
  }

  _mergedBarColors() {
    if (!this.barColorLayers.size) return null;
    const merged = new Map();
    for (const m of this.barColorLayers.values()) for (const [t, c] of m) merged.set(t, c);
    return merged;
  }

  /**
   * Give indicator panes a sensible height whenever the number of panes changes
   * (lightweight-charts would otherwise squeeze older panes). Manual resizes by the
   * user are kept until the pane count changes again.
   */
  _layoutPanes() {
    const panes = this.chart.panes();
    if (panes.length === this._lastPaneCount) return;
    this._lastPaneCount = panes.length;
    const n = panes.length - 1;
    if (n <= 0) return;
    const total = this.container.clientHeight - 30; // minus time axis
    const sub = Math.max(60, Math.min(170, Math.round(total * 0.2), Math.floor((total * 0.55) / n)));
    const main = Math.max(120, total - sub * n);
    try {
      panes[0].setHeight(main);
      for (let i = 1; i < panes.length; i++) panes[i].setHeight(sub);
    } catch {
      /* layout is cosmetic */
    }
  }

  _cleanupEmptyPanes() {
    const panes = this.chart.panes();
    for (let i = panes.length - 1; i > 0; i--) {
      if (panes[i].getSeries().length === 0) {
        try {
          this.chart.removePane(i);
        } catch {
          /* already gone */
        }
      }
    }
    this._layoutPanes();
  }

  // ============================================================ conversions
  timeToX(time) {
    if (!this.bars.length || time === null || time === undefined) return null;
    const l = timeToLogical(this.bars, time, this.tfSeconds);
    const x = this.chart.timeScale().logicalToCoordinate(l);
    return x === null ? null : x;
  }

  timeOfIndex(times, i) {
    if (!times || !times.length) return null;
    if (i < 0) return times[0] + i * this.tfSeconds;
    if (i < times.length) return times[Math.floor(i)];
    return times[times.length - 1] + (i - (times.length - 1)) * this.tfSeconds;
  }

  barAtTime(time) {
    const i = bisectTime(this.bars, time);
    if (i < 0) return null;
    return this.bars[i].time === time ? this.bars[i] : this.bars[i];
  }

  barSpacing() {
    return this.chart.timeScale().options().barSpacing || 6;
  }

  _conv() {
    return {
      timeToX: (t) => this.timeToX(t),
      xToTime: (x) => {
        const l = this.chart.timeScale().coordinateToLogical(x);
        return l === null ? (this.bars.length ? this.bars[this.bars.length - 1].time : 0) : logicalToTime(this.bars, l, this.tfSeconds);
      },
      xToLogical: (x) => this.chart.timeScale().coordinateToLogical(x) ?? 0,
      timeToLogical: (t) => timeToLogical(this.bars, t, this.tfSeconds) ?? 0,
      logicalToTime: (l) => logicalToTime(this.bars, l, this.tfSeconds),
      priceToY: (p) => this.mainSeries.priceToCoordinate(p),
      yToPrice: (y) => this.mainSeries.coordinateToPrice(y),
      paneSize: () => {
        const pane = this.chart.panes()[0];
        const el = pane && pane.getHTMLElement();
        return { width: this.chart.timeScale().width(), height: el ? el.clientHeight : this.container.clientHeight };
      },
      barNearX: (x) => {
        const l = this.chart.timeScale().coordinateToLogical(x);
        if (l === null || !this.bars.length) return null;
        const i = Math.max(0, Math.min(this.bars.length - 1, Math.round(l)));
        return this.bars[i];
      },
      visiblePriceRange: () => {
        const h = this.container.clientHeight;
        const a = this.mainSeries.coordinateToPrice(h * 0.1);
        const b = this.mainSeries.coordinateToPrice(h * 0.6);
        return a === null || b === null ? null : { min: Math.min(a, b), max: Math.max(a, b) };
      },
      digits: () => this.digits,
    };
  }

  // ================================================================ events
  _mainPaneRect() {
    const pane = this.chart.panes()[0];
    const el = pane && pane.getHTMLElement();
    return el ? el.getBoundingClientRect() : null;
  }

  _localPoint(ev) {
    const r = this._mainPaneRect();
    if (!r) return null;
    const x = ev.clientX - r.left;
    const y = ev.clientY - r.top;
    if (x < 0 || y < 0 || y > r.height || x > this.chart.timeScale().width()) return null;
    return { x, y };
  }

  _bindEvents() {
    const el = this.container;
    this._owning = false;
    this._onPointerDown = (ev) => {
      if (ev.button !== 0) return;
      const pt = this._localPoint(ev);
      if (!pt) return;
      if (this.drawingManager.pointerDown(pt.x, pt.y, ev)) {
        this._owning = true;
        ev.preventDefault();
        ev.stopPropagation();
        if (this.cb.onFocus) this.cb.onFocus();
      }
    };
    this._swallow = (ev) => {
      if (this._owning) {
        ev.preventDefault();
        ev.stopPropagation();
      }
    };
    this._onPointerMove = (ev) => {
      const r = this._mainPaneRect();
      if (!r) return;
      const x = ev.clientX - r.left;
      const y = ev.clientY - r.top;
      if (this._owning || this.drawingManager.creating) {
        this.drawingManager.pointerMove(x, y);
        if (this._owning) ev.stopPropagation();
        return;
      }
      if (x >= 0 && y >= 0 && y <= r.height) this.drawingManager.pointerMove(x, y);
    };
    this._onPointerUp = (ev) => {
      const r = this._mainPaneRect();
      if (!r) return;
      const handled = this.drawingManager.pointerUp(ev.clientX - r.left, ev.clientY - r.top);
      if (this._owning) {
        this._owning = false;
        if (handled) ev.stopPropagation();
      }
    };
    this._onDblClick = (ev) => {
      const pt = this._localPoint(ev);
      if (pt && this.drawingManager.doubleClick(pt.x, pt.y)) ev.stopPropagation();
    };
    this._onContextMenu = (ev) => {
      ev.preventDefault();
      const pt = this._localPoint(ev);
      if (this.cb.onContextMenu) this.cb.onContextMenu({ clientX: ev.clientX, clientY: ev.clientY, price: pt ? this.mainSeries.coordinateToPrice(pt.y) : null });
    };
    el.addEventListener("pointerdown", this._onPointerDown, true);
    el.addEventListener("mousedown", this._swallow, true);
    el.addEventListener("touchstart", this._swallow, true);
    window.addEventListener("pointermove", this._onPointerMove, true);
    window.addEventListener("pointerup", this._onPointerUp, true);
    el.addEventListener("dblclick", this._onDblClick, true);
    el.addEventListener("contextmenu", this._onContextMenu);

    this.chart.timeScale().subscribeVisibleLogicalRangeChange((r) => {
      if (r && r.from < 20) this._loadOlder();
    });
    this.chart.subscribeCrosshairMove((param) => {
      this.lastCrosshairTime = param && param.time !== undefined ? param.time : null;
      if (this.legendRaf) return;
      this.legendRaf = requestAnimationFrame(() => {
        this.legendRaf = 0;
        this._emitLegend(this.lastCrosshairTime);
      });
    });
    this.chart.subscribeClick((param) => {
      if (this._replayArmed && param && param.time !== undefined) this.startReplay(param.time);
      if (this.cb.onFocus) this.cb.onFocus();
    });
  }

  _emitState(patch) {
    if (this.cb.onState) this.cb.onState(patch);
  }

  _emitLegend(time) {
    if (!this.cb.onLegend || this.destroyed) return;
    let bar = null;
    let prev = null;
    if (this.bars.length) {
      let i = this.bars.length - 1;
      if (time !== null && time !== undefined) {
        i = bisectTime(this.bars, time);
        if (i < 0) i = 0;
      }
      bar = this.bars[i];
      prev = i > 0 ? this.bars[i - 1] : null;
    }
    const indicators = [];
    for (const rt of this.indicators.values()) {
      indicators.push({ id: rt.id, values: rt.result ? rt.valuesAt(time ?? null) : [] });
    }
    this.cb.onLegend({ bar, prev, indicators });
  }

  // =============================================================== options
  setChartType(type) {
    if (type === this.chartType) return;
    this.chartType = type;
    this.mainSeries.detachPrimitive(this.drawingsPrimitive);
    this.chart.removeSeries(this.mainSeries);
    this._createMainSeries();
    this._refreshMain();
    this._alertLines = [];
    this.setAlertLines(this._alertLevels || []);
    for (const rt of this.indicators.values()) if (rt.overlay) rt.rebind();
    this.drawingsPrimitive.requestRedraw();
  }

  setTheme(theme) {
    this.theme = THEMES[theme] ? theme : "dark";
    const t = THEMES[this.theme];
    this.chart.applyOptions({
      layout: { background: { type: ColorType.Solid, color: t.bg }, textColor: t.text, panes: { separatorColor: t.border } },
      grid: { vertLines: { color: t.grid }, horzLines: { color: t.grid } },
      rightPriceScale: { borderColor: t.border },
      timeScale: { borderColor: t.border },
    });
    const [, opts] = this._seriesFor(this.chartType);
    this.mainSeries.applyOptions(opts);
    this._refreshMain();
  }

  /** Time axis / crosshair clock (see chart/timezone.js). */
  setTimezone(tz, shift = 0) {
    this.tz = tz;
    this.tzShift = shift;
    const intraday = () => !["D1", "W1", "MN1"].includes(this.timeframe);
    const { timeFormatter, tickMarkFormatter } = chartTimeFormatters(tz, shift, intraday);
    this.chart.applyOptions({ localization: { priceFormatter: (p) => this._formatPrice(p), timeFormatter }, timeScale: { tickMarkFormatter } });
  }

  setGrid(show) {
    const t = THEMES[this.theme];
    this.chart.applyOptions({ grid: { vertLines: { visible: show, color: t.grid }, horzLines: { visible: show, color: t.grid } } });
  }

  setShowVolume(v) {
    this.showVolume = !!v;
    this.volumeSeries.setData(this.showVolume ? this._volumeData() : []);
  }

  setPriceScale({ mode = "normal", inverted = false, autoScale = true } = {}) {
    const m = { normal: PriceScaleMode.Normal, log: PriceScaleMode.Logarithmic, percent: PriceScaleMode.Percentage, indexed: PriceScaleMode.IndexedTo100 }[mode] ?? PriceScaleMode.Normal;
    this.mainSeries.priceScale().applyOptions({ mode: m, invertScale: !!inverted, autoScale });
  }

  setCrosshairMode(mode) {
    this.chart.applyOptions({ crosshair: { mode: mode === "magnet" ? CrosshairMode.Magnet : CrosshairMode.Normal } });
  }

  resetView() {
    this.chart.timeScale().resetTimeScale();
    this.chart.timeScale().scrollToRealTime();
    this.mainSeries.priceScale().applyOptions({ autoScale: true });
  }

  /** Show active price alerts of this symbol as dashed orange lines with a bell label. */
  setAlertLines(levels) {
    for (const l of this._alertLines || []) {
      try {
        this.mainSeries.removePriceLine(l);
      } catch {
        /* series replaced */
      }
    }
    this._alertLevels = levels || [];
    this._alertLines = this._alertLevels.map((lv) =>
      this.mainSeries.createPriceLine({ price: lv.price, color: "#FF9800", lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: "🔔" + (lv.title ? " " + lv.title : "") }),
    );
  }

  setDrawings(list) {
    this.drawingManager.setDrawings(list);
  }

  setTool(tool) {
    this.drawingManager.setTool(tool);
  }

  setMagnet(v) {
    this.drawingManager.setMagnet(v);
  }

  setDrawingsLocked(v) {
    this.drawingManager.setLocked(v);
  }

  setDrawingsHidden(v) {
    this.drawingManager.setHidden(v);
  }

  deleteSelectedDrawing() {
    return this.drawingManager.deleteSelected();
  }

  cancelDrawing() {
    return this.drawingManager.cancel();
  }

  updateSelectedDrawingStyle(patch) {
    this.drawingManager.updateSelectedStyle(patch);
  }

  screenshot() {
    return this.chart.takeScreenshot(true, false);
  }

  destroy() {
    this.destroyed = true;
    this.loadToken++;
    clearTimeout(this.recalcTimer);
    this._stopReplayTimer();
    if (this.legendRaf) cancelAnimationFrame(this.legendRaf);
    if (this.unsubscribe) this.unsubscribe();
    if (this.unsubTick) this.unsubTick();
    for (const rt of this.indicators.values()) rt.destroyed = true;
    this.indicators.clear();
    pine.dropData(this.datasetId);
    const el = this.container;
    el.removeEventListener("pointerdown", this._onPointerDown, true);
    el.removeEventListener("mousedown", this._swallow, true);
    el.removeEventListener("touchstart", this._swallow, true);
    window.removeEventListener("pointermove", this._onPointerMove, true);
    window.removeEventListener("pointerup", this._onPointerUp, true);
    el.removeEventListener("dblclick", this._onDblClick, true);
    el.removeEventListener("contextmenu", this._onContextMenu);
    this.chart.remove();
  }
}
