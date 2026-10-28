import { describe, expect, it } from "vitest";
import { PineTS, Indicator } from "pinets";
import { BUILTIN_INDICATORS, NEW_INDICATOR_TEMPLATE, NEW_STRATEGY_TEMPLATE } from "../pine/builtins.js";

const candles = [];
let p = 100;
for (let i = 0; i < 400; i++) {
  const o = p;
  p = p * (1 + Math.sin(i / 23) * 0.003 + Math.cos(i * 1.7) * 0.002);
  candles.push({ open: o, high: Math.max(o, p) * 1.002, low: Math.min(o, p) * 0.998, close: p, volume: 1000 + ((i * 37) % 500), openTime: 1704067200000 + i * 60000, closeTime: 1704067200000 + (i + 1) * 60000 - 1 });
}

describe("built-in Pine library", () => {
  for (const b of [...BUILTIN_INDICATORS, { id: "tpl_ind", source: NEW_INDICATOR_TEMPLATE }, { id: "tpl_strat", source: NEW_STRATEGY_TEMPLATE }]) {
    it(`${b.id} runs without errors`, async () => {
      const ind = new Indicator(b.source);
      const res = await new PineTS(candles.slice(), "TEST", "1").run(ind);
      const plots = Object.keys(res.plots).filter((k) => !k.startsWith("__"));
      const hasDrawings = Object.entries(res.plots).some(([k, v]) => k.startsWith("__") && v.data[0] && v.data[0].value && v.data[0].value.length);
      expect(plots.length > 0 || hasDrawings || res.strategy).toBeTruthy();
    });
  }
});
