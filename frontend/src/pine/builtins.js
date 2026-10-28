// Built-in indicator library. Every built-in is a plain Pine Script v6 source that
// runs through the same PineTS engine as user scripts, so built-ins and custom
// scripts behave identically (inputs dialog, legend values, styles, alerts).

const S = (id, name, category, source, description = "") => ({ id, name, category, source: source.trim() + "\n", description, builtin: true });

export const BUILTIN_INDICATORS = [
  // ------------------------------------------------------------------ trend
  S("sma", "Moving Average Simple", "Trend", `
//@version=6
indicator("Moving Average Simple", shorttitle="SMA", overlay=true)
len = input.int(20, "Length", minval=1)
src = input.source(close, "Source")
plot(ta.sma(src, len), "SMA", color.new(#2962FF, 0), 2)
`),
  S("ema", "Moving Average Exponential", "Trend", `
//@version=6
indicator("Moving Average Exponential", shorttitle="EMA", overlay=true)
len = input.int(21, "Length", minval=1)
src = input.source(close, "Source")
plot(ta.ema(src, len), "EMA", #FF9800, 2)
`),
  S("wma", "Moving Average Weighted", "Trend", `
//@version=6
indicator("Moving Average Weighted", shorttitle="WMA", overlay=true)
len = input.int(20, "Length", minval=1)
src = input.source(close, "Source")
plot(ta.wma(src, len), "WMA", #AB47BC, 2)
`),
  S("hma", "Hull Moving Average", "Trend", `
//@version=6
indicator("Hull Moving Average", shorttitle="HMA", overlay=true)
len = input.int(9, "Length", minval=2)
src = input.source(close, "Source")
plot(ta.hma(src, len), "HMA", #00BCD4, 2)
`),
  S("vwma", "Volume Weighted Moving Average", "Trend", `
//@version=6
indicator("Volume Weighted Moving Average", shorttitle="VWMA", overlay=true)
len = input.int(20, "Length", minval=1)
src = input.source(close, "Source")
plot(ta.vwma(src, len), "VWMA", #26A69A, 2)
`),
  S("ma_cross", "MA Cross", "Trend", `
//@version=6
indicator("MA Cross", overlay=true)
fastLen = input.int(9, "Fast length", minval=1)
slowLen = input.int(21, "Slow length", minval=1)
fast = ta.ema(close, fastLen)
slow = ta.ema(close, slowLen)
plot(fast, "Fast", #2962FF, 2)
plot(slow, "Slow", #FF6D00, 2)
plotshape(ta.crossover(fast, slow), "Cross up", shape.triangleup, location.belowbar, #26A69A, size=size.small)
plotshape(ta.crossunder(fast, slow), "Cross down", shape.triangledown, location.abovebar, #EF5350, size=size.small)
alertcondition(ta.crossover(fast, slow), "Bullish cross", "Fast MA crossed above slow MA")
alertcondition(ta.crossunder(fast, slow), "Bearish cross", "Fast MA crossed below slow MA")
`),
  S("ribbon", "Moving Average Ribbon", "Trend", `
//@version=6
indicator("Moving Average Ribbon", shorttitle="MA Ribbon", overlay=true)
src = input.source(close, "Source")
plot(ta.ema(src, 20), "MA 20", #F6C309, 1)
plot(ta.ema(src, 50), "MA 50", #FB9800, 1)
plot(ta.ema(src, 100), "MA 100", #FB6500, 1)
plot(ta.ema(src, 200), "MA 200", #F60C0C, 2)
`),
  S("supertrend", "Supertrend", "Trend", `
//@version=6
indicator("Supertrend", overlay=true)
atrPeriod = input.int(10, "ATR length", minval=1)
factor = input.float(3.0, "Factor", minval=0.01, step=0.01)
[st, dir] = ta.supertrend(factor, atrPeriod)
upTrend = plot(dir < 0 ? st : na, "Up trend", #26A69A, 2, plot.style_linebr)
downTrend = plot(dir < 0 ? na : st, "Down trend", #EF5350, 2, plot.style_linebr)
plotshape(dir < 0 and dir[1] >= 0 ? st : na, "Buy", shape.labelup, location.absolute, #26A69A, text="Buy", textcolor=color.white, size=size.tiny)
plotshape(dir >= 0 and dir[1] < 0 ? st : na, "Sell", shape.labeldown, location.absolute, #EF5350, text="Sell", textcolor=color.white, size=size.tiny)
alertcondition(dir < 0 and dir[1] >= 0, "Supertrend buy", "Supertrend flipped up")
alertcondition(dir >= 0 and dir[1] < 0, "Supertrend sell", "Supertrend flipped down")
`),
  S("psar", "Parabolic SAR", "Trend", `
//@version=6
indicator("Parabolic SAR", shorttitle="SAR", overlay=true)
start = input.float(0.02, "Start", step=0.001)
inc = input.float(0.02, "Increment", step=0.001)
maxV = input.float(0.2, "Max value", step=0.01)
plot(ta.sar(start, inc, maxV), "ParabolicSAR", #2962FF, 1, plot.style_cross)
`),
  S("ichimoku", "Ichimoku Cloud", "Trend", `
//@version=6
indicator("Ichimoku Cloud", shorttitle="Ichimoku", overlay=true)
convLen = input.int(9, "Conversion line length", minval=1)
baseLen = input.int(26, "Base line length", minval=1)
spanBLen = input.int(52, "Leading span B length", minval=1)
disp = input.int(26, "Lagging span", minval=1)
donchian(len) => math.avg(ta.lowest(len), ta.highest(len))
conv = donchian(convLen)
base = donchian(baseLen)
spanA = math.avg(conv, base)
spanB = donchian(spanBLen)
plot(conv, "Conversion line", #2962FF)
plot(base, "Base line", #B71C1C)
plot(close, "Lagging span", #43A047, offset=-disp + 1)
p1 = plot(spanA, "Leading span A", #A5D6A7, offset=disp - 1)
p2 = plot(spanB, "Leading span B", #EF9A9A, offset=disp - 1)
fill(p1, p2, spanA > spanB ? color.rgb(67, 160, 71, 90) : color.rgb(244, 67, 54, 90), "Cloud")
`),
  S("pivots", "Pivot Points High Low", "Trend", `
//@version=6
indicator("Pivot Points High Low", shorttitle="Pivots HL", overlay=true, max_labels_count=500)
leftBars = input.int(10, "Left bars", minval=1)
rightBars = input.int(10, "Right bars", minval=1)
ph = ta.pivothigh(leftBars, rightBars)
pl = ta.pivotlow(leftBars, rightBars)
if not na(ph)
    label.new(bar_index - rightBars, ph, str.tostring(ph, format.mintick), style=label.style_label_down, color=#EF5350, textcolor=color.white, size=size.small)
if not na(pl)
    label.new(bar_index - rightBars, pl, str.tostring(pl, format.mintick), style=label.style_label_up, color=#26A69A, textcolor=color.white, size=size.small)
`),

  // ------------------------------------------------------------- volatility
  S("bb", "Bollinger Bands", "Volatility", `
//@version=6
indicator("Bollinger Bands", shorttitle="BB", overlay=true)
len = input.int(20, "Length", minval=1)
src = input.source(close, "Source")
mult = input.float(2.0, "StdDev", minval=0.001, maxval=50)
basis = ta.sma(src, len)
dev = mult * ta.stdev(src, len)
plot(basis, "Basis", #FF6D00)
p1 = plot(basis + dev, "Upper", #2962FF)
p2 = plot(basis - dev, "Lower", #2962FF)
fill(p1, p2, color.rgb(33, 150, 243, 92), "Background")
`),
  S("kc", "Keltner Channels", "Volatility", `
//@version=6
indicator("Keltner Channels", shorttitle="KC", overlay=true)
len = input.int(20, "Length", minval=1)
mult = input.float(2.0, "Multiplier")
src = input.source(close, "Source")
basis = ta.ema(src, len)
rangeMa = ta.atr(10)
u = plot(basis + rangeMa * mult, "Upper", #2962FF)
plot(basis, "Basis", #2962FF)
l = plot(basis - rangeMa * mult, "Lower", #2962FF)
fill(u, l, color.rgb(33, 150, 243, 95), "Background")
`),
  S("donchian", "Donchian Channels", "Volatility", `
//@version=6
indicator("Donchian Channels", shorttitle="DC", overlay=true)
len = input.int(20, "Length", minval=1)
lower = ta.lowest(len)
upper = ta.highest(len)
basis = math.avg(upper, lower)
plot(basis, "Basis", #FF6D00)
u = plot(upper, "Upper", #2962FF)
l = plot(lower, "Lower", #2962FF)
fill(u, l, color.rgb(33, 150, 243, 95), "Background")
`),
  S("envelope", "Envelope", "Volatility", `
//@version=6
indicator("Envelope", shorttitle="Env", overlay=true)
len = input.int(20, "Length", minval=1)
percent = input.float(10.0, "Percent", step=0.1)
src = input.source(close, "Source")
basis = ta.sma(src, len)
k = percent / 100.0
u = plot(basis * (1 + k), "Upper", #2962FF)
plot(basis, "Basis", #FF6D00)
l = plot(basis * (1 - k), "Lower", #2962FF)
fill(u, l, color.rgb(33, 150, 243, 95), "Background")
`),
  S("atr", "Average True Range", "Volatility", `
//@version=6
indicator("Average True Range", shorttitle="ATR", overlay=false)
len = input.int(14, "Length", minval=1)
plot(ta.rma(ta.tr(true), len), "ATR", #B71C1C)
`),
  S("bbw", "Bollinger BandWidth", "Volatility", `
//@version=6
indicator("Bollinger BandWidth", shorttitle="BBW", overlay=false)
len = input.int(20, "Length", minval=1)
mult = input.float(2.0, "StdDev")
basis = ta.sma(close, len)
dev = mult * ta.stdev(close, len)
plot(((basis + dev) - (basis - dev)) / basis, "Bollinger BandWidth", #2962FF)
`),

  // ------------------------------------------------------------ oscillators
  S("rsi", "Relative Strength Index", "Oscillators", `
//@version=6
indicator("Relative Strength Index", shorttitle="RSI", overlay=false, precision=2)
len = input.int(14, "RSI Length", minval=1)
src = input.source(close, "Source")
maLen = input.int(14, "MA Length", minval=1)
r = ta.rsi(src, len)
plot(r, "RSI", #7E57C2, 2)
plot(ta.sma(r, maLen), "RSI-based MA", #F7B924)
up = hline(70, "Upper Band", #787B86)
hline(50, "Middle Band", color.new(#787B86, 50))
dn = hline(30, "Lower Band", #787B86)
fill(up, dn, color.rgb(126, 87, 194, 90), "Background")
alertcondition(ta.crossover(r, 70), "RSI overbought", "RSI crossed above 70")
alertcondition(ta.crossunder(r, 30), "RSI oversold", "RSI crossed below 30")
`),
  S("macd", "MACD", "Oscillators", `
//@version=6
indicator("MACD", overlay=false)
fastLen = input.int(12, "Fast Length")
slowLen = input.int(26, "Slow Length")
src = input.source(close, "Source")
sigLen = input.int(9, "Signal Smoothing", minval=1, maxval=50)
[macdLine, signalLine, hist] = ta.macd(src, fastLen, slowLen, sigLen)
plot(hist, "Histogram", hist >= 0 ? (hist[1] < hist ? #26A69A : #B2DFDB) : (hist[1] < hist ? #FFCDD2 : #FF5252), style=plot.style_columns)
plot(macdLine, "MACD", #2962FF, 2)
plot(signalLine, "Signal", #FF6D00, 2)
hline(0, "Zero Line", color.new(#787B86, 50))
alertcondition(ta.crossover(macdLine, signalLine), "MACD bullish cross", "MACD crossed above signal")
alertcondition(ta.crossunder(macdLine, signalLine), "MACD bearish cross", "MACD crossed below signal")
`),
  S("stoch", "Stochastic", "Oscillators", `
//@version=6
indicator("Stochastic", shorttitle="Stoch", overlay=false, precision=2)
periodK = input.int(14, "%K Length", minval=1)
smoothK = input.int(1, "%K Smoothing", minval=1)
periodD = input.int(3, "%D Smoothing", minval=1)
k = ta.sma(ta.stoch(close, high, low, periodK), smoothK)
d = ta.sma(k, periodD)
plot(k, "%K", #2962FF, 2)
plot(d, "%D", #FF6D00)
h0 = hline(80, "Upper Band", #787B86)
hline(50, "Middle Band", color.new(#787B86, 50))
h1 = hline(20, "Lower Band", #787B86)
fill(h0, h1, color.rgb(33, 150, 243, 90), "Background")
`),
  S("stochrsi", "Stochastic RSI", "Oscillators", `
//@version=6
indicator("Stochastic RSI", shorttitle="Stoch RSI", overlay=false, precision=2)
smoothK = input.int(3, "K", minval=1)
smoothD = input.int(3, "D", minval=1)
lengthRSI = input.int(14, "RSI Length", minval=1)
lengthStoch = input.int(14, "Stochastic Length", minval=1)
src = input.source(close, "RSI Source")
rsi1 = ta.rsi(src, lengthRSI)
k = ta.sma(ta.stoch(rsi1, rsi1, rsi1, lengthStoch), smoothK)
d = ta.sma(k, smoothD)
plot(k, "K", #2962FF, 2)
plot(d, "D", #FF6D00)
h0 = hline(80, "Upper Band", #787B86)
h1 = hline(20, "Lower Band", #787B86)
fill(h0, h1, color.rgb(33, 150, 243, 90), "Background")
`),
  S("cci", "Commodity Channel Index", "Oscillators", `
//@version=6
indicator("Commodity Channel Index", shorttitle="CCI", overlay=false, precision=2)
len = input.int(20, "Length", minval=1)
src = input.source(hlc3, "Source")
ma = ta.sma(src, len)
cci = (src - ma) / (0.015 * ta.dev(src, len))
plot(cci, "CCI", #2962FF, 2)
b1 = hline(100, "Upper Band", #787B86)
hline(0, "Middle Band", color.new(#787B86, 50))
b0 = hline(-100, "Lower Band", #787B86)
fill(b1, b0, color.rgb(33, 150, 243, 90), "Background")
`),
  S("dmi", "Directional Movement Index", "Oscillators", `
//@version=6
indicator("Directional Movement Index", shorttitle="DMI", overlay=false, precision=2)
lensig = input.int(14, "ADX Smoothing", minval=1, maxval=50)
len = input.int(14, "DI Length", minval=1)
[diplus, diminus, adx] = ta.dmi(len, lensig)
plot(adx, "ADX", #F50057, 2)
plot(diplus, "+DI", #2962FF)
plot(diminus, "-DI", #FF6D00)
`),
  S("wpr", "Williams %R", "Oscillators", `
//@version=6
indicator("Williams Percent Range", shorttitle="Williams %R", overlay=false, precision=2)
len = input.int(14, "Length", minval=1)
upper = ta.highest(len)
lower = ta.lowest(len)
out = 100 * (close - upper) / (upper - lower)
plot(out, "%R", #7E57C2, 2)
b1 = hline(-20, "Upper Band", #787B86)
hline(-50, "Middle Level", color.new(#787B86, 50))
b0 = hline(-80, "Lower Band", #787B86)
fill(b1, b0, color.rgb(126, 87, 194, 90), "Background")
`),
  S("mom", "Momentum", "Oscillators", `
//@version=6
indicator("Momentum", shorttitle="Mom", overlay=false)
len = input.int(10, "Length", minval=1)
src = input.source(close, "Source")
plot(src - src[len], "Momentum", #2962FF, 2)
hline(0, "Zero", color.new(#787B86, 50))
`),
  S("roc", "Rate Of Change", "Oscillators", `
//@version=6
indicator("Rate Of Change", shorttitle="ROC", overlay=false, precision=2)
len = input.int(9, "Length", minval=1)
src = input.source(close, "Source")
plot(100 * (src - src[len]) / src[len], "ROC", #2962FF, 2)
hline(0, "Zero Line", color.new(#787B86, 50))
`),
  S("ao", "Awesome Oscillator", "Oscillators", `
//@version=6
indicator("Awesome Oscillator", shorttitle="AO", overlay=false)
ao = ta.sma(hl2, 5) - ta.sma(hl2, 34)
plot(ao, "AO", ao >= ao[1] ? #26A69A : #EF5350, style=plot.style_columns)
`),
  S("tsi", "True Strength Index", "Oscillators", `
//@version=6
indicator("True Strength Index", shorttitle="TSI", overlay=false, precision=4)
longLen = input.int(25, "Long Length", minval=1)
shortLen = input.int(13, "Short Length", minval=1)
sigLen = input.int(13, "Signal Length", minval=1)
pc = ta.change(close)
dsPc = ta.ema(ta.ema(pc, longLen), shortLen)
dsAbs = ta.ema(ta.ema(math.abs(pc), longLen), shortLen)
tsi = 100 * dsPc / dsAbs
plot(tsi, "True Strength Index", #2962FF, 2)
plot(ta.ema(tsi, sigLen), "Signal", #E91E63)
hline(0, "Zero", color.new(#787B86, 50))
`),
  S("aroon", "Aroon", "Oscillators", `
//@version=6
indicator("Aroon", overlay=false, precision=2)
len = input.int(14, "Length", minval=1)
upper = 100 * (ta.highestbars(high, len + 1) + len) / len
lower = 100 * (ta.lowestbars(low, len + 1) + len) / len
plot(upper, "Aroon Up", #FF6D00, 2)
plot(lower, "Aroon Down", #2962FF, 2)
`),

  // ----------------------------------------------------------------- volume
  S("volume", "Volume", "Volume", `
//@version=6
indicator("Volume", shorttitle="Vol", overlay=false, format=format.volume)
maLen = input.int(20, "MA Length", minval=1)
plot(volume, "Volume", close >= open ? color.new(#26A69A, 40) : color.new(#EF5350, 40), style=plot.style_columns)
plot(ta.sma(volume, maLen), "Volume MA", #2962FF)
`),
  S("vwap", "VWAP", "Volume", `
//@version=6
indicator("Volume Weighted Average Price", shorttitle="VWAP", overlay=true)
src = input.source(hlc3, "Source")
plot(ta.vwap(src), "VWAP", #2962FF, 2)
`),
  S("obv", "On Balance Volume", "Volume", `
//@version=6
indicator("On Balance Volume", shorttitle="OBV", overlay=false, format=format.volume)
obv = ta.cum(math.sign(ta.change(close)) * volume)
plot(obv, "OBV", #2962FF, 2)
`),
  S("mfi", "Money Flow Index", "Volume", `
//@version=6
indicator("Money Flow Index", shorttitle="MFI", overlay=false, precision=2)
len = input.int(14, "Length", minval=1)
mf = ta.mfi(hlc3, len)
plot(mf, "MF", #7E57C2, 2)
o = hline(80, "Overbought", #787B86)
hline(50, "Middle Band", color.new(#787B86, 50))
s = hline(20, "Oversold", #787B86)
fill(o, s, color.rgb(126, 87, 194, 90), "Background")
`),
  S("cmf", "Chaikin Money Flow", "Volume", `
//@version=6
indicator("Chaikin Money Flow", shorttitle="CMF", overlay=false, precision=4)
len = input.int(20, "Length", minval=1)
ad = close == high and close == low or high == low ? 0 : ((2 * close - low - high) / (high - low)) * volume
mf = math.sum(ad, len) / math.sum(volume, len)
plot(mf, "CMF", mf >= 0 ? #26A69A : #EF5350, 2)
hline(0, "Zero", color.new(#787B86, 50))
`),

  // ------------------------------------------------------------- strategies
  S("strat_ma_cross", "MA Cross Strategy", "Strategies", `
//@version=6
strategy("MA Cross Strategy", overlay=true, initial_capital=10000, default_qty_type=strategy.percent_of_equity, default_qty_value=10, commission_type=strategy.commission.percent, commission_value=0.01)
fastLen = input.int(9, "Fast EMA", minval=1)
slowLen = input.int(21, "Slow EMA", minval=1)
fast = ta.ema(close, fastLen)
slow = ta.ema(close, slowLen)
plot(fast, "Fast", #2962FF, 2)
plot(slow, "Slow", #FF6D00, 2)
if ta.crossover(fast, slow)
    strategy.entry("Long", strategy.long)
if ta.crossunder(fast, slow)
    strategy.entry("Short", strategy.short)
`),
  S("strat_rsi", "RSI Reversal Strategy", "Strategies", `
//@version=6
strategy("RSI Reversal Strategy", overlay=true, initial_capital=10000, default_qty_type=strategy.percent_of_equity, default_qty_value=10)
len = input.int(14, "RSI Length", minval=1)
ob = input.int(70, "Overbought")
os = input.int(30, "Oversold")
r = ta.rsi(close, len)
if ta.crossover(r, os)
    strategy.entry("Long", strategy.long)
if ta.crossunder(r, ob)
    strategy.close("Long")
`),
  S("strat_bb", "Bollinger Breakout Strategy", "Strategies", `
//@version=6
strategy("Bollinger Breakout Strategy", overlay=true, initial_capital=10000, default_qty_type=strategy.percent_of_equity, default_qty_value=10)
len = input.int(20, "Length", minval=1)
mult = input.float(2.0, "StdDev")
basis = ta.sma(close, len)
dev = mult * ta.stdev(close, len)
upper = basis + dev
lower = basis - dev
plot(upper, "Upper", #2962FF)
plot(basis, "Basis", #FF6D00)
plot(lower, "Lower", #2962FF)
if ta.crossover(close, upper)
    strategy.entry("Long", strategy.long)
if ta.crossunder(close, basis)
    strategy.close("Long")
`),
];

export const BUILTIN_BY_ID = Object.fromEntries(BUILTIN_INDICATORS.map((b) => [b.id, b]));

export const NEW_INDICATOR_TEMPLATE = `//@version=6
indicator("My script", overlay=true)
length = input.int(20, "Length", minval=1)
src = input.source(close, "Source")
ma = ta.sma(src, length)
plot(ma, "MA", color.blue, 2)
plotshape(ta.crossover(close, ma), "Cross", shape.triangleup, location.belowbar, color.green)
`;

export const NEW_STRATEGY_TEMPLATE = `//@version=6
strategy("My strategy", overlay=true, initial_capital=10000, default_qty_type=strategy.percent_of_equity, default_qty_value=10)
fast = ta.sma(close, 14)
slow = ta.sma(close, 28)
plot(fast, "Fast", color.blue)
plot(slow, "Slow", color.orange)
if ta.crossover(fast, slow)
    strategy.entry("Long", strategy.long)
if ta.crossunder(fast, slow)
    strategy.close("Long")
`;
