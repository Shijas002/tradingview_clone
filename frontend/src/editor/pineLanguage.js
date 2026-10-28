// Pine Script syntax highlighting + autocompletion for CodeMirror 6.
import { StreamLanguage, HighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { tags as t } from "@lezer/highlight";

const KEYWORDS = new Set(["if", "else", "for", "to", "by", "while", "switch", "var", "varip", "import", "export", "method", "type", "enum", "and", "or", "not", "in", "break", "continue", "return", "true", "false", "na", "series", "simple", "const", "input"]);
const TYPES = new Set(["int", "float", "bool", "string", "color", "label", "line", "box", "table", "array", "matrix", "map", "linefill", "polyline", "chart"]);
const NAMESPACES = new Set(["ta", "math", "str", "array", "matrix", "map", "request", "strategy", "input", "color", "label", "line", "box", "table", "plot", "shape", "location", "size", "position", "extend", "xloc", "yloc", "format", "display", "barstate", "syminfo", "timeframe", "session", "currency", "alert", "hline", "order", "text", "font", "chart", "dayofweek", "linefill", "polyline", "ticker", "runtime", "log"]);
const BUILTIN_VARS = new Set(["open", "high", "low", "close", "volume", "time", "time_close", "bar_index", "last_bar_index", "hl2", "hlc3", "ohlc4", "hlcc4", "year", "month", "weekofyear", "dayofmonth", "hour", "minute", "second", "timenow"]);
const FUNCS = new Set(["plot", "plotshape", "plotchar", "plotarrow", "plotcandle", "plotbar", "hline", "fill", "bgcolor", "barcolor", "indicator", "strategy", "library", "alertcondition", "alert", "nz", "na", "fixnan", "timestamp", "max_bars_back", "runtime"]);

const pineMode = {
  name: "pine",
  startState: () => ({}),
  token(stream) {
    if (stream.eatSpace()) return null;
    if (stream.match("//")) {
      stream.skipToEnd();
      return "comment";
    }
    if (stream.match(/^"(?:[^"\\]|\\.)*"?/) || stream.match(/^'(?:[^'\\]|\\.)*'?/)) return "string";
    if (stream.match(/^#[0-9a-fA-F]{6,8}\b/)) return "color";
    if (stream.match(/^\d+\.?\d*(e[+-]?\d+)?/i) || stream.match(/^\.\d+/)) return "number";
    if (stream.match(/^(:=|=>|==|!=|<=|>=|\+=|-=|\*=|\/=|%=|[+\-*/%<>=?:])/)) return "operator";
    const m = stream.match(/^[A-Za-z_][A-Za-z0-9_]*/);
    if (m) {
      const w = m[0];
      if (KEYWORDS.has(w)) return "keyword";
      if (TYPES.has(w)) return "typeName";
      if (stream.peek() === ".") {
        if (NAMESPACES.has(w)) return "namespace";
      }
      if (BUILTIN_VARS.has(w)) return "builtin";
      if (FUNCS.has(w) && stream.peek() === "(") return "function";
      if (stream.peek() === "(") return "function";
      return "variable";
    }
    stream.next();
    return null;
  },
  tokenTable: {
    comment: t.comment,
    string: t.string,
    color: t.color,
    number: t.number,
    operator: t.operator,
    keyword: t.keyword,
    typeName: t.typeName,
    namespace: t.namespace,
    builtin: t.standard(t.variableName),
    function: t.function(t.variableName),
    variable: t.variableName,
  },
};

export const pineLanguage = StreamLanguage.define(pineMode);

export const pineHighlight = syntaxHighlighting(
  HighlightStyle.define([
    { tag: t.comment, color: "#6A9955", fontStyle: "italic" },
    { tag: t.string, color: "#CE9178" },
    { tag: t.color, color: "#4EC9B0" },
    { tag: t.number, color: "#B5CEA8" },
    { tag: t.operator, color: "#D4D4D4" },
    { tag: t.keyword, color: "#C586C0" },
    { tag: t.typeName, color: "#4EC9B0" },
    { tag: t.namespace, color: "#4FC1FF" },
    { tag: t.standard(t.variableName), color: "#9CDCFE", fontWeight: "bold" },
    { tag: t.function(t.variableName), color: "#DCDCAA" },
    { tag: t.variableName, color: "#D4D4D4" },
  ]),
);

// ------------------------------------------------------------- completions
const COMPLETIONS = [
  ...["ta.sma(source, length)", "ta.ema(source, length)", "ta.wma(source, length)", "ta.rma(source, length)", "ta.hma(source, length)", "ta.vwma(source, length)", "ta.rsi(source, length)", "ta.macd(source, fast, slow, signal)", "ta.stoch(source, high, low, length)", "ta.atr(length)", "ta.tr(handle_na)", "ta.bb(source, length, mult)", "ta.stdev(source, length)", "ta.dev(source, length)", "ta.highest(source, length)", "ta.lowest(source, length)", "ta.highestbars(source, length)", "ta.lowestbars(source, length)", "ta.crossover(a, b)", "ta.crossunder(a, b)", "ta.cross(a, b)", "ta.change(source)", "ta.mom(source, length)", "ta.roc(source, length)", "ta.cci(source, length)", "ta.mfi(source, length)", "ta.supertrend(factor, atrPeriod)", "ta.sar(start, inc, max)", "ta.dmi(diLength, adxSmoothing)", "ta.vwap(source)", "ta.pivothigh(left, right)", "ta.pivotlow(left, right)", "ta.valuewhen(condition, source, occurrence)", "ta.barssince(condition)", "ta.cum(source)", "ta.linreg(source, length, offset)", "ta.percentrank(source, length)", "ta.correlation(a, b, length)", "ta.median(source, length)", "ta.rising(source, length)", "ta.falling(source, length)", "ta.kc(source, length, mult)", "ta.wpr(length)", "ta.tsi(source, short, long)"].map((l) => ({ label: l.split("(")[0], detail: l, type: "function" })),
  ...["math.abs(x)", "math.max(a, b)", "math.min(a, b)", "math.round(x)", "math.floor(x)", "math.ceil(x)", "math.sqrt(x)", "math.pow(base, exp)", "math.log(x)", "math.exp(x)", "math.avg(a, b)", "math.sum(source, length)", "math.sign(x)"].map((l) => ({ label: l.split("(")[0], detail: l, type: "function" })),
  ...["input.int(defval, title)", "input.float(defval, title)", "input.bool(defval, title)", "input.string(defval, title, options)", "input.source(close, title)", "input.color(defval, title)", "input.timeframe(defval, title)", "input.symbol(defval, title)", "input.session(defval, title)", "input.time(defval, title)", "input.price(defval, title)", "input.text_area(defval, title)"].map((l) => ({ label: l.split("(")[0], detail: l, type: "function" })),
  ...["plot(series, title, color, linewidth, style)", "plotshape(series, title, style, location, color, text)", "plotchar(series, title, char, location, color)", "plotarrow(series, title)", "plotcandle(open, high, low, close, title, color)", "hline(price, title, color, linestyle)", "fill(plot1, plot2, color, title)", "bgcolor(color)", "barcolor(color)", "alertcondition(condition, title, message)", "indicator(title, shorttitle, overlay)", "strategy(title, overlay, initial_capital)", "nz(x, replacement)", "na(x)", "str.tostring(value, format)", "color.new(color, transp)", "color.rgb(r, g, b, transp)", "label.new(x, y, text, style, color, textcolor)", "line.new(x1, y1, x2, y2, color, width)", "box.new(left, top, right, bottom, border_color, bgcolor)", "table.new(position, columns, rows)", "table.cell(table, column, row, text)", "strategy.entry(id, direction, qty)", "strategy.close(id)", "strategy.exit(id, from_entry, stop, limit)", "strategy.close_all()", "request.security(symbol, timeframe, expression)", "array.new_float(size, initial)", "array.push(id, value)", "array.get(id, index)"].map((l) => ({ label: l.split("(")[0], detail: l, type: "function" })),
  ...["open", "high", "low", "close", "volume", "hl2", "hlc3", "ohlc4", "hlcc4", "bar_index", "time", "barstate.islast", "barstate.isconfirmed", "barstate.isnew", "syminfo.ticker", "syminfo.mintick", "timeframe.period", "strategy.long", "strategy.short", "strategy.position_size", "color.red", "color.green", "color.blue", "color.orange", "color.purple", "color.teal", "color.yellow", "color.white", "color.black", "color.gray", "color.lime", "color.aqua", "color.fuchsia", "shape.triangleup", "shape.triangledown", "shape.circle", "shape.cross", "shape.xcross", "shape.labelup", "shape.labeldown", "shape.arrowup", "shape.arrowdown", "shape.square", "shape.diamond", "shape.flag", "location.abovebar", "location.belowbar", "location.top", "location.bottom", "location.absolute", "size.tiny", "size.small", "size.normal", "size.large", "plot.style_line", "plot.style_linebr", "plot.style_histogram", "plot.style_columns", "plot.style_area", "plot.style_circles", "plot.style_cross", "plot.style_stepline", "hline.style_dashed", "hline.style_dotted", "hline.style_solid", "position.top_right", "position.bottom_left", "format.price", "format.volume", "format.percent", "extend.right", "extend.both", "label.style_label_up", "label.style_label_down", "line.style_dashed"].map((l) => ({ label: l, type: "variable" })),
  ...["if", "else", "for", "while", "switch", "var", "varip", "true", "false", "na"].map((l) => ({ label: l, type: "keyword" })),
];

export function pineCompletions(context) {
  const word = context.matchBefore(/[A-Za-z_][A-Za-z0-9_.]*/);
  if (!word || (word.from === word.to && !context.explicit)) return null;
  return { from: word.from, options: COMPLETIONS, validFor: /^[A-Za-z0-9_.]*$/ };
}
