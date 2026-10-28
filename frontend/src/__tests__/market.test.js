import { describe, expect, it } from "vitest";
import { bisectTime, logicalToTime, timeToLogical, toHeikinAshi, parseColor, withAlpha } from "../utils/market.js";

const bars = [0, 60, 120, 180, 240].map((t, i) => ({ time: 1000 + t, open: 1 + i, high: 2 + i, low: 0.5 + i, close: 1.5 + i, volume: 10 }));

describe("time <-> logical index", () => {
  it("maps exact, fractional and extrapolated times", () => {
    expect(timeToLogical(bars, 1000, 60)).toBe(0);
    expect(timeToLogical(bars, 1090, 60)).toBe(1.5);
    expect(timeToLogical(bars, 1240 + 120, 60)).toBe(6);
    expect(timeToLogical(bars, 1000 - 60, 60)).toBe(-1);
  });
  it("round-trips", () => {
    for (const l of [-2, 0, 1.25, 3, 7.5]) expect(timeToLogical(bars, logicalToTime(bars, l, 60), 60)).toBeCloseTo(l, 5);
  });
  it("bisects", () => {
    expect(bisectTime(bars, 999)).toBe(-1);
    expect(bisectTime(bars, 1061)).toBe(1);
    expect(bisectTime(bars, 5000)).toBe(4);
  });
});

describe("heikin ashi", () => {
  it("computes HA candles", () => {
    const ha = toHeikinAshi(bars);
    expect(ha).toHaveLength(5);
    expect(ha[0].close).toBeCloseTo((1 + 2 + 0.5 + 1.5) / 4);
    expect(ha[1].open).toBeCloseTo((ha[0].open + ha[0].close) / 2);
    for (const b of ha) expect(b.high).toBeGreaterThanOrEqual(Math.max(b.open, b.close));
  });
});

describe("colors", () => {
  it("parses pine colors", () => {
    expect(parseColor("#FF000080")).toEqual({ r: 255, g: 0, b: 0, a: 128 / 255 });
    expect(withAlpha("#2962FF", 0.5)).toBe("rgba(41,98,255,0.5)");
  });
});
