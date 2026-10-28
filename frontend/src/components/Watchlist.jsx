// Right sidebar: watchlist with live prices + details of the active symbol.
import React, { useEffect, useState } from "react";
import { getHistory } from "../api/client.js";
import { stream } from "../api/stream.js";
import { useStore } from "../store/useStore.js";
import { Icon } from "./Icons.jsx";

function WatchRow({ item, index, active, onPick }) {
  const [last, setLast] = useState(null);
  const [prevClose, setPrevClose] = useState(null);
  const [day, setDay] = useState(null);
  const [flash, setFlash] = useState("");
  const [err, setErr] = useState(null);

  useEffect(() => {
    let alive = true;
    getHistory(item.provider, item.symbol, "D1", 2)
      .then((bars) => {
        if (!alive || !bars.length) return;
        const lastBar = bars[bars.length - 1];
        setDay(lastBar);
        setLast(lastBar.close);
        setPrevClose(bars.length > 1 ? bars[bars.length - 2].close : lastBar.open);
        setErr(null);
      })
      .catch((e) => alive && setErr(e.message));
    const off = stream.subscribeCandles(
      item.provider,
      item.symbol,
      "D1",
      (c) => {
        setDay(c);
        setLast((prev) => {
          if (prev !== null && c.close !== prev) {
            setFlash(c.close > prev ? "flash-up" : "flash-down");
            setTimeout(() => setFlash(""), 400);
          }
          return c.close;
        });
      },
      (m) => setErr(m),
    );
    return () => {
      alive = false;
      off();
    };
  }, [item.provider, item.symbol]);

  const chg = last !== null && prevClose ? last - prevClose : null;
  const pct = chg !== null && prevClose ? (chg / prevClose) * 100 : null;
  const s = useStore.getState();
  const digits = last !== null ? (Math.abs(last) >= 1000 ? 2 : Math.abs(last) >= 10 ? 3 : 5) : 5;
  return (
    <div
      className={`watch-row ${active ? "active" : ""}`}
      onClick={() => onPick(item)}
      draggable
      onDragStart={(e) => e.dataTransfer.setData("text/plain", String(index))}
      onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => {
        const from = Number(e.dataTransfer.getData("text/plain"));
        if (!Number.isNaN(from)) s.moveWatchlistItem(from, index);
      }}
      title={err || `${item.provider}:${item.symbol}`}
    >
      <span className="w-sym">
        {item.symbol}
        <small>{item.provider}</small>
      </span>
      <span className={`w-last ${flash}`}>{last !== null ? last.toFixed(digits) : err ? "⚠" : "…"}</span>
      <span className={`w-chg ${chg === null ? "" : chg >= 0 ? "up" : "down"}`}>{chg !== null ? chg.toFixed(digits) : ""}</span>
      <span className={`w-pct ${pct === null ? "" : pct >= 0 ? "up" : "down"}`}>{pct !== null ? `${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%` : ""}</span>
      <button
        className="w-del"
        title="Remove"
        onClick={(e) => {
          e.stopPropagation();
          s.removeFromWatchlist(item.provider, item.symbol);
        }}
      >
        ×
      </button>
      {active && day && (
        <div className="w-details" onClick={(e) => e.stopPropagation()}>
          <span>Day range</span>
          <b>
            {day.low.toFixed(digits)} — {day.high.toFixed(digits)}
          </b>
          <span>Open</span>
          <b>{day.open.toFixed(digits)}</b>
          <span>Prev close</span>
          <b>{prevClose !== null ? prevClose.toFixed(digits) : "—"}</b>
        </div>
      )}
    </div>
  );
}

export default function Watchlist() {
  const watchlist = useStore((s) => s.watchlist);
  const pane = useStore((s) => s.panes.find((p) => p.id === s.activePaneId) || s.panes[0]);
  const s = useStore.getState();
  const pick = (item) => s.updatePane(pane.id, { provider: item.provider, symbol: item.symbol, digits: undefined });

  return (
    <div className="watchlist">
      <div className="rp-subhead">
        <span className="rp-title">Watchlist</span>
        <span className="flex1" />
        <button className="icon-btn" title="Add symbol" onClick={() => s.openDialog({ type: "symbol", paneId: pane.id, mode: "watchlist" })}>
          <Icon.plus />
        </button>
        {pane && pane.provider && (
          <button className="icon-btn" title={`Add ${pane.symbol}`} onClick={() => s.addToWatchlist(pane.provider, pane.symbol)}>
            <Icon.star />
          </button>
        )}
      </div>
      <div className="watch-head">
        <span>Symbol</span>
        <span>Last</span>
        <span>Chg</span>
        <span>Chg%</span>
      </div>
      <div className="watch-list">
        {watchlist.map((w, i) => (
          <WatchRow key={`${w.provider}:${w.symbol}`} item={w} index={i} active={pane && pane.provider === w.provider && pane.symbol === w.symbol} onPick={pick} />
        ))}
        {!watchlist.length && <div className="empty">Your watchlist is empty. Click + to add symbols.</div>}
      </div>
    </div>
  );
}
