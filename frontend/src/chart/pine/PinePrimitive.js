// Series primitive that renders everything a Pine script produces which is not a
// plain series: bgcolor bands, fill() areas, plotshape/plotchar/plotarrow markers,
// label/line/box/polyline/linefill/table drawings and strategy trade markers.

import { roundRect } from "../drawings/geometry.js";

const SIZE_PX = { tiny: 7, small: 10, normal: 14, auto: 10, large: 20, huge: 28 };
const LABEL_FONT = { tiny: 9, small: 10, normal: 12, auto: 11, large: 15, huge: 19 };
const FONT_FAMILY = '-apple-system, BlinkMacSystemFont, "Trebuchet MS", Roboto, sans-serif';

function isVisibleColor(c) {
  if (!c) return false;
  if (typeof c === "string" && c.length === 9 && c.endsWith("00")) return false; // #RRGGBB00
  return true;
}

function dash(style) {
  if (!style) return [];
  if (style.includes("dotted")) return [2, 3];
  if (style.includes("dashed")) return [6, 4];
  return [];
}

function drawShape(ctx, shape, x, y, s, color, up) {
  ctx.fillStyle = color;
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  switch (shape) {
    case "shape_triangle_up":
      ctx.moveTo(x, y - s / 2);
      ctx.lineTo(x + s / 2, y + s / 2);
      ctx.lineTo(x - s / 2, y + s / 2);
      ctx.closePath();
      ctx.fill();
      break;
    case "shape_triangle_down":
      ctx.moveTo(x, y + s / 2);
      ctx.lineTo(x + s / 2, y - s / 2);
      ctx.lineTo(x - s / 2, y - s / 2);
      ctx.closePath();
      ctx.fill();
      break;
    case "shape_arrow_up":
    case "arrow_up":
      ctx.moveTo(x, y - s / 2);
      ctx.lineTo(x + s / 2, y);
      ctx.lineTo(x + s / 5, y);
      ctx.lineTo(x + s / 5, y + s / 2);
      ctx.lineTo(x - s / 5, y + s / 2);
      ctx.lineTo(x - s / 5, y);
      ctx.lineTo(x - s / 2, y);
      ctx.closePath();
      ctx.fill();
      break;
    case "shape_arrow_down":
    case "arrow_down":
      ctx.moveTo(x, y + s / 2);
      ctx.lineTo(x + s / 2, y);
      ctx.lineTo(x + s / 5, y);
      ctx.lineTo(x + s / 5, y - s / 2);
      ctx.lineTo(x - s / 5, y - s / 2);
      ctx.lineTo(x - s / 5, y);
      ctx.lineTo(x - s / 2, y);
      ctx.closePath();
      ctx.fill();
      break;
    case "shape_circle":
      ctx.arc(x, y, s / 2, 0, Math.PI * 2);
      ctx.fill();
      break;
    case "shape_square":
      ctx.rect(x - s / 2, y - s / 2, s, s);
      ctx.fill();
      break;
    case "shape_diamond":
      ctx.moveTo(x, y - s / 2);
      ctx.lineTo(x + s / 2, y);
      ctx.lineTo(x, y + s / 2);
      ctx.lineTo(x - s / 2, y);
      ctx.closePath();
      ctx.fill();
      break;
    case "shape_cross":
      ctx.moveTo(x - s / 2, y);
      ctx.lineTo(x + s / 2, y);
      ctx.moveTo(x, y - s / 2);
      ctx.lineTo(x, y + s / 2);
      ctx.stroke();
      break;
    case "shape_flag":
      ctx.moveTo(x - s / 3, y + s / 2);
      ctx.lineTo(x - s / 3, y - s / 2);
      ctx.lineTo(x + s / 2, y - s / 4);
      ctx.lineTo(x - s / 3, y);
      ctx.stroke();
      ctx.fill();
      break;
    case "shape_label_up":
    case "shape_label_down": {
      const w = s * 1.6;
      const h = s * 1.1;
      const top = shape === "shape_label_up" ? y : y - h;
      roundRect(ctx, x - w / 2, top, w, h, 2);
      ctx.fill();
      break;
    }
    default: // xcross
      ctx.moveTo(x - s / 2, y - s / 2);
      ctx.lineTo(x + s / 2, y + s / 2);
      ctx.moveTo(x + s / 2, y - s / 2);
      ctx.lineTo(x - s / 2, y + s / 2);
      ctx.stroke();
      break;
  }
  return up;
}

class PineRenderer {
  constructor(prim, layer) {
    this.p = prim;
    this.layer = layer; // "bottom": bgcolor, fills, boxes | "top": lines, shapes, labels, trades, tables
  }

  draw(target) {
    target.useMediaCoordinateSpace(({ context, mediaSize }) => {
      const p = this.p;
      if (!p.series || !p.scene || !p.visible) return;
      try {
        if (this.layer === "bottom") p.drawBottom(context, mediaSize.width, mediaSize.height);
        else p.drawTop(context, mediaSize.width, mediaSize.height);
      } catch {
        /* never let a malformed script result break the chart */
      }
    });
  }
}

class PineView {
  constructor(prim, z) {
    this.r = new PineRenderer(prim, z === "bottom" ? "bottom" : "top");
    this.z = z;
  }

  zOrder() {
    return this.z;
  }

  renderer() {
    return this.r;
  }
}

export class PinePrimitive {
  /** @param ctrl ChartController (time->x conversion, bar lookup) */
  constructor(ctrl, isOverlay) {
    this.ctrl = ctrl;
    this.isOverlay = isOverlay;
    this.scene = null;
    this.visible = true;
    this.series = null;
    this._req = null;
    this._views = [new PineView(this, "bottom"), new PineView(this, "top")];
  }

  attached({ series, requestUpdate }) {
    this.series = series;
    this._req = requestUpdate;
  }

  detached() {
    this.series = null;
    this._req = null;
  }

  setScene(scene, isOverlay) {
    this.scene = scene;
    this.isOverlay = isOverlay;
    if (this._req) this._req();
  }

  setVisible(v) {
    this.visible = v;
    if (this._req) this._req();
  }

  updateAllViews() {}

  paneViews() {
    return this._views;
  }

  _x(time) {
    return this.ctrl.timeToX(time);
  }

  _y(price) {
    return this.series ? this.series.priceToCoordinate(price) : null;
  }

  _timeOfIndex(i) {
    return this.ctrl.timeOfIndex(this.scene.barTimes, i);
  }

  _objX(o, key = "x") {
    const v = o[key];
    if (v === undefined || v === null) return null;
    if (o.xloc === "bt") return this._x(Math.round(v / 1000));
    return this._x(this._timeOfIndex(v));
  }

  drawBackgroundScene(ctx, width, height) {
    const sc = this.scene;
    if (!sc.bg || !sc.bg.length) return;
    const spacing = this.ctrl.barSpacing();
    for (const b of sc.bg) {
      const x = this._x(b.time);
      if (x === null || x < -spacing || x > width + spacing) continue;
      ctx.fillStyle = b.color;
      ctx.fillRect(x - spacing / 2, 0, spacing + 0.5, height);
    }
  }

  drawBottom(ctx, width, height) {
    this.drawBackgroundScene(ctx, width, height);
    this._drawFills(ctx, width);
    this._drawBoxes(ctx, width);
    this._drawLinefills(ctx);
  }

  drawTop(ctx, width, height) {
    this._drawLines(ctx, width);
    this._drawPolylines(ctx);
    this._drawShapes(ctx, width, height);
    this._drawLabels(ctx, width);
    this._drawTrades(ctx, width);
    this._drawTables(ctx, width, height);
  }

  _drawFills(ctx, width) {
    for (const f of this.scene.fills || []) {
      const pts = f.points;
      for (let i = 1; i < pts.length; i++) {
        const a = pts[i - 1];
        const b = pts[i];
        const color = b.color === undefined ? f.color : b.color;
        if (!isVisibleColor(color)) continue;
        if (a.v1 === null || a.v2 === null || b.v1 === null || b.v2 === null) continue;
        const xa = this._x(a.time);
        const xb = this._x(b.time);
        if (xa === null || xb === null || (xa < -20 && xb < -20) || (xa > width + 20 && xb > width + 20)) continue;
        const ya1 = this._y(a.v1);
        const ya2 = this._y(a.v2);
        const yb1 = this._y(b.v1);
        const yb2 = this._y(b.v2);
        if ([ya1, ya2, yb1, yb2].some((v) => v === null)) continue;
        ctx.fillStyle = color;
        ctx.beginPath();
        ctx.moveTo(xa, ya1);
        ctx.lineTo(xb, yb1);
        ctx.lineTo(xb, yb2);
        ctx.lineTo(xa, ya2);
        ctx.closePath();
        ctx.fill();
      }
    }
  }

  _markerY(m, height, size) {
    const loc = (m.location || "AboveBar").toLowerCase();
    if (loc === "absolute") return typeof m.value === "number" ? this._y(m.value) : null;
    if (loc === "top") return 4 + size / 2;
    if (loc === "bottom") return height - 4 - size / 2;
    if (!this.isOverlay) return loc === "belowbar" ? height - 4 - size / 2 : 4 + size / 2;
    const bar = this.ctrl.barAtTime(m.time);
    if (!bar) return null;
    if (loc === "belowbar") {
      const y = this._y(bar.low);
      return y === null ? null : y + 6 + size / 2;
    }
    const y = this._y(bar.high);
    return y === null ? null : y - 6 - size / 2;
  }

  _drawShapes(ctx, width, height) {
    for (const m of this.scene.shapes || []) {
      const x = this._x(m.time);
      if (x === null || x < -20 || x > width + 20) continue;
      const size = SIZE_PX[m.size] || SIZE_PX.small;
      const loc = (m.location || "AboveBar").toLowerCase();
      let shape = m.shape;
      if (m.kind === "arrow") shape = m.value >= 0 ? "arrow_up" : "arrow_down";
      const y = m.kind === "arrow" ? this._markerY({ ...m, location: m.value >= 0 ? "BelowBar" : "AboveBar" }, height, size) : this._markerY(m, height, size);
      if (y === null) continue;
      const color = m.color || "#2962FF";
      if (m.kind === "char") {
        ctx.fillStyle = color;
        ctx.font = `${size + 2}px ${FONT_FAMILY}`;
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(m.char || "★", x, y);
      } else {
        drawShape(ctx, shape, x, y, size, color, loc === "belowbar");
      }
      if (m.text) {
        const below = loc === "belowbar" || shape === "shape_label_up" || (m.kind === "arrow" && m.value >= 0);
        ctx.fillStyle = m.textcolor || (shape && shape.startsWith("shape_label") ? "#FFFFFF" : color);
        ctx.font = `10px ${FONT_FAMILY}`;
        ctx.textAlign = "center";
        if (shape && shape.startsWith("shape_label")) {
          ctx.textBaseline = "middle";
          const h = size * 1.1;
          ctx.fillText(m.text, x, shape === "shape_label_up" ? y + h / 2 : y - h / 2);
        } else {
          ctx.textBaseline = below ? "top" : "bottom";
          ctx.fillText(m.text, x, below ? y + size / 2 + 2 : y - size / 2 - 2);
        }
      }
    }
  }

  _drawLabels(ctx, width) {
    for (const o of this.scene.labels || []) {
      const x = this._objX(o);
      if (x === null || x < -100 || x > width + 100) continue;
      let y;
      if (o.yloc === "ab" || o.yloc === "bl") {
        const bar = this.ctrl.barAtTime(o.xloc === "bt" ? Math.round(o.x / 1000) : this._timeOfIndex(o.x));
        y = bar ? this._y(o.yloc === "ab" ? bar.high : bar.low) : null;
      } else y = this._y(o.y);
      if (y === null) continue;
      const fs = LABEL_FONT[o.size] || 11;
      ctx.font = `${fs}px ${FONT_FAMILY}`;
      const lines = String(o.text ?? "").split("\n");
      const tw = Math.max(0, ...lines.map((l) => ctx.measureText(l).width));
      const padX = 5;
      const padY = 3;
      const w = tw + padX * 2;
      const h = lines.length * fs * 1.25 + padY * 2;
      const style = o.style || "style_label_down";
      const bg = o.color || "#2962FF";
      let bx = x - w / 2;
      let by = y - h - 6;
      const pointer = 6;
      if (style === "style_label_up") by = y + pointer;
      else if (style === "style_label_left") {
        bx = x + pointer;
        by = y - h / 2;
      } else if (style === "style_label_right") {
        bx = x - w - pointer;
        by = y - h / 2;
      } else if (style === "style_label_center" || style === "style_none" || style === "style_text_outline") {
        by = y - h / 2;
      }
      if (style !== "style_none" && isVisibleColor(bg)) {
        ctx.fillStyle = bg;
        roundRect(ctx, bx, by, w, h, 3);
        ctx.fill();
        ctx.beginPath();
        if (style === "style_label_down") {
          ctx.moveTo(x - 5, by + h);
          ctx.lineTo(x + 5, by + h);
          ctx.lineTo(x, y);
        } else if (style === "style_label_up") {
          ctx.moveTo(x - 5, by);
          ctx.lineTo(x + 5, by);
          ctx.lineTo(x, y);
        } else if (style === "style_label_left") {
          ctx.moveTo(bx, y - 5);
          ctx.lineTo(bx, y + 5);
          ctx.lineTo(x, y);
        } else if (style === "style_label_right") {
          ctx.moveTo(bx + w, y - 5);
          ctx.lineTo(bx + w, y + 5);
          ctx.lineTo(x, y);
        }
        ctx.closePath();
        ctx.fill();
      }
      ctx.fillStyle = o.textcolor || (style === "style_none" ? bg : "#FFFFFF");
      ctx.textAlign = "center";
      ctx.textBaseline = "top";
      lines.forEach((l, i) => ctx.fillText(l, bx + w / 2, by + padY + i * fs * 1.25));
    }
  }

  _lineEnds(o, width) {
    const x1 = this._objX(o, "x1");
    const x2 = this._objX(o, "x2");
    const y1 = this._y(o.y1);
    const y2 = this._y(o.y2);
    if ([x1, x2, y1, y2].some((v) => v === null)) return null;
    let a = [x1, y1];
    let b = [x2, y2];
    const ext = o.extend || "n";
    const dx = x2 - x1;
    const dy = y2 - y1;
    if (dx !== 0 || dy !== 0) {
      const far = (width + 2000) / (Math.hypot(dx, dy) || 1);
      if (ext === "l" || ext === "b" || ext === "left" || ext === "both") a = [x1 - dx * far, y1 - dy * far];
      if (ext === "r" || ext === "b" || ext === "right" || ext === "both") b = [x2 + dx * far, y2 + dy * far];
    }
    return [a, b];
  }

  _drawLines(ctx, width) {
    for (const o of this.scene.lines || []) {
      const ends = this._lineEnds(o, width);
      if (!ends) continue;
      ctx.strokeStyle = o.color || "#2962FF";
      ctx.lineWidth = o.width || 1;
      ctx.setLineDash(dash(o.style));
      ctx.beginPath();
      ctx.moveTo(ends[0][0], ends[0][1]);
      ctx.lineTo(ends[1][0], ends[1][1]);
      ctx.stroke();
      ctx.setLineDash([]);
      const st = o.style || "";
      const head = (from, to) => {
        const ang = Math.atan2(to[1] - from[1], to[0] - from[0]);
        ctx.fillStyle = o.color || "#2962FF";
        ctx.beginPath();
        ctx.moveTo(to[0], to[1]);
        ctx.lineTo(to[0] - 9 * Math.cos(ang - 0.45), to[1] - 9 * Math.sin(ang - 0.45));
        ctx.lineTo(to[0] - 9 * Math.cos(ang + 0.45), to[1] - 9 * Math.sin(ang + 0.45));
        ctx.closePath();
        ctx.fill();
      };
      if (st.includes("arrow_right") || st.includes("arrow_both")) head(ends[0], ends[1]);
      if (st.includes("arrow_left") || st.includes("arrow_both")) head(ends[1], ends[0]);
    }
  }

  _drawLinefills(ctx) {
    const byId = new Map((this.scene.lines || []).map((l) => [l.id, l]));
    for (const lf of this.scene.linefills || []) {
      const l1 = byId.get(lf.line1 && typeof lf.line1 === "object" ? lf.line1.id : lf.line1);
      const l2 = byId.get(lf.line2 && typeof lf.line2 === "object" ? lf.line2.id : lf.line2);
      if (!l1 || !l2) continue;
      const a = this._lineEnds({ ...l1, extend: "n" }, 0);
      const b = this._lineEnds({ ...l2, extend: "n" }, 0);
      if (!a || !b) continue;
      ctx.fillStyle = lf.color || "rgba(41,98,255,0.2)";
      ctx.beginPath();
      ctx.moveTo(a[0][0], a[0][1]);
      ctx.lineTo(a[1][0], a[1][1]);
      ctx.lineTo(b[1][0], b[1][1]);
      ctx.lineTo(b[0][0], b[0][1]);
      ctx.closePath();
      ctx.fill();
    }
  }

  _drawBoxes(ctx, width) {
    for (const o of this.scene.boxes || []) {
      let x1 = this._objX(o, "left");
      let x2 = this._objX(o, "right");
      const y1 = this._y(o.top);
      const y2 = this._y(o.bottom);
      if ([x1, x2, y1, y2].some((v) => v === null)) continue;
      const ext = o.extend || "none";
      if (ext === "left" || ext === "both" || ext === "l" || ext === "b") x1 = -10;
      if (ext === "right" || ext === "both" || ext === "r" || ext === "b") x2 = width + 10;
      const x = Math.min(x1, x2);
      const y = Math.min(y1, y2);
      const w = Math.abs(x2 - x1);
      const h = Math.abs(y2 - y1);
      if (isVisibleColor(o.bgcolor)) {
        ctx.fillStyle = o.bgcolor;
        ctx.fillRect(x, y, w, h);
      }
      if (isVisibleColor(o.border_color) && (o.border_width ?? 1) > 0) {
        ctx.strokeStyle = o.border_color;
        ctx.lineWidth = o.border_width ?? 1;
        ctx.setLineDash(dash(o.border_style));
        ctx.strokeRect(x, y, w, h);
        ctx.setLineDash([]);
      }
      if (o.text) {
        ctx.fillStyle = o.text_color || "#FFFFFF";
        ctx.font = `${LABEL_FONT[o.text_size] || 11}px ${FONT_FAMILY}`;
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(o.text, x + w / 2, y + h / 2);
      }
    }
  }

  _drawPolylines(ctx) {
    for (const o of this.scene.polylines || []) {
      const pts = (o.points || [])
        .map((pt) => {
          const t = pt.time !== undefined && pt.time !== null && (o.xloc === "bt" || pt.index === undefined) ? Math.round(pt.time / 1000) : this._timeOfIndex(pt.index);
          const x = this._x(t);
          const y = this._y(pt.price);
          return x === null || y === null ? null : [x, y];
        })
        .filter(Boolean);
      if (pts.length < 2) continue;
      ctx.beginPath();
      ctx.moveTo(pts[0][0], pts[0][1]);
      for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
      if (o.closed) ctx.closePath();
      if (isVisibleColor(o.fill_color)) {
        ctx.fillStyle = o.fill_color;
        ctx.fill();
      }
      ctx.strokeStyle = o.line_color || "#2962FF";
      ctx.lineWidth = o.line_width || 1;
      ctx.setLineDash(dash(o.line_style));
      ctx.stroke();
      ctx.setLineDash([]);
    }
  }

  _drawTrades(ctx, width) {
    for (const t of this.scene.trades || []) {
      const x = this._x(t.time);
      const y = this._y(t.price);
      if (x === null || y === null || x < -20 || x > width + 20) continue;
      const up = t.dir === "buy";
      const color = up ? "#2962FF" : "#F23645";
      ctx.fillStyle = color;
      const yy = up ? y + 10 : y - 10;
      ctx.beginPath();
      if (up) {
        ctx.moveTo(x, yy - 6);
        ctx.lineTo(x + 5, yy + 2);
        ctx.lineTo(x - 5, yy + 2);
      } else {
        ctx.moveTo(x, yy + 6);
        ctx.lineTo(x + 5, yy - 2);
        ctx.lineTo(x - 5, yy - 2);
      }
      ctx.closePath();
      ctx.fill();
      ctx.font = `10px ${FONT_FAMILY}`;
      ctx.textAlign = "center";
      ctx.textBaseline = up ? "top" : "bottom";
      ctx.fillText(t.text, x, up ? yy + 4 : yy - 4);
    }
  }

  _drawTables(ctx, width, height) {
    for (const t of this.scene.tables || []) {
      const cells = t.cells || [];
      const rows = cells.length;
      if (!rows) continue;
      const cols = Math.max(...cells.map((r) => (r ? r.length : 0)));
      // measure
      const colW = new Array(cols).fill(0);
      const rowH = new Array(rows).fill(0);
      for (let r = 0; r < rows; r++) {
        for (let c = 0; c < cols; c++) {
          const cell = cells[r] && cells[r][c];
          if (!cell || cell.text === undefined || cell.text === "") continue;
          const fs = LABEL_FONT[cell.text_size] || 11;
          ctx.font = `${fs}px ${FONT_FAMILY}`;
          colW[c] = Math.max(colW[c], ctx.measureText(String(cell.text)).width + 10);
          rowH[r] = Math.max(rowH[r], fs * 1.3 + 6);
        }
      }
      const W = colW.reduce((a, b) => a + b, 0);
      const H = rowH.reduce((a, b) => a + b, 0);
      if (!W || !H) continue;
      const pos = t.position || "top_right";
      let x0 = width - W - 8;
      let y0 = 8;
      if (pos.includes("left")) x0 = 8;
      if (pos.includes("center") && !pos.startsWith("middle")) x0 = (width - W) / 2;
      if (pos.startsWith("middle")) y0 = (height - H) / 2;
      if (pos.startsWith("bottom")) y0 = height - H - 8;
      if (pos === "middle_center") x0 = (width - W) / 2;
      if (isVisibleColor(t.bgcolor)) {
        ctx.fillStyle = t.bgcolor;
        ctx.fillRect(x0, y0, W, H);
      }
      let yy = y0;
      for (let r = 0; r < rows; r++) {
        let xx = x0;
        for (let c = 0; c < cols; c++) {
          const cell = cells[r] && cells[r][c];
          if (cell) {
            if (isVisibleColor(cell.bgcolor)) {
              ctx.fillStyle = cell.bgcolor;
              ctx.fillRect(xx, yy, colW[c], rowH[r]);
            }
            if (cell.text !== undefined && cell.text !== "") {
              const fs = LABEL_FONT[cell.text_size] || 11;
              ctx.font = `${fs}px ${FONT_FAMILY}`;
              ctx.fillStyle = cell.text_color || "#D1D4DC";
              ctx.textAlign = "center";
              ctx.textBaseline = "middle";
              ctx.fillText(String(cell.text), xx + colW[c] / 2, yy + rowH[r] / 2);
            }
          }
          xx += colW[c];
        }
        yy += rowH[r];
      }
      if (isVisibleColor(t.frame_color) && t.frame_width > 0) {
        ctx.strokeStyle = t.frame_color;
        ctx.lineWidth = t.frame_width;
        ctx.strokeRect(x0, y0, W, H);
      }
    }
  }
}
