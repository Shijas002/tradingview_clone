// IndicatorRuntime: one Pine script instance on one chart.
//
// It asks the Pine worker to run the script, then maps the result onto the chart:
// plot() -> lightweight-charts series, hline() -> price lines, everything else
// (fill, bgcolor, shapes, labels, lines, boxes, tables, strategy trades) -> a
// PinePrimitive. Overlay scripts draw on the price pane; others get their own pane.

import { AreaSeries, CandlestickSeries, BarSeries, HistogramSeries, LineSeries, LineStyle, LineType } from "lightweight-charts";
import { pine } from "../../pine/pineClient.js";
import { PinePrimitive } from "./PinePrimitive.js";
import { autoDigits } from "../../utils/market.js";

const LINE_STYLES = new Set([undefined, null, "", "line", "style_line", "style_linebr", "style_stepline", "style_stepline_diamond", "style_steplinebr", "style_circles", "style_cross", "style_area", "style_areabr", "style_histogram", "style_columns"]);

function kindOf(plot) {
  const st = plot.options.style;
  if (plot.key.startsWith("__")) return "objects";
  if (st === "fill") return "fill";
  if (st === "hline") return "hline";
  if (st === "background") return "bg";
  if (st === "barcolor") return "barcolor";
  if (st === "shape") return "shape";
  if (st === "char") return "char";
  if (st === "candle") return "candle";
  if (st === "bar") return "bar";
  if (LINE_STYLES.has(st)) return "series";
  return "series";
}

function lineStyleOf(opt) {
  const s = opt.linestyle || opt.lineStyle;
  if (!s) return LineStyle.Dashed;
  if (String(s).includes("dotted")) return LineStyle.Dotted;
  if (String(s).includes("solid")) return LineStyle.Solid;
  return LineStyle.Dashed;
}

export class IndicatorRuntime {
  constructor(ctrl, cfg) {
    this.ctrl = ctrl;
    this.cfg = cfg;
    this.series = []; // [{plot, api, kind, valueByTime}]
    this.priceLines = [];
    this.anchor = null;
    this.ownAnchor = false;
    this.primitive = null;
    this.structureKey = null;
    this.result = null;
    this.error = null;
    this.running = false;
    this.pendingRun = false;
    this.destroyed = false;
    this.paneRef = null;
    this.lastRunMs = 0;
  }

  get id() {
    return this.cfg.id;
  }

  get overlay() {
    return this.result ? this.result.declaration.overlay : true;
  }

  get name() {
    if (this.result) return this.result.declaration.shorttitle;
    return this.cfg.name || "Script";
  }

  /** Update config; returns true when the script must be re-run. */
  configure(cfg) {
    const rerun = cfg.source !== this.cfg.source || JSON.stringify(cfg.inputs || {}) !== JSON.stringify(this.cfg.inputs || {});
    const visChanged = cfg.visible !== this.cfg.visible;
    this.cfg = cfg;
    if (visChanged) this._applyVisibility();
    return rerun;
  }

  async run() {
    if (this.destroyed) return;
    if (this.running) {
      this.pendingRun = true;
      return;
    }
    this.running = true;
    this.pendingRun = false;
    const started = performance.now();
    try {
      const res = await pine.run(this.ctrl.datasetId, this.cfg.source, this.cfg.inputs || {});
      if (this.destroyed) return;
      this.error = null;
      this.result = res;
      this._apply(res);
    } catch (err) {
      if (this.destroyed) return;
      this.error = err.message || String(err);
    } finally {
      this.running = false;
      this.lastRunMs = performance.now() - started;
      if (!this.destroyed) {
        this.ctrl._indicatorUpdated(this);
        if (this.pendingRun) this.run();
      }
    }
  }

  // ------------------------------------------------------------------ apply
  _paneIndex() {
    if (this.overlay) return 0;
    if (this.paneRef) {
      const idx = this.ctrl.chart.panes().indexOf(this.paneRef);
      if (idx > 0) return idx;
    }
    return this.ctrl.chart.panes().length; // new pane at the bottom
  }

  _apply(res) {
    const chart = this.ctrl.chart;
    const overlay = res.declaration.overlay;
    const plots = res.plots;
    const seriesPlots = plots.filter((p) => ["series", "candle", "bar"].includes(kindOf(p)));
    const key = JSON.stringify([overlay, seriesPlots.map((p) => [p.title, kindOf(p), p.options.style || ""])]);

    if (key !== this.structureKey || !this._structureAlive()) {
      this._teardownSeries();
      const paneIndex = this._paneIndex();
      for (const p of seriesPlots) {
        const api = this._createSeries(p, paneIndex, res);
        this.series.push({ plot: p, api, kind: kindOf(p), valueByTime: new Map(), colorByTime: new Map() });
      }
      if (overlay) {
        this.anchor = this.ctrl.mainSeries;
        this.ownAnchor = false;
      } else if (this.series.length) {
        this.anchor = this.series[0].api;
        this.ownAnchor = false;
      } else {
        this.anchor = chart.addSeries(LineSeries, { visible: true, lineVisible: false, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false }, paneIndex);
        this.ownAnchor = true;
      }
      if (!overlay) {
        const pane = this.anchor.getPane ? this.anchor.getPane() : chart.panes()[paneIndex];
        if (pane) this.paneRef = pane;
      } else this.paneRef = null;
      this.structureKey = key;
    }

    // ---- series data (refresh each series' plot reference with the new result first)
    const tf = this.ctrl.tfSeconds;
    this.series.forEach((s, i) => {
      s.plot = seriesPlots[i];
      this._setSeriesData(s, tf, res);
    });
    if (this.ownAnchor) {
      const t = this.ctrl.bars.map((b) => ({ time: b.time }));
      this.anchor.setData(t);
    }

    // ---- hlines
    for (const pl of this.priceLines) {
      try {
        pl.series.removePriceLine(pl.line);
      } catch {
        /* series may be gone */
      }
    }
    this.priceLines = [];
    const hlines = plots.filter((p) => kindOf(p) === "hline");
    const hlineValues = new Map();
    for (const h of hlines) {
      const v = h.values.find((x) => x !== null && x !== undefined);
      if (v === undefined) continue;
      hlineValues.set(h.title, v);
      const color = (h.colors && h.colors.find(Boolean)) || h.options.color || "#787B86";
      const line = this.anchor.createPriceLine({ price: v, color, lineWidth: h.options.linewidth || 1, lineStyle: lineStyleOf(h.options), axisLabelVisible: false, title: "" });
      this.priceLines.push({ series: this.anchor, line });
    }

    // ---- primitive scene
    const scene = this._buildScene(res, hlineValues);
    if (!this.primitive) this.primitive = new PinePrimitive(this.ctrl, overlay);
    if (this.primitive.series !== this.anchor) {
      if (this.primitive.series) {
        try {
          this.primitive.series.detachPrimitive(this.primitive);
        } catch {
          /* ignore */
        }
      }
      this.anchor.attachPrimitive(this.primitive);
    }
    this.primitive.setScene(scene, overlay);

    // ---- bar colours
    const bc = plots.find((p) => kindOf(p) === "barcolor");
    if (bc && bc.colors) {
      const map = new Map();
      bc.times.forEach((t, i) => {
        if (bc.colors[i]) map.set(t, bc.colors[i]);
      });
      this.ctrl._setBarColors(this.id, map.size ? map : null);
    } else this.ctrl._setBarColors(this.id, null);

    this._applyVisibility();
  }

  _structureAlive() {
    // panes can disappear when other indicators are removed; verify our series still exist
    try {
      for (const s of this.series) s.api.options();
      if (this.ownAnchor && this.anchor) this.anchor.options();
      return true;
    } catch {
      return false;
    }
  }

  _createSeries(p, paneIndex, res) {
    const chart = this.ctrl.chart;
    const o = p.options;
    const color = o.color || "#2962FF";
    const width = o.linewidth || 1;
    const precision = res.declaration.precision ?? (res.declaration.overlay ? this.ctrl.digits : null);
    const priceFormat = res.declaration.format === "volume" ? { type: "volume" } : precision !== null ? { type: "price", precision, minMove: 1 / 10 ** precision } : undefined;
    const common = { priceLineVisible: false, lastValueVisible: true, title: "", ...(priceFormat ? { priceFormat } : {}) };
    const kind = kindOf(p);
    if (kind === "candle") return chart.addSeries(CandlestickSeries, { ...common, upColor: color, downColor: color, wickUpColor: color, wickDownColor: color, borderVisible: false }, paneIndex);
    if (kind === "bar") return chart.addSeries(BarSeries, { ...common, upColor: color, downColor: color }, paneIndex);
    switch (o.style) {
      case "style_histogram":
      case "style_columns":
        return chart.addSeries(HistogramSeries, { ...common, color, base: o.histbase || 0 }, paneIndex);
      case "style_area":
      case "style_areabr":
        return chart.addSeries(AreaSeries, { ...common, lineColor: color, topColor: color + "66", bottomColor: color + "05", lineWidth: width }, paneIndex);
      case "style_circles":
      case "style_cross":
        return chart.addSeries(LineSeries, { ...common, color, lineVisible: false, pointMarkersVisible: true, pointMarkersRadius: Math.max(1.5, width + 0.5), crosshairMarkerVisible: false }, paneIndex);
      case "style_stepline":
      case "style_steplinebr":
      case "style_stepline_diamond":
        return chart.addSeries(LineSeries, { ...common, color, lineWidth: width, lineType: LineType.WithSteps }, paneIndex);
      default:
        return chart.addSeries(LineSeries, { ...common, color, lineWidth: width }, paneIndex);
    }
  }

  _shiftedTime(times, i, offset, tf) {
    const j = i + offset;
    if (j < 0) return null;
    if (j < times.length) return times[j];
    return times[times.length - 1] + (j - (times.length - 1)) * tf;
  }

  _setSeriesData(s, tf, res) {
    const p = s.plot;
    const offset = p.options.offset || 0;
    const base = p.options.color || "#2962FF";
    const breaks = ["style_linebr", "style_areabr", "style_steplinebr", "style_histogram", "style_columns", "style_circles", "style_cross"].includes(p.options.style);
    const data = [];
    s.valueByTime = new Map();
    s.colorByTime = new Map();
    const isCandle = s.kind === "candle" || s.kind === "bar";
    let lastTime = -Infinity;
    for (let i = 0; i < p.times.length; i++) {
      const t = offset ? this._shiftedTime(p.times, i, offset, tf) : p.times[i];
      if (t === null || t <= lastTime) continue;
      const v = p.values[i];
      const c = p.colors ? p.colors[i] : undefined;
      if (isCandle) {
        if (!Array.isArray(v) || v.some((x) => x === null)) {
          data.push({ time: t });
        } else {
          data.push({ time: t, open: v[0], high: v[1], low: v[2], close: v[3], ...(c ? { color: c, wickColor: c, borderColor: c } : {}) });
          s.valueByTime.set(t, v[3]);
        }
        lastTime = t;
        continue;
      }
      if (v === null || v === undefined || typeof v !== "number") {
        if (breaks) {
          data.push({ time: t });
          lastTime = t;
        }
        continue;
      }
      const item = { time: t, value: v };
      if (c !== undefined) {
        const col = c || "rgba(0,0,0,0)";
        if (s.kind === "series" && (p.options.style === "style_area" || p.options.style === "style_areabr")) item.lineColor = col;
        else item.color = col;
        s.colorByTime.set(t, c);
      }
      data.push(item);
      s.valueByTime.set(t, v);
      lastTime = t;
    }
    try {
      s.api.setData(data);
      s.api.applyOptions({ color: base });
    } catch {
      /* series removed concurrently */
    }
  }

  _buildScene(res, hlineValues) {
    const tf = this.ctrl.tfSeconds;
    const plots = res.plots;
    const timesRef = (plots.find((p) => p.times) || {}).times || this.ctrl.bars.map((b) => b.time);
    const scene = { barTimes: timesRef, bg: [], fills: [], shapes: [], labels: [], lines: [], boxes: [], polylines: [], linefills: [], tables: [], trades: [] };
    const byTitle = new Map(plots.filter((p) => p.times).map((p) => [p.title, p]));

    for (const p of plots) {
      const kind = kindOf(p);
      if (kind === "bg" && p.colors) {
        p.times.forEach((t, i) => {
          const c = p.colors[i];
          if (c) scene.bg.push({ time: t, color: c });
        });
      } else if (kind === "fill") {
        // Each side is either an hline (constant) or a plot (possibly offset into the future).
        const side = (title) => {
          if (hlineValues.has(title)) {
            const v = hlineValues.get(title);
            return { fn: () => v, times: null };
          }
          const src = byTitle.get(title);
          if (!src) return null;
          const off = src.options.offset || 0;
          const map = new Map();
          src.times.forEach((t, i) => {
            const tt = off ? this._shiftedTime(src.times, i, off, tf) : t;
            const v = src.values[i];
            if (tt !== null && typeof v === "number") map.set(tt, v);
          });
          return { fn: (t) => (map.has(t) ? map.get(t) : null), times: [...map.keys()] };
        };
        const a = side(p.options.plot1);
        const b = side(p.options.plot2);
        if (!a || !b) continue;
        const src1 = byTitle.get(p.options.plot1) || byTitle.get(p.options.plot2);
        const off = src1 ? src1.options.offset || 0 : 0;
        const colorAt = new Map();
        p.times.forEach((t, i) => {
          const tt = off ? this._shiftedTime(p.times, i, off, tf) : t;
          if (tt !== null) colorAt.set(tt, p.colors ? p.colors[i] : undefined);
        });
        let times = [...new Set([...(a.times || []), ...(b.times || [])])];
        if (!times.length) times = [...colorAt.keys()];
        times.sort((x, y) => x - y);
        const pts = times.map((t) => ({ time: t, v1: a.fn(t), v2: b.fn(t), color: colorAt.get(t) }));
        scene.fills.push({ color: p.options.color || "rgba(41,98,255,0.2)", points: pts });
      } else if (kind === "shape" || kind === "char") {
        const o = p.options;
        p.times.forEach((t, i) => {
          const v = p.values[i];
          const show = v === true || (typeof v === "number" && v !== 0);
          if (!show) return;
          const ex = p.extra ? p.extra[i] : undefined;
          const color = p.colors && p.colors[i] !== undefined ? p.colors[i] : o.color;
          if (!color && p.colors && p.colors[i] === null) return; // na colour hides the shape
          const isArrow = kind === "shape" && !o.shape && typeof v === "number" && (o.location === undefined || o.location === null);
          scene.shapes.push({
            time: t,
            value: v,
            kind: isArrow ? "arrow" : kind,
            shape: o.shape,
            char: o.char,
            location: o.location || "AboveBar",
            color: color || (isArrow ? (v >= 0 ? "#26A69A" : "#EF5350") : "#2962FF"),
            text: (ex && ex.text) || o.text,
            textcolor: (ex && ex.textcolor) || o.textcolor,
            size: o.size || "small",
          });
        });
      } else if (kind === "objects") {
        const map = { __labels__: "labels", __lines__: "lines", __boxes__: "boxes", __polylines__: "polylines", __linefills__: "linefills", __tables__: "tables" };
        const target = map[p.key];
        if (target) scene[target] = p.objects || [];
      }
    }

    if (res.strategy) {
      for (const tr of res.strategy.trades) {
        const long = tr.size >= 0;
        scene.trades.push({ time: tr.entryTime, price: tr.entryPrice, dir: long ? "buy" : "sell", text: tr.entryId || (long ? "Long" : "Short") });
        scene.trades.push({ time: tr.exitTime, price: tr.exitPrice, dir: long ? "sell" : "buy", text: tr.profit >= 0 ? `+${tr.profit.toFixed(2)}` : tr.profit.toFixed(2) });
      }
      for (const tr of res.strategy.openTrades) {
        const long = tr.size >= 0;
        scene.trades.push({ time: tr.entryTime, price: tr.entryPrice, dir: long ? "buy" : "sell", text: tr.entryId || "Open" });
      }
    }
    return scene;
  }

  _applyVisibility() {
    const vis = this.cfg.visible !== false;
    for (const s of this.series) {
      try {
        s.api.applyOptions({ visible: vis });
      } catch {
        /* removed */
      }
    }
    for (const pl of this.priceLines) {
      try {
        pl.line.applyOptions({ lineVisible: vis });
      } catch {
        /* removed */
      }
    }
    if (this.primitive) this.primitive.setVisible(vis);
  }

  _teardownSeries() {
    const chart = this.ctrl.chart;
    for (const pl of this.priceLines) {
      try {
        pl.series.removePriceLine(pl.line);
      } catch {
        /* gone */
      }
    }
    this.priceLines = [];
    if (this.primitive && this.primitive.series) {
      try {
        this.primitive.series.detachPrimitive(this.primitive);
      } catch {
        /* gone */
      }
    }
    for (const s of this.series) {
      try {
        chart.removeSeries(s.api);
      } catch {
        /* gone */
      }
    }
    this.series = [];
    if (this.ownAnchor && this.anchor) {
      try {
        chart.removeSeries(this.anchor);
      } catch {
        /* gone */
      }
    }
    this.anchor = null;
    this.ownAnchor = false;
    this.structureKey = null;
  }

  /** Called when the chart's main series is recreated (chart type change). */
  rebind() {
    this._teardownSeries();
    if (this.result) this._apply(this.result);
  }

  destroy() {
    this.destroyed = true;
    this._teardownSeries();
    this.ctrl._setBarColors(this.id, null);
    this.ctrl._cleanupEmptyPanes();
  }

  // ----------------------------------------------------------------- legend
  valuesAt(time) {
    const out = [];
    for (const s of this.series) {
      let v = time === null ? null : s.valueByTime.get(time);
      if (time === null) {
        // last available value
        const vals = [...s.valueByTime.values()];
        v = vals.length ? vals[vals.length - 1] : null;
      }
      const c = (time !== null && s.colorByTime.get(time)) || s.plot.options.color || "#2962FF";
      const digits = this.result.declaration.precision ?? (this.overlay ? this.ctrl.digits : autoDigits(v));
      const text = v === null || v === undefined ? "∅" : this.result.declaration.format === "volume" ? formatVolume(v) : Number(v).toFixed(digits);
      out.push({ title: s.plot.title, color: c, text });
    }
    return out;
  }
}

function formatVolume(v) {
  const a = Math.abs(v);
  if (a >= 1e9) return (v / 1e9).toFixed(2) + "B";
  if (a >= 1e6) return (v / 1e6).toFixed(2) + "M";
  if (a >= 1e3) return (v / 1e3).toFixed(2) + "K";
  return v.toFixed(0);
}
