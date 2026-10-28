// Small 2D geometry helpers (CSS pixel space).

export function distToSegment(px, py, x1, y1, x2, y2) {
  const dx = x2 - x1;
  const dy = y2 - y1;
  const len2 = dx * dx + dy * dy;
  if (len2 === 0) return Math.hypot(px - x1, py - y1);
  let t = ((px - x1) * dx + (py - y1) * dy) / len2;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(px - (x1 + t * dx), py - (y1 + t * dy));
}

/** Extend segment p1->p2 to a far point in either direction (canvas clips the rest). */
export function extendSegment(x1, y1, x2, y2, left, right, far = 20000) {
  const dx = x2 - x1;
  const dy = y2 - y1;
  const len = Math.hypot(dx, dy) || 1;
  const ux = dx / len;
  const uy = dy / len;
  const a = left ? [x1 - ux * far, y1 - uy * far] : [x1, y1];
  const b = right ? [x2 + ux * far, y2 + uy * far] : [x2, y2];
  return [a[0], a[1], b[0], b[1]];
}

export function pointInRect(px, py, x1, y1, x2, y2, pad = 0) {
  const l = Math.min(x1, x2) - pad;
  const r = Math.max(x1, x2) + pad;
  const t = Math.min(y1, y2) - pad;
  const b = Math.max(y1, y2) + pad;
  return px >= l && px <= r && py >= t && py <= b;
}

export function distToRectEdges(px, py, x1, y1, x2, y2) {
  return Math.min(
    distToSegment(px, py, x1, y1, x2, y1),
    distToSegment(px, py, x2, y1, x2, y2),
    distToSegment(px, py, x2, y2, x1, y2),
    distToSegment(px, py, x1, y2, x1, y1),
  );
}

export function distToPolyline(px, py, pts) {
  let best = Infinity;
  for (let i = 1; i < pts.length; i++) best = Math.min(best, distToSegment(px, py, pts[i - 1][0], pts[i - 1][1], pts[i][0], pts[i][1]));
  return best;
}

export function drawArrowHead(ctx, x1, y1, x2, y2, size) {
  const ang = Math.atan2(y2 - y1, x2 - x1);
  ctx.beginPath();
  ctx.moveTo(x2, y2);
  ctx.lineTo(x2 - size * Math.cos(ang - Math.PI / 7), y2 - size * Math.sin(ang - Math.PI / 7));
  ctx.lineTo(x2 - size * Math.cos(ang + Math.PI / 7), y2 - size * Math.sin(ang + Math.PI / 7));
  ctx.closePath();
  ctx.fill();
}

export function roundRect(ctx, x, y, w, h, r) {
  const rr = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + rr, y);
  ctx.arcTo(x + w, y, x + w, y + h, rr);
  ctx.arcTo(x + w, y + h, x, y + h, rr);
  ctx.arcTo(x, y + h, x, y, rr);
  ctx.arcTo(x, y, x + w, y, rr);
  ctx.closePath();
}

export function textBox(ctx, text, x, y, { font = "12px sans-serif", color = "#fff", bg = "rgba(41,98,255,0.9)", padX = 6, padY = 3, align = "center", baseline = "middle" } = {}) {
  ctx.font = font;
  const lines = String(text).split("\n");
  const lineH = parseInt(font, 10) * 1.25;
  const w = Math.max(...lines.map((l) => ctx.measureText(l).width)) + padX * 2;
  const h = lineH * lines.length + padY * 2;
  let bx = x;
  if (align === "center") bx = x - w / 2;
  else if (align === "right") bx = x - w;
  let by = y;
  if (baseline === "middle") by = y - h / 2;
  else if (baseline === "bottom") by = y - h;
  if (bg) {
    ctx.fillStyle = bg;
    roundRect(ctx, bx, by, w, h, 3);
    ctx.fill();
  }
  ctx.fillStyle = color;
  ctx.textAlign = "left";
  ctx.textBaseline = "top";
  lines.forEach((l, i) => ctx.fillText(l, bx + padX, by + padY + i * lineH));
  return { x: bx, y: by, w, h };
}
