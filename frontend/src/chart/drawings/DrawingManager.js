// DrawingManager: model, rendering and mouse interaction for chart drawings.
//
// Drawings are stored in data space ({time, price} points) so they survive zooming,
// timeframe changes and reloads. Rendering happens inside a lightweight-charts
// series primitive (see DrawingsPrimitive) on the main price pane.

import { DASH, FIB_LEVELS, TOOLS, defaultStyle } from "./tools.js";
import { distToPolyline, distToRectEdges, distToSegment, drawArrowHead, extendSegment, pointInRect, textBox } from "./geometry.js";
import { uid, withAlpha } from "../../utils/market.js";

const HANDLE_R = 5;
const HIT_TOL = 6;

export class DrawingManager {
  /**
   * @param conv coordinate converter supplied by the ChartController
   * @param callbacks { onChange(list), onSelect(drawing|null), onToolDone(), onEditText(drawing), requestRedraw() }
   */
  constructor(conv, callbacks) {
    this.conv = conv;
    this.cb = callbacks;
    this.drawings = [];
    this.selectedId = null;
    this.hoverId = null;
    this.tool = "cursor";
    this.magnet = false;
    this.locked = false;
    this.hidden = false;
    this.creating = null; // { drawing, placed, downX, downY }
    this.drag = null; // { id, handle, startX, startY, orig }
  }

  // ------------------------------------------------------------------ state
  setDrawings(list) {
    if (this.drag || this.creating) return; // don't clobber an in-flight edit
    this.drawings = (list || []).map((d) => ({ ...d, points: d.points.map((p) => ({ ...p })), style: { ...d.style } }));
    if (this.selectedId && !this.drawings.some((d) => d.id === this.selectedId)) this._select(null);
    this.cb.requestRedraw();
  }

  setTool(tool) {
    this.tool = tool || "cursor";
    if (this.creating) {
      this.creating = null;
      this.cb.requestRedraw();
    }
  }

  setMagnet(v) {
    this.magnet = !!v;
  }

  setLocked(v) {
    this.locked = !!v;
  }

  setHidden(v) {
    this.hidden = !!v;
    this.cb.requestRedraw();
  }

  get selected() {
    return this.drawings.find((d) => d.id === this.selectedId) || null;
  }

  _select(id) {
    if (this.selectedId === id) return;
    this.selectedId = id;
    this.cb.onSelect(this.selected);
    this.cb.requestRedraw();
  }

  _emit() {
    this.cb.onChange(this.drawings.map((d) => ({ ...d, points: d.points.map((p) => ({ ...p })), style: { ...d.style } })));
  }

  deleteSelected() {
    if (!this.selectedId) return false;
    this.drawings = this.drawings.filter((d) => d.id !== this.selectedId);
    this._select(null);
    this._emit();
    return true;
  }

  cancel() {
    if (this.creating) {
      this.creating = null;
      this.cb.requestRedraw();
      return true;
    }
    if (this.selectedId) {
      this._select(null);
      return true;
    }
    return false;
  }

  updateSelectedStyle(patch) {
    const d = this.selected;
    if (!d) return;
    d.style = { ...d.style, ...patch };
    this._emit();
    this.cb.requestRedraw();
  }

  // ------------------------------------------------------- coordinate utils
  _xy(p) {
    const x = this.conv.timeToX(p.time);
    const y = this.conv.priceToY(p.price);
    return x === null || y === null ? null : [x, y];
  }

  _pointAt(x, y) {
    let time = this.conv.xToTime(x);
    let price = this.conv.yToPrice(y);
    if (this.magnet) {
      const bar = this.conv.barNearX(x);
      if (bar) {
        time = bar.time;
        const candidates = [bar.open, bar.high, bar.low, bar.close];
        let best = price;
        let bestD = Infinity;
        for (const c of candidates) {
          const cy = this.conv.priceToY(c);
          if (cy === null) continue;
          const dd = Math.abs(cy - y);
          if (dd < bestD) {
            bestD = dd;
            best = c;
          }
        }
        if (bestD < 30) price = best;
      }
    }
    return { time, price };
  }

  // ----------------------------------------------------------- interaction
  /** Returns true when the manager takes ownership of the gesture (chart must not pan). */
  pointerDown(x, y, ev) {
    if (this.hidden) return false;
    if (this.tool !== "cursor") return this._createDown(x, y, ev);

    if (this.locked) return false;
    const hit = this.hitTest(x, y);
    if (!hit) {
      this._select(null);
      return false;
    }
    const d = this.drawings.find((dd) => dd.id === hit.id);
    this._select(d.id);
    if (d.locked) return true; // selectable but not movable
    this.drag = {
      id: d.id,
      handle: hit.handle,
      startX: x,
      startY: y,
      startLogical: this.conv.xToLogical(x),
      startPrice: this.conv.yToPrice(y),
      orig: d.points.map((p) => ({ ...p })),
      moved: false,
    };
    return true;
  }

  pointerMove(x, y) {
    if (this.creating) {
      const c = this.creating;
      const p = this._pointAt(x, y);
      const d = c.drawing;
      if (TOOLS[d.type].kind === "free") {
        const last = d.points[d.points.length - 1];
        const lxy = this._xy(last);
        if (!lxy || Math.hypot(lxy[0] - x, lxy[1] - y) > 3) d.points.push(p);
      } else {
        for (let i = c.placed; i < d.points.length; i++) d.points[i] = { ...p };
      }
      this.cb.requestRedraw();
      return true;
    }
    if (this.drag) {
      const dr = this.drag;
      const d = this.drawings.find((dd) => dd.id === dr.id);
      if (!d) return false;
      if (Math.hypot(x - dr.startX, y - dr.startY) > 2) dr.moved = true;
      if (dr.handle === null || dr.handle === undefined) {
        // move whole drawing by a delta in bars and price
        const dl = this.conv.xToLogical(x) - dr.startLogical;
        const dp = this.conv.yToPrice(y) - dr.startPrice;
        d.points = dr.orig.map((p) => ({
          time: this.conv.logicalToTime(this.conv.timeToLogical(p.time) + dl),
          price: p.price + dp,
        }));
      } else {
        const p = this._pointAt(x, y);
        this._moveHandle(d, dr.handle, p);
      }
      this.cb.requestRedraw();
      return true;
    }
    const hit = this.tool === "cursor" && !this.locked && !this.hidden ? this.hitTest(x, y) : null;
    const id = hit ? hit.id : null;
    if (id !== this.hoverId) {
      this.hoverId = id;
      this.cb.requestRedraw();
    }
    return false;
  }

  pointerUp(x, y) {
    if (this.creating) {
      const c = this.creating;
      const kind = TOOLS[c.drawing.type].kind;
      if (kind === "free") {
        this._finishCreate();
        return true;
      }
      // press-drag-release places the second point for 2/3-point tools
      if (c.placed === 1 && Math.hypot(x - c.downX, y - c.downY) > 6) {
        c.placed = 2;
        if (c.placed >= c.drawing.points.length) this._finishCreate();
      }
      return true;
    }
    if (this.drag) {
      const moved = this.drag.moved;
      this.drag = null;
      if (moved) this._emit();
      return true;
    }
    return false;
  }

  doubleClick(x, y) {
    const hit = this.hitTest(x, y);
    if (!hit) return false;
    const d = this.drawings.find((dd) => dd.id === hit.id);
    if (d && d.type === "text") {
      this._select(d.id);
      this.cb.onEditText(d);
      return true;
    }
    return false;
  }

  _createDown(x, y) {
    const type = this.tool;
    const def = TOOLS[type];
    if (!def) return false;
    const p = this._pointAt(x, y);

    if (this.creating) {
      const c = this.creating;
      c.placed += 1;
      for (let i = c.placed; i < c.drawing.points.length; i++) c.drawing.points[i] = { ...p };
      if (c.placed >= c.drawing.points.length) this._finishCreate();
      this.cb.requestRedraw();
      return true;
    }

    const drawing = { id: uid("drw"), type, points: [], style: defaultStyle(type), locked: false, hidden: false };
    if (def.kind === 1) {
      drawing.points = [p];
      this._commitNew(drawing);
      if (type === "text") this.cb.onEditText(drawing);
      return true;
    }
    if (def.kind === "position") {
      const range = this.conv.visiblePriceRange();
      const span = range ? (range.max - range.min) * 0.08 : p.price * 0.005;
      const endTime = this.conv.logicalToTime(this.conv.timeToLogical(p.time) + 25);
      const up = type === "long" ? 1 : -1;
      drawing.points = [p, { time: endTime, price: p.price + up * span * 1.5 }, { time: endTime, price: p.price - up * span }];
      this._commitNew(drawing);
      return true;
    }
    if (def.kind === "free") {
      drawing.points = [p];
      this.creating = { drawing, placed: 1, downX: x, downY: y };
      return true;
    }
    drawing.points = Array.from({ length: def.kind }, () => ({ ...p }));
    this.creating = { drawing, placed: 1, downX: x, downY: y };
    this.cb.requestRedraw();
    return true;
  }

  _finishCreate() {
    const d = this.creating.drawing;
    this.creating = null;
    if (TOOLS[d.type].kind === "free" && d.points.length < 2) {
      this.cb.requestRedraw();
      return;
    }
    this._commitNew(d);
  }

  _commitNew(drawing) {
    this.drawings = [...this.drawings, drawing];
    this.selectedId = drawing.id;
    this._emit();
    this.cb.onSelect(drawing);
    this.cb.onToolDone();
    this.cb.requestRedraw();
  }

  _moveHandle(d, handle, p) {
    if (d.type === "long" || d.type === "short") {
      if (handle === 0) d.points[0] = p;
      else {
        d.points[handle] = { time: p.time, price: p.price };
        const other = handle === 1 ? 2 : 1;
        d.points[other] = { ...d.points[other], time: p.time };
      }
      return;
    }
    if (d.type === "hline") {
      d.points[0] = { ...d.points[0], price: p.price };
      return;
    }
    if (d.type === "vline") {
      d.points[0] = { ...d.points[0], time: p.time };
      return;
    }
    d.points[handle] = p;
  }

  // ----------------------------------------------------------- hit testing
  _handles(d) {
    const { width, height } = this.conv.paneSize();
    if (d.type === "hline") {
      const y = this.conv.priceToY(d.points[0].price);
      return y === null ? [] : [[width / 2, y]];
    }
    if (d.type === "vline") {
      const x = this.conv.timeToX(d.points[0].time);
      return x === null ? [] : [[x, height / 2]];
    }
    if (d.type === "brush") {
      const a = this._xy(d.points[0]);
      const b = this._xy(d.points[d.points.length - 1]);
      return [a, b].filter(Boolean);
    }
    return d.points.map((p) => this._xy(p)).filter(Boolean);
  }

  hitTest(x, y) {
    if (this.hidden) return null;
    // selected drawing's handles first
    const sel = this.selected;
    if (sel && !sel.hidden) {
      const hs = this._handles(sel);
      for (let i = 0; i < hs.length; i++) {
        if (Math.hypot(hs[i][0] - x, hs[i][1] - y) <= HANDLE_R + 3) {
          const handle = sel.type === "brush" ? (i === 0 ? 0 : sel.points.length - 1) : i;
          return { id: sel.id, handle, distance: 0 };
        }
      }
    }
    let best = null;
    for (let i = this.drawings.length - 1; i >= 0; i--) {
      const d = this.drawings[i];
      if (d.hidden) continue;
      const dist = this._distance(d, x, y);
      if (dist !== null && dist <= HIT_TOL && (!best || dist < best.distance)) best = { id: d.id, handle: null, distance: dist };
    }
    return best;
  }

  _distance(d, x, y) {
    const { width, height } = this.conv.paneSize();
    const pts = d.points.map((p) => this._xy(p));
    if (pts.some((p) => p === null) && !["hline", "vline"].includes(d.type)) return null;
    const s = d.style;
    switch (d.type) {
      case "trendline":
      case "ray":
      case "extended":
      case "arrow": {
        const [x1, y1, x2, y2] = extendSegment(pts[0][0], pts[0][1], pts[1][0], pts[1][1], s.extendLeft, s.extendRight);
        return distToSegment(x, y, x1, y1, x2, y2);
      }
      case "hline": {
        const yy = this.conv.priceToY(d.points[0].price);
        return yy === null ? null : Math.abs(y - yy);
      }
      case "hray":
        return x >= pts[0][0] - HIT_TOL ? Math.abs(y - pts[0][1]) : null;
      case "vline": {
        const xx = this.conv.timeToX(d.points[0].time);
        return xx === null ? null : Math.abs(x - xx);
      }
      case "cross":
        return Math.min(Math.abs(y - pts[0][1]), Math.abs(x - pts[0][0]));
      case "rect":
      case "pricerange":
        if (pointInRect(x, y, pts[0][0], pts[0][1], pts[1][0], pts[1][1])) return s.fill || d.type === "pricerange" ? 1 : distToRectEdges(x, y, pts[0][0], pts[0][1], pts[1][0], pts[1][1]);
        return distToRectEdges(x, y, pts[0][0], pts[0][1], pts[1][0], pts[1][1]);
      case "channel": {
        const off = this._channelOffset(d, pts);
        const d1 = distToSegment(x, y, pts[0][0], pts[0][1], pts[1][0], pts[1][1]);
        const d2 = distToSegment(x, y, pts[0][0], pts[0][1] + off, pts[1][0], pts[1][1] + off);
        const inside = pointInRect(x, y, Math.min(pts[0][0], pts[1][0]), Math.min(pts[0][1], pts[0][1] + off, pts[1][1], pts[1][1] + off), Math.max(pts[0][0], pts[1][0]), Math.max(pts[0][1], pts[0][1] + off, pts[1][1], pts[1][1] + off));
        return Math.min(d1, d2, inside ? 2 : Infinity);
      }
      case "fib": {
        const xl = Math.min(pts[0][0], pts[1][0]);
        const xr = s.extendRight ? width : Math.max(pts[0][0], pts[1][0]);
        if (x < xl - HIT_TOL || x > xr + HIT_TOL) return null;
        let best = Infinity;
        for (const lv of FIB_LEVELS) {
          const yy = this.conv.priceToY(this._fibPrice(d, lv.level));
          if (yy !== null) best = Math.min(best, Math.abs(y - yy));
        }
        return Math.min(best, distToSegment(x, y, pts[0][0], pts[0][1], pts[1][0], pts[1][1]));
      }
      case "text": {
        const b = this._textBounds(d, pts[0]);
        return b && pointInRect(x, y, b.x, b.y, b.x + b.w, b.y + b.h, 2) ? 0 : null;
      }
      case "brush":
        return distToPolyline(x, y, pts);
      case "long":
      case "short": {
        const xl = pts[0][0];
        const xr = pts[1][0];
        const top = Math.min(pts[1][1], pts[2][1]);
        const bot = Math.max(pts[1][1], pts[2][1]);
        return pointInRect(x, y, xl, top, xr, bot) ? 1 : null;
      }
      default:
        return null;
    }
  }

  _channelOffset(d, pts) {
    // vertical pixel offset of the parallel line through point 3
    const [p1, p2, p3] = pts;
    if (!p3) return 0;
    const dx = p2[0] - p1[0];
    const t = dx === 0 ? 0 : (p3[0] - p1[0]) / dx;
    const yOnLine = p1[1] + t * (p2[1] - p1[1]);
    return p3[1] - yOnLine;
  }

  _fibPrice(d, level) {
    const a = d.points[0].price;
    const b = d.points[1].price;
    return b - (b - a) * level;
  }

  _textBounds(d, xy) {
    if (!xy || !this._measureCtx) return null;
    const ctx = this._measureCtx;
    const fs = d.style.fontSize || 14;
    ctx.font = `${fs}px -apple-system, BlinkMacSystemFont, "Trebuchet MS", Roboto, sans-serif`;
    const lines = String(d.style.text || "").split("\n");
    const w = Math.max(10, ...lines.map((l) => ctx.measureText(l).width)) + 8;
    const h = fs * 1.25 * lines.length + 6;
    return { x: xy[0], y: xy[1] - h, w, h };
  }

  // ---------------------------------------------------------------- render
  render(ctx, size) {
    this._measureCtx = ctx;
    if (this.hidden) return;
    const all = this.creating ? [...this.drawings, this.creating.drawing] : this.drawings;
    for (const d of all) {
      if (d.hidden) continue;
      ctx.save();
      try {
        this._renderOne(ctx, d, size);
      } catch {
        /* a broken drawing must never break the chart */
      }
      ctx.restore();
    }
    const sel = this.selected;
    if (sel && !sel.hidden) {
      for (const h of this._handles(sel)) {
        ctx.beginPath();
        ctx.arc(h[0], h[1], HANDLE_R, 0, Math.PI * 2);
        ctx.fillStyle = "#131722";
        ctx.fill();
        ctx.lineWidth = 2;
        ctx.strokeStyle = "#2962FF";
        ctx.stroke();
      }
    }
  }

  _stroke(ctx, s, hovered) {
    ctx.strokeStyle = s.color;
    ctx.lineWidth = (s.width || 1) + (hovered ? 1 : 0);
    ctx.setLineDash(DASH[s.lineStyle] || []);
    ctx.lineCap = "round";
  }

  _line(ctx, x1, y1, x2, y2) {
    ctx.beginPath();
    ctx.moveTo(x1, y1);
    ctx.lineTo(x2, y2);
    ctx.stroke();
  }

  _priceText(p) {
    return Number(p).toFixed(this.conv.digits());
  }

  _renderOne(ctx, d, { width, height }) {
    const s = d.style;
    const hovered = d.id === this.hoverId || d.id === this.selectedId;
    const pts = d.points.map((p) => this._xy(p));
    const font = '11px -apple-system, BlinkMacSystemFont, "Trebuchet MS", Roboto, sans-serif';
    this._stroke(ctx, s, hovered);

    switch (d.type) {
      case "trendline":
      case "ray":
      case "extended":
      case "arrow": {
        if (!pts[0] || !pts[1]) return;
        const [x1, y1, x2, y2] = extendSegment(pts[0][0], pts[0][1], pts[1][0], pts[1][1], s.extendLeft, s.extendRight);
        this._line(ctx, x1, y1, x2, y2);
        if (d.type === "arrow") {
          ctx.fillStyle = s.color;
          ctx.setLineDash([]);
          drawArrowHead(ctx, pts[0][0], pts[0][1], pts[1][0], pts[1][1], 8 + (s.width || 1) * 2);
        }
        break;
      }
      case "hline": {
        const y = this.conv.priceToY(d.points[0].price);
        if (y === null) return;
        this._line(ctx, 0, y, width, y);
        break;
      }
      case "hray": {
        if (!pts[0]) return;
        this._line(ctx, pts[0][0], pts[0][1], width, pts[0][1]);
        break;
      }
      case "vline": {
        const x = this.conv.timeToX(d.points[0].time);
        if (x === null) return;
        this._line(ctx, x, 0, x, height);
        break;
      }
      case "cross": {
        if (!pts[0]) return;
        this._line(ctx, 0, pts[0][1], width, pts[0][1]);
        this._line(ctx, pts[0][0], 0, pts[0][0], height);
        break;
      }
      case "rect": {
        if (!pts[0] || !pts[1]) return;
        const x = Math.min(pts[0][0], pts[1][0]);
        const y = Math.min(pts[0][1], pts[1][1]);
        const w = Math.abs(pts[1][0] - pts[0][0]);
        const h = Math.abs(pts[1][1] - pts[0][1]);
        if (s.fill) {
          ctx.fillStyle = s.fill;
          ctx.fillRect(x, y, w, h);
        }
        ctx.strokeRect(x, y, w, h);
        break;
      }
      case "channel": {
        if (!pts[0] || !pts[1]) return;
        const off = pts[2] ? this._channelOffset(d, pts) : 0;
        const [ax, ay, bx, by] = extendSegment(pts[0][0], pts[0][1], pts[1][0], pts[1][1], s.extendLeft, s.extendRight);
        if (s.fill && off) {
          ctx.fillStyle = s.fill;
          ctx.beginPath();
          ctx.moveTo(ax, ay);
          ctx.lineTo(bx, by);
          ctx.lineTo(bx, by + off);
          ctx.lineTo(ax, ay + off);
          ctx.closePath();
          ctx.fill();
        }
        this._line(ctx, ax, ay, bx, by);
        if (off) {
          this._line(ctx, ax, ay + off, bx, by + off);
          ctx.setLineDash([4, 4]);
          ctx.lineWidth = 1;
          this._line(ctx, ax, ay + off / 2, bx, by + off / 2);
        }
        break;
      }
      case "fib": {
        if (!pts[0] || !pts[1]) return;
        const xl = Math.min(pts[0][0], pts[1][0]);
        const xr = s.extendRight ? width : Math.max(pts[0][0], pts[1][0]);
        ctx.setLineDash([3, 3]);
        ctx.lineWidth = 1;
        ctx.strokeStyle = withAlpha(s.color, 0.6);
        this._line(ctx, pts[0][0], pts[0][1], pts[1][0], pts[1][1]);
        let prevY = null;
        FIB_LEVELS.forEach((lv) => {
          const price = this._fibPrice(d, lv.level);
          const y = this.conv.priceToY(price);
          if (y === null) return;
          if (prevY !== null) {
            ctx.fillStyle = withAlpha(lv.color, 0.1);
            ctx.fillRect(xl, Math.min(prevY, y), xr - xl, Math.abs(y - prevY));
          }
          prevY = y;
          ctx.setLineDash([]);
          ctx.strokeStyle = lv.color;
          ctx.lineWidth = hovered ? 2 : 1;
          this._line(ctx, xl, y, xr, y);
          ctx.fillStyle = lv.color;
          ctx.font = font;
          ctx.textAlign = "right";
          ctx.textBaseline = "middle";
          const label = s.showPrices === false ? `${lv.level}` : `${lv.level} (${this._priceText(price)})`;
          ctx.fillText(label, xl - 4, y);
        });
        break;
      }
      case "text": {
        if (!pts[0]) return;
        const b = this._textBounds(d, pts[0]);
        const fs = s.fontSize || 14;
        if (s.background && s.background !== "rgba(41,98,255,0.0)") {
          ctx.fillStyle = s.background;
          ctx.fillRect(b.x, b.y, b.w, b.h);
        }
        if (hovered) {
          ctx.setLineDash([3, 3]);
          ctx.lineWidth = 1;
          ctx.strokeStyle = "#2962FF";
          ctx.strokeRect(b.x, b.y, b.w, b.h);
        }
        ctx.fillStyle = s.color;
        ctx.font = `${fs}px -apple-system, BlinkMacSystemFont, "Trebuchet MS", Roboto, sans-serif`;
        ctx.textAlign = "left";
        ctx.textBaseline = "top";
        String(s.text || "").split("\n").forEach((line, i) => ctx.fillText(line, b.x + 4, b.y + 3 + i * fs * 1.25));
        break;
      }
      case "brush": {
        const good = pts.filter(Boolean);
        if (good.length < 2) return;
        ctx.lineJoin = "round";
        ctx.beginPath();
        ctx.moveTo(good[0][0], good[0][1]);
        for (let i = 1; i < good.length; i++) ctx.lineTo(good[i][0], good[i][1]);
        ctx.stroke();
        break;
      }
      case "pricerange": {
        if (!pts[0] || !pts[1]) return;
        const x = Math.min(pts[0][0], pts[1][0]);
        const y = Math.min(pts[0][1], pts[1][1]);
        const w = Math.abs(pts[1][0] - pts[0][0]);
        const h = Math.abs(pts[1][1] - pts[0][1]);
        const up = d.points[1].price >= d.points[0].price;
        const col = up ? "#2962FF" : "#F23645";
        ctx.fillStyle = withAlpha(col, 0.18);
        ctx.fillRect(x, y, w, h);
        ctx.strokeStyle = col;
        ctx.setLineDash([]);
        ctx.lineWidth = 1;
        const midX = x + w / 2;
        this._line(ctx, midX, pts[0][1], midX, pts[1][1]);
        ctx.fillStyle = col;
        drawArrowHead(ctx, midX, pts[0][1], midX, pts[1][1], 8);
        const dp = d.points[1].price - d.points[0].price;
        const pct = (dp / d.points[0].price) * 100;
        const bars = Math.round(this.conv.timeToLogical(d.points[1].time) - this.conv.timeToLogical(d.points[0].time));
        const label = `${dp >= 0 ? "+" : ""}${this._priceText(dp)} (${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%)\n${bars} bars`;
        textBox(ctx, label, midX, up ? y - 6 : y + h + 6, { font, bg: col, baseline: up ? "bottom" : "top" });
        break;
      }
      case "long":
      case "short": {
        if (!pts[0] || !pts[1] || !pts[2]) return;
        const xl = pts[0][0];
        const xr = pts[1][0];
        const yE = pts[0][1];
        const yT = pts[1][1];
        const yS = pts[2][1];
        ctx.setLineDash([]);
        ctx.fillStyle = "rgba(8,153,129,0.22)";
        ctx.fillRect(Math.min(xl, xr), Math.min(yE, yT), Math.abs(xr - xl), Math.abs(yT - yE));
        ctx.fillStyle = "rgba(242,54,69,0.22)";
        ctx.fillRect(Math.min(xl, xr), Math.min(yE, yS), Math.abs(xr - xl), Math.abs(yS - yE));
        ctx.strokeStyle = "#787B86";
        ctx.lineWidth = 1;
        this._line(ctx, xl, yE, xr, yE);
        const entry = d.points[0].price;
        const target = d.points[1].price;
        const stop = d.points[2].price;
        const tPct = ((target - entry) / entry) * 100;
        const sPct = ((stop - entry) / entry) * 100;
        const risk = Math.abs(entry - stop);
        const rr = risk === 0 ? 0 : Math.abs(target - entry) / risk;
        const qty = s.qty || 1;
        const cx = (xl + xr) / 2;
        textBox(ctx, `Target: ${this._priceText(target)} (${tPct >= 0 ? "+" : ""}${tPct.toFixed(2)}%)  P&L: ${((target - entry) * qty).toFixed(2)}`, cx, yT, { font, bg: "#089981", baseline: yT < yE ? "bottom" : "top" });
        textBox(ctx, `Stop: ${this._priceText(stop)} (${sPct >= 0 ? "+" : ""}${sPct.toFixed(2)}%)  P&L: ${((stop - entry) * qty).toFixed(2)}`, cx, yS, { font, bg: "#F23645", baseline: yS < yE ? "bottom" : "top" });
        textBox(ctx, `${d.type === "long" ? "Long" : "Short"} · R:R ${rr.toFixed(2)} · Qty ${qty}`, cx, yE, { font, bg: "rgba(42,46,57,0.95)" });
        break;
      }
      default:
        break;
    }
  }

  /** Price labels for the right price scale (horizontal lines + selected drawing). */
  axisLabels() {
    if (this.hidden) return [];
    const out = [];
    for (const d of this.drawings) {
      if (d.hidden) continue;
      if (d.type === "hline" || d.type === "hray" || d.type === "cross") out.push({ price: d.points[0].price, color: d.style.color });
      else if (d.id === this.selectedId) for (const p of d.points) out.push({ price: p.price, color: "#2962FF" });
    }
    return out;
  }
}
