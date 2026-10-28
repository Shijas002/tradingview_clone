import { describe, expect, it } from "vitest";
import { DrawingManager } from "../chart/drawings/DrawingManager.js";

// Linear fake chart: x = (time - 1000) / 60 * 10 px, y = (110 - price) * 10 px
function fakeConv() {
  const tToL = (t) => (t - 1000) / 60;
  return {
    timeToX: (t) => tToL(t) * 10,
    xToTime: (x) => 1000 + (x / 10) * 60,
    xToLogical: (x) => x / 10,
    timeToLogical: tToL,
    logicalToTime: (l) => 1000 + l * 60,
    priceToY: (p) => (110 - p) * 10,
    yToPrice: (y) => 110 - y / 10,
    paneSize: () => ({ width: 800, height: 400 }),
    barNearX: (x) => ({ time: 1000 + Math.round(x / 10) * 60, open: 100, high: 105, low: 95, close: 102 }),
    visiblePriceRange: () => ({ min: 90, max: 110 }),
    digits: () => 2,
  };
}

function setup() {
  const events = { changes: [], selected: null, toolDone: 0 };
  const m = new DrawingManager(fakeConv(), {
    onChange: (l) => events.changes.push(l),
    onSelect: (d) => (events.selected = d),
    onToolDone: () => events.toolDone++,
    onEditText: () => {},
    requestRedraw: () => {},
  });
  return { m, events };
}

describe("DrawingManager", () => {
  it("creates a trend line with two clicks", () => {
    const { m, events } = setup();
    m.setTool("trendline");
    expect(m.pointerDown(100, 100)).toBe(true);
    m.pointerUp(100, 100);
    m.pointerMove(300, 50);
    m.pointerDown(300, 50);
    const list = events.changes.at(-1);
    expect(list).toHaveLength(1);
    expect(list[0].type).toBe("trendline");
    expect(list[0].points[0]).toEqual({ time: 1600, price: 100 });
    expect(list[0].points[1]).toEqual({ time: 2800, price: 105 });
    expect(events.toolDone).toBe(1);
    expect(events.selected.id).toBe(list[0].id);
  });

  it("creates a 2-point tool with press-drag-release", () => {
    const { m, events } = setup();
    m.setTool("rect");
    m.pointerDown(100, 100);
    m.pointerMove(200, 200);
    m.pointerUp(200, 200);
    expect(events.changes.at(-1)[0].points).toHaveLength(2);
  });

  it("hit-tests, moves and deletes", () => {
    const { m, events } = setup();
    m.setDrawings([{ id: "a", type: "trendline", points: [{ time: 1000, price: 100 }, { time: 1600, price: 100 }], style: { color: "#fff", width: 1 } }]);
    m.setTool("cursor");
    expect(m.hitTest(50, 100)).toMatchObject({ id: "a", handle: null });
    expect(m.hitTest(50, 150)).toBeNull();
    expect(m.pointerDown(50, 100)).toBe(true); // grab body
    m.pointerMove(70, 80);
    m.pointerUp(70, 80);
    const moved = events.changes.at(-1)[0];
    expect(moved.points[0].time).toBeCloseTo(1120);
    expect(moved.points[0].price).toBeCloseTo(102);
    expect(m.deleteSelected()).toBe(true);
    expect(events.changes.at(-1)).toHaveLength(0);
  });

  it("drags a handle and snaps with the magnet", () => {
    const { m, events } = setup();
    m.setDrawings([{ id: "b", type: "trendline", points: [{ time: 1000, price: 100 }, { time: 1600, price: 100 }], style: { color: "#fff", width: 1 } }]);
    m.pointerDown(0, 100); // select via handle 0 (also on the line)
    m.pointerUp(0, 100);
    m.setMagnet(true);
    expect(m.pointerDown(100, 100)).toBe(true); // handle 1 at (100,100)
    m.pointerMove(203, 52); // near high=105 -> y=50
    m.pointerUp(203, 52);
    const p = events.changes.at(-1)[0].points[1];
    expect(p.price).toBe(105);
    expect(p.time).toBe(2200);
  });

  it("places long position with defaults in one click", () => {
    const { m, events } = setup();
    m.setTool("long");
    m.pointerDown(200, 100);
    const d = events.changes.at(-1)[0];
    expect(d.points).toHaveLength(3);
    expect(d.points[1].price).toBeGreaterThan(d.points[0].price);
    expect(d.points[2].price).toBeLessThan(d.points[0].price);
  });

  it("locked drawings are not moved", () => {
    const { m, events } = setup();
    m.setDrawings([{ id: "c", type: "hline", points: [{ time: 1000, price: 100 }], style: { color: "#fff", width: 1 }, locked: true }]);
    expect(m.pointerDown(400, 100)).toBe(true);
    m.pointerMove(400, 150);
    m.pointerUp(400, 150);
    expect(events.changes).toHaveLength(0);
  });
});
