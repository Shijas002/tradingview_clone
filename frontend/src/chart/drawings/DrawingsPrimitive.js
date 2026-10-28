// lightweight-charts series primitive that renders the DrawingManager on the main pane.

class DrawingsRenderer {
  constructor(manager) {
    this.manager = manager;
  }

  draw(target) {
    target.useMediaCoordinateSpace(({ context, mediaSize }) => {
      this.manager.render(context, { width: mediaSize.width, height: mediaSize.height });
    });
  }
}

class DrawingsPaneView {
  constructor(manager) {
    this._renderer = new DrawingsRenderer(manager);
  }

  zOrder() {
    return "top";
  }

  renderer() {
    return this._renderer;
  }
}

class PriceAxisLabel {
  constructor(primitive, item) {
    this.p = primitive;
    this.item = item;
  }

  coordinate() {
    const y = this.p.series ? this.p.series.priceToCoordinate(this.item.price) : null;
    return y === null ? -100 : y;
  }

  text() {
    return Number(this.item.price).toFixed(this.p.digits());
  }

  textColor() {
    return "#FFFFFF";
  }

  backColor() {
    return this.item.color;
  }

  visible() {
    return true;
  }

  tickVisible() {
    return true;
  }
}

export class DrawingsPrimitive {
  constructor(manager, digits) {
    this.manager = manager;
    this.digits = digits;
    this._paneViews = [new DrawingsPaneView(manager)];
    this.series = null;
    this._requestUpdate = null;
  }

  attached({ series, requestUpdate }) {
    this.series = series;
    this._requestUpdate = requestUpdate;
  }

  detached() {
    this.series = null;
    this._requestUpdate = null;
  }

  requestRedraw() {
    if (this._requestUpdate) this._requestUpdate();
  }

  updateAllViews() {}

  paneViews() {
    return this._paneViews;
  }

  priceAxisViews() {
    return this.manager.axisLabels().map((item) => new PriceAxisLabel(this, item));
  }

  hitTest(x, y) {
    const m = this.manager;
    if (m.tool !== "cursor") return { cursorStyle: "crosshair", externalId: "drawing-tool", zOrder: "top" };
    if (m.locked || m.hidden) return null;
    const hit = m.hitTest(x, y);
    if (!hit) return null;
    return {
      cursorStyle: hit.handle !== null && hit.handle !== undefined ? "pointer" : "move",
      externalId: hit.id,
      zOrder: "top",
      distance: hit.distance,
    };
  }
}
