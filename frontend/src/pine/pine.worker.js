// Web Worker that runs Pine Script through the PineTS engine, off the UI thread.
//
// Messages in:
//   { type: "setData", datasetId, bars, tickerId, timeframe }   full candle history
//   { type: "updateBar", datasetId, bar }                        live candle (replace last or append)
//   { type: "dropData", datasetId }
//   { type: "run", reqId, datasetId, source, inputs }            run a script
//   { type: "inspect", reqId, source }                           parse only: inputs + declaration
// Messages out:
//   { reqId, ok: true, result } | { reqId, ok: false, error }

import { PineTS, Indicator } from "pinets";

const datasets = new Map(); // datasetId -> { candles, tickerId, timeframe }
const indicatorCache = new Map(); // source -> Indicator (keeps the transpiled code)
const MAX_CACHE = 64;

function toPineCandle(bar, tfSeconds) {
  return {
    open: bar.open,
    high: bar.high,
    low: bar.low,
    close: bar.close,
    volume: bar.volume || 0,
    openTime: bar.time * 1000,
    closeTime: (bar.time + tfSeconds) * 1000 - 1,
  };
}

// Pine timeframe strings -> backend timeframes (for request.security)
const PINE_TF = { "1": "M1", "5": "M5", "15": "M15", "30": "M30", "60": "H1", "240": "H4", D: "D1", "1D": "D1", W: "W1", "1W": "W1", M: "MN1", "1M": "MN1" };
const TF_SECONDS = { M1: 60, M5: 300, M15: 900, M30: 1800, H1: 3600, H4: 14400, D1: 86400, W1: 604800, MN1: 2592000 };
const securityCache = new Map(); // key -> { at, candles }

/**
 * Market-data provider handed to PineTS. The chart's own symbol/timeframe is served
 * from memory; any other symbol or timeframe (request.security) is fetched from the
 * backend REST API, so multi-timeframe scripts work.
 */
function makeProvider(ds) {
  return {
    configure() {},
    async getSymbolInfo(rawTickerId) {
      const tickerId = restoreExchange(String(rawTickerId));
      const digits = ds.digits ?? 5;
      const mintick = 1 / 10 ** digits;
      const indian = ds.provider === "upstox";
      const [exchange, ticker] = String(tickerId).includes(":") ? String(tickerId).split(/:(.*)/s) : [ds.provider || "LOCAL", String(tickerId)];
      return {
        current_contract: "", description: tickerId, isin: "", main_tickerid: tickerId, prefix: exchange, root: ticker,
        ticker, tickerid: `${exchange}:${ticker}`, type: indian ? "stock" : "forex", basecurrency: indian ? ticker : ticker.slice(0, 3), country: indian ? "IN" : "",
        currency: indian ? "INR" : ticker.length >= 6 ? ticker.slice(3, 6) : "USD", timezone: indian ? "Asia/Kolkata" : "Etc/UTC", employees: 0, industry: "", sector: "",
        shareholders: 0, shares_outstanding_float: 0, shares_outstanding_total: 0, expiration_date: 0, session: indian ? "0915-1530" : "24x7", volumetype: indian ? "base" : "tick",
        mincontract: 1, minmove: 1, mintick, pointvalue: 1, pricescale: 10 ** digits,
        recommendations_buy: 0, recommendations_buy_strong: 0, recommendations_date: 0, recommendations_hold: 0, recommendations_sell: 0,
        recommendations_sell_strong: 0, recommendations_total: 0, target_price_average: 0, target_price_date: 0, target_price_estimates: 0,
        target_price_high: 0, target_price_low: 0, target_price_median: 0,
      };
    },
    async getMarketData(tickerId, timeframe, limit) {
      const full = restoreExchange(String(tickerId || ds.tickerId));
      const bare = (x) => String(x).split(":").pop();
      const own =
        !tickerId || full === ds.tickerId ||
        (ds.provider === "upstox" ? !full.includes(":") && full === bare(ds.tickerId) : bare(full) === bare(ds.tickerId));
      // Indian symbols keep their exchange ("NSE:RELIANCE"); other providers use the bare name
      const sym = ds.provider === "upstox" ? full : full.split(":").pop();
      if (own && (timeframe === ds.timeframe || !timeframe)) return ds.candles.slice();
      const tf = PINE_TF[String(timeframe)] || (timeframe === ds.timeframe ? ds.backendTf : null);
      if (!tf) throw new Error(`request.security: timeframe "${timeframe}" is not supported (use 1, 5, 15, 30, 60, 240, D, W or M)`);
      if (!ds.provider) throw new Error("request.security needs a data provider");
      const count = Math.min(Math.max(limit || 1000, 100), 5000);
      const key = `${ds.provider}|${sym}|${tf}|${count}`;
      const hit = securityCache.get(key);
      if (hit && Date.now() - hit.at < 30000) return hit.candles.slice();
      const q = new URLSearchParams({ provider: ds.provider, symbol: sym, timeframe: tf, count: String(count) });
      const res = await fetch(`/api/history?${q}`);
      const body = await res.json().catch(() => null);
      if (!res.ok) throw new Error(`request.security(${sym}, ${timeframe}): ${(body && body.error) || res.status}`);
      const candles = body.candles.map((b) => toPineCandle(b, TF_SECONDS[tf]));
      securityCache.set(key, { at: Date.now(), candles });
      return candles.slice();
    },
  };
}

// PineTS drops the exchange prefix of request.security("BSE:RELIANCE", ...), but for
// Indian symbols the exchange matters (NSE vs BSE prices differ). Exchange-prefixed
// string literals are therefore rewritten to "BSE~RELIANCE" before compiling, and
// turned back into "BSE:RELIANCE" when the data is fetched or shown in the UI.
const EXCHANGE_LITERAL = /(["'])(NSE|BSE|MCX):([^"'\n]+)\1/g;
const protectExchange = (src) => String(src).replace(EXCHANGE_LITERAL, (_, q, ex, rest) => `${q}${ex}~${rest}${q}`);
const restoreExchange = (v) => (typeof v === "string" ? v.replace(/^(NSE|BSE|MCX)~/, "$1:") : v);
const protectValue = (v) => (typeof v === "string" ? v.replace(/^(NSE|BSE|MCX):/, "$1~") : v);
function restoreDeep(v) {
  if (typeof v === "string") return restoreExchange(v);
  if (Array.isArray(v)) return v.map(restoreDeep);
  if (v && typeof v === "object") return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, restoreDeep(x)]));
  return v;
}

function getIndicator(source) {
  let ind = indicatorCache.get(source);
  if (!ind) {
    ind = new Indicator(protectExchange(source));
    indicatorCache.set(source, ind);
    if (indicatorCache.size > MAX_CACHE) indicatorCache.delete(indicatorCache.keys().next().value);
  }
  return ind;
}

function cleanNumber(v) {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

/** Convert the PineTS context into a compact, structured-clone friendly result. */
function serialize(ctx, inputsMeta, declarationType) {
  const plots = [];
  for (const [key, plot] of Object.entries(ctx.plots || {})) {
    const options = { ...(plot.options || {}) };
    if (key.startsWith("__")) {
      // drawing collections: the whole list lives on the first data point
      const first = plot.data && plot.data[0];
      const items = first && Array.isArray(first.value) ? first.value.filter((o) => o && !o._deleted) : [];
      plots.push({ key, title: key, options, objects: JSON.parse(JSON.stringify(items)) });
      continue;
    }
    const n = plot.data.length;
    const times = new Array(n);
    const values = new Array(n);
    let colors = null;
    let extra = null;
    for (let i = 0; i < n; i++) {
      const d = plot.data[i];
      times[i] = Math.round(d.time / 1000);
      const v = d.value;
      if (Array.isArray(v)) values[i] = v.map(cleanNumber);
      else if (typeof v === "boolean") values[i] = v;
      else values[i] = cleanNumber(v);
      const o = d.options;
      if (o) {
        if (o.color !== undefined) {
          if (!colors) colors = new Array(n).fill(undefined);
          colors[i] = o.color;
        }
        if (o.text !== undefined || o.textcolor !== undefined) {
          if (!extra) extra = new Array(n).fill(undefined);
          extra[i] = { text: o.text, textcolor: o.textcolor };
        }
      }
    }
    plots.push({ key, title: plot.title || key, options, times, values, colors, extra });
  }

  let strategy = null;
  const st = ctx.strategy;
  if (declarationType === "strategy" && st) {
    const trades = (st.closedtrades || []).map((t) => ({
      entryId: t.entry_id,
      entryPrice: t.entry_price,
      entryTime: Math.round(t.entry_time / 1000),
      exitId: t.exit_id,
      exitPrice: t.exit_price,
      exitTime: Math.round(t.exit_time / 1000),
      size: t.size,
      profit: t.profit,
      commission: t.commission,
      maxRunup: t.max_runup,
      maxDrawdown: t.max_drawdown,
    }));
    const open = (st.opentrades || []).map((t) => ({
      entryId: t.entry_id,
      entryPrice: t.entry_price,
      entryTime: Math.round(t.entry_time / 1000),
      size: t.size,
      profit: t.profit,
    }));
    strategy = {
      initialCapital: st.initial_capital,
      currency: st.account_currency,
      netProfit: st.netprofit,
      grossProfit: st.grossprofit,
      grossLoss: st.grossloss,
      openProfit: st.openprofit,
      maxDrawdown: st.max_drawdown,
      maxDrawdownPercent: st.max_drawdown_percent_value,
      maxRunup: st.max_runup,
      sharpe: st.sharpe_ratio,
      sortino: st.sortino_ratio,
      buyAndHold: st.buy_and_hold_pnl,
      equity: st.equity,
      positionSize: st.position_size,
      trades,
      openTrades: open,
    };
  }

  // strategy() scripts keep their declaration in strategy.config instead of ctx.indicator
  const ind = ctx.indicator || (st && st.config) || {};
  return {
    declaration: {
      type: declarationType,
      title: ind.title || "Script",
      shorttitle: (ind.shorttitle && String(ind.shorttitle).trim()) || ind.title || "Script",
      overlay: !!ind.overlay,
      format: ind.format || "inherit",
      precision: typeof ind.precision === "number" && ind.precision < 10 ? ind.precision : null,
    },
    inputsMeta,
    plots,
    strategy,
    warnings: (ctx.warnings || []).map((w) => (typeof w === "string" ? w : JSON.stringify(w))).slice(0, 20),
  };
}

function errorMessage(err) {
  const msg = err && err.message ? err.message : String(err);
  return msg.length > 2000 ? msg.slice(0, 2000) + "…" : msg;
}

async function handle(msg) {
  switch (msg.type) {
    case "setData": {
      const tfSeconds = msg.tfSeconds || 60;
      datasets.set(msg.datasetId, {
        candles: msg.bars.map((b) => toPineCandle(b, tfSeconds)),
        tickerId: msg.tickerId || "SYMBOL",
        timeframe: msg.timeframe || "1",
        backendTf: msg.backendTf || null,
        provider: msg.provider || null,
        digits: msg.digits,
        tfSeconds,
      });
      return null;
    }
    case "updateBar": {
      const ds = datasets.get(msg.datasetId);
      if (!ds) return null;
      const c = toPineCandle(msg.bar, ds.tfSeconds);
      const last = ds.candles[ds.candles.length - 1];
      if (last && last.openTime === c.openTime) ds.candles[ds.candles.length - 1] = c;
      else if (!last || c.openTime > last.openTime) ds.candles.push(c);
      return null;
    }
    case "dropData":
      datasets.delete(msg.datasetId);
      return null;
    case "inspect": {
      const ind = getIndicator(msg.source);
      ind.prepare(); // throws on syntax errors
      return { inputsMeta: restoreDeep(ind.getInputsMeta()), declarationType: ind.getDeclarationType() };
    }
    case "run": {
      const ds = datasets.get(msg.datasetId);
      if (!ds) throw new Error("No chart data loaded yet");
      const ind = getIndicator(msg.source);
      ind.inputs = Object.fromEntries(Object.entries(msg.inputs || {}).map(([k, v]) => [k, protectValue(v)]));
      const pine = new PineTS(makeProvider(ds), ds.tickerId, ds.timeframe, ds.candles.length);
      const started = performance.now();
      const ctx = await pine.run(ind);
      const result = serialize(ctx, restoreDeep(ind.getInputsMeta()), ind.getDeclarationType() || "indicator");
      result.elapsedMs = Math.round(performance.now() - started);
      return result;
    }
    default:
      throw new Error(`Unknown message type ${msg.type}`);
  }
}

// Messages are processed strictly one at a time: cached Indicator instances carry
// per-run input overrides, so two runs must never interleave.
let queue = Promise.resolve();

self.onmessage = (ev) => {
  const msg = ev.data;
  queue = queue.then(async () => {
    try {
      const result = await handle(msg);
      if (msg.reqId !== undefined) self.postMessage({ reqId: msg.reqId, ok: true, result });
    } catch (err) {
      if (msg.reqId !== undefined) self.postMessage({ reqId: msg.reqId, ok: false, error: errorMessage(err) });
    }
  });
};
