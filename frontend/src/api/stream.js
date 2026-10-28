// Shared WebSocket client for /ws/market.
//
// One socket serves every chart pane and the watchlist. Candle subscriptions are
// reference-counted by (provider, symbol, timeframe): the first listener sends
// "subscribe", the last one to leave sends "unsubscribe". After a reconnect every
// active subscription is re-sent automatically.

const keyOf = (provider, symbol, tf) => `${provider}|${symbol}|${tf}`.toLowerCase();
const feedOf = (provider, symbol) => `${provider}|${symbol}`.toLowerCase();

class StreamClient {
  constructor() {
    this.ws = null;
    this.retry = 0;
    this.state = "idle"; // idle | connecting | open | closed
    this.subs = new Map(); // key -> { msg, handlers:Set, errorHandlers:Set }
    this.tickHandlers = new Map(); // feed -> Set
    this.statusHandlers = new Set();
    this.connHandlers = new Set();
    this.errorHandlers = new Set();
    this.eventHandlers = new Map(); // message type -> Set (alert, alerts_changed, journal_changed...)
    this.reconnectTimer = null;
  }

  url() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    return `${proto}://${location.host}/ws/market`;
  }

  connect() {
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) return;
    this._setState("connecting");
    let ws;
    try {
      ws = new WebSocket(this.url());
    } catch {
      this._scheduleReconnect();
      return;
    }
    this.ws = ws;
    ws.onopen = () => {
      this.retry = 0;
      this._setState("open");
      for (const sub of this.subs.values()) ws.send(JSON.stringify(sub.msg));
    };
    ws.onmessage = (ev) => {
      let m;
      try {
        m = JSON.parse(ev.data);
      } catch {
        return;
      }
      this._dispatch(m);
    };
    ws.onclose = () => {
      this._setState("closed");
      this._scheduleReconnect();
    };
    ws.onerror = () => {};
  }

  _scheduleReconnect() {
    clearTimeout(this.reconnectTimer);
    const delay = Math.min(1000 * 2 ** this.retry, 15000);
    this.retry += 1;
    this.reconnectTimer = setTimeout(() => this.connect(), delay);
  }

  _setState(state) {
    this.state = state;
    for (const h of this.connHandlers) h(state);
  }

  _send(msg) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(msg));
  }

  _dispatch(m) {
    switch (m.type) {
      case "candle": {
        const sub = this.subs.get(keyOf(m.provider, m.symbol, m.timeframe));
        if (sub) for (const h of sub.handlers) h(m.data);
        break;
      }
      case "tick": {
        const set = this.tickHandlers.get(feedOf(m.data.provider, m.data.symbol));
        if (set) for (const h of set) h(m.data);
        break;
      }
      case "status":
        for (const h of this.statusHandlers) h(m.data);
        break;
      case "welcome":
        for (const p of m.providers || []) for (const h of this.statusHandlers) h(p);
        break;
      case "error": {
        const req = m.request || {};
        if (req.action === "subscribe") {
          const sub = this.subs.get(keyOf(req.provider, req.symbol, req.timeframe));
          if (sub) for (const h of sub.errorHandlers) h(m.message);
        }
        for (const h of this.errorHandlers) h(m.message, req);
        break;
      }
      default: {
        const set = this.eventHandlers.get(m.type);
        if (set) for (const h of set) h(m.data);
        break;
      }
    }
  }

  /** Listen to broadcast messages such as "alert" or "alerts_changed". */
  on(type, handler) {
    if (!this.eventHandlers.has(type)) this.eventHandlers.set(type, new Set());
    this.eventHandlers.get(type).add(handler);
    return () => this.eventHandlers.get(type)?.delete(handler);
  }

  /** Listen to live candles. Returns an unsubscribe function. */
  subscribeCandles(provider, symbol, timeframe, onCandle, onError) {
    this.connect();
    const key = keyOf(provider, symbol, timeframe);
    let sub = this.subs.get(key);
    if (!sub) {
      sub = { msg: { action: "subscribe", provider, symbol, timeframe }, handlers: new Set(), errorHandlers: new Set() };
      this.subs.set(key, sub);
      this._send(sub.msg);
    }
    sub.handlers.add(onCandle);
    if (onError) sub.errorHandlers.add(onError);
    return () => {
      const s = this.subs.get(key);
      if (!s) return;
      s.handlers.delete(onCandle);
      if (onError) s.errorHandlers.delete(onError);
      if (s.handlers.size === 0) {
        this.subs.delete(key);
        this._send({ action: "unsubscribe", provider, symbol, timeframe });
      }
    };
  }

  /** Listen to raw ticks of a symbol (only flows while a candle subscription exists). */
  onTick(provider, symbol, handler) {
    const feed = feedOf(provider, symbol);
    if (!this.tickHandlers.has(feed)) this.tickHandlers.set(feed, new Set());
    this.tickHandlers.get(feed).add(handler);
    return () => this.tickHandlers.get(feed)?.delete(handler);
  }

  onStatus(handler) {
    this.statusHandlers.add(handler);
    return () => this.statusHandlers.delete(handler);
  }

  onConnection(handler) {
    this.connHandlers.add(handler);
    handler(this.state);
    return () => this.connHandlers.delete(handler);
  }

  onError(handler) {
    this.errorHandlers.add(handler);
    return () => this.errorHandlers.delete(handler);
  }
}

export const stream = new StreamClient();
