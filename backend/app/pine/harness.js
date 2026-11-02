// Server-side Pine runner (v1.1). Loaded into an embedded V8 (mini-racer) after the
// PineTS bundle. Mirrors frontend/src/pine/pine.worker.js so a script behaves the same
// on the server (alerts) as on the chart.
//
//   __pineRun(argsJson) -> Promise<string JSON>
//     args:   { source, inputs, bars:[{time,open,high,low,close,volume}], tickerId,
//               timeframe (Pine "1","60","D"...), backendTf ("M1"...), provider, digits }
//     result: { title, plots: {title: [[time, value], ...last 3]}, alerts: [{type,title,message,time}] }
//   globalThis.PYFETCH(provider, symbol, backendTf, count) is provided by Python and
//   returns a JSON string of bars (used by request.security).

var TF_SECONDS = { M1: 60, M5: 300, M15: 900, M30: 1800, H1: 3600, H4: 14400, D1: 86400, W1: 604800, MN1: 2592000 };
var PINE_TF = { "1": "M1", "5": "M5", "15": "M15", "30": "M30", "60": "H1", "240": "H4", D: "D1", "1D": "D1", W: "W1", "1W": "W1", M: "MN1", "1M": "MN1" };
var EXCHANGE_LITERAL = /(["'])(NSE|BSE|MCX):([^"'\n]+)\1/g;
var indicatorCache = new Map();

function protectExchange(src) {
  return String(src).replace(EXCHANGE_LITERAL, function (_, q, ex, rest) { return q + ex + "~" + rest + q; });
}
function restoreExchange(v) {
  return typeof v === "string" ? v.replace(/^(NSE|BSE|MCX)~/, "$1:") : v;
}
function protectValue(v) {
  return typeof v === "string" ? v.replace(/^(NSE|BSE|MCX):/, "$1~") : v;
}
function toPineCandle(bar, tfSeconds) {
  return {
    open: bar.open, high: bar.high, low: bar.low, close: bar.close, volume: bar.volume || 0,
    openTime: bar.time * 1000, closeTime: (bar.time + tfSeconds) * 1000 - 1,
  };
}
function getIndicator(source) {
  var ind = indicatorCache.get(source);
  if (!ind) {
    ind = new PineTSLib.Indicator(protectExchange(source));
    indicatorCache.set(source, ind);
    if (indicatorCache.size > 64) indicatorCache.delete(indicatorCache.keys().next().value);
  }
  return ind;
}
function bare(x) { return String(x).split(":").pop(); }

function makeProvider(a, candles) {
  var digits = a.digits == null ? 5 : a.digits;
  var indian = a.provider === "upstox";
  return {
    configure: function () {},
    getSymbolInfo: async function (raw) {
      var tickerId = restoreExchange(String(raw));
      var parts = tickerId.indexOf(":") >= 0 ? [tickerId.slice(0, tickerId.indexOf(":")), tickerId.slice(tickerId.indexOf(":") + 1)] : [a.provider || "LOCAL", tickerId];
      var ticker = parts[1];
      return {
        current_contract: "", description: tickerId, isin: "", main_tickerid: tickerId, prefix: parts[0], root: ticker, ticker: ticker,
        tickerid: parts[0] + ":" + ticker, type: indian ? "stock" : "forex", basecurrency: indian ? ticker : ticker.slice(0, 3), country: indian ? "IN" : "",
        currency: indian ? "INR" : ticker.length >= 6 ? ticker.slice(3, 6) : "USD", timezone: indian ? "Asia/Kolkata" : "Etc/UTC",
        employees: 0, industry: "", sector: "", shareholders: 0, shares_outstanding_float: 0, shares_outstanding_total: 0, expiration_date: 0,
        session: indian ? "0915-1530" : "24x7", volumetype: indian ? "base" : "tick", mincontract: 1, minmove: 1,
        mintick: 1 / Math.pow(10, digits), pointvalue: 1, pricescale: Math.pow(10, digits),
        recommendations_buy: 0, recommendations_buy_strong: 0, recommendations_date: 0, recommendations_hold: 0, recommendations_sell: 0,
        recommendations_sell_strong: 0, recommendations_total: 0, target_price_average: 0, target_price_date: 0, target_price_estimates: 0,
        target_price_high: 0, target_price_low: 0, target_price_median: 0,
      };
    },
    getMarketData: async function (tickerId, timeframe, limit) {
      var full = restoreExchange(String(tickerId || a.tickerId));
      var own = !tickerId || full === a.tickerId ||
        (indian ? full.indexOf(":") < 0 && full === bare(a.tickerId) : bare(full) === bare(a.tickerId));
      if (own && (timeframe === a.timeframe || !timeframe)) return candles.slice();
      var tf = PINE_TF[String(timeframe)] || (timeframe === a.timeframe ? a.backendTf : null);
      if (!tf) throw new Error('request.security: timeframe "' + timeframe + '" is not supported (use 1, 5, 15, 30, 60, 240, D, W or M)');
      var sym = indian ? full : bare(full);
      var count = Math.min(Math.max(limit || 1000, 100), 5000);
      var json = await globalThis.PYFETCH(a.provider, sym, tf, count);
      return JSON.parse(json).map(function (b) { return toPineCandle(b, TF_SECONDS[tf]); });
    },
  };
}

function cleanValue(v) {
  if (typeof v === "boolean") return v;
  return typeof v === "number" && isFinite(v) ? v : null;
}

globalThis.__pineRun = async function (argsJson) {
  var a = JSON.parse(argsJson);
  var tfs = TF_SECONDS[a.backendTf] || 60;
  var candles = a.bars.map(function (b) { return toPineCandle(b, tfs); });
  if (!candles.length) throw new Error("No candles to run the script on");
  var ind = getIndicator(a.source);
  var inputs = {};
  Object.keys(a.inputs || {}).forEach(function (k) { inputs[k] = protectValue(a.inputs[k]); });
  ind.inputs = inputs;
  var pine = new PineTSLib.PineTS(makeProvider(a, candles), a.tickerId, a.timeframe, candles.length);
  var ctx = await pine.run(ind);
  var plots = {};
  Object.keys(ctx.plots || {}).forEach(function (k) {
    if (k.indexOf("__") === 0) return;
    var p = ctx.plots[k];
    var data = p.data || [];
    plots[p.title || k] = data.slice(-3).map(function (d) { return [Math.round(d.time / 1000), cleanValue(d.value)]; });
  });
  var last = candles.length - 1;
  var alerts = (ctx.alerts || []).filter(function (x) { return x.bar_index === last; }).map(function (x) {
    return { type: x.type, title: restoreExchange(x.title || ""), message: restoreExchange(x.message || ""), time: Math.round((x.time || 0) / 1000) };
  });
  var decl = ctx.indicator || (ctx.strategy && ctx.strategy.config) || {};
  return JSON.stringify({ title: decl.title || "Script", plots: plots, alerts: alerts });
};
