// Symbol search dialog: provider tabs + live search against /api/symbols.
import React, { useEffect, useMemo, useRef, useState } from "react";
import { getSymbols } from "../../api/client.js";
import { useStore } from "../../store/useStore.js";
import { Modal } from "../ui.jsx";
import { Icon } from "../Icons.jsx";
import { BrokerLoginButton } from "../../broker/BrokerLogin.jsx";

export default function SymbolSearch({ dialog }) {
  const providers = useStore((s) => s.providers);
  const panes = useStore((s) => s.panes);
  const watchlist = useStore((s) => s.watchlist);
  const pane = panes.find((p) => p.id === dialog.paneId) || panes[0];
  const forWatchlist = dialog.mode === "watchlist";
  const [provider, setProvider] = useState(pane?.provider || (providers.find((p) => p.connected) || {}).name || "simulated");
  const [query, setQuery] = useState(dialog.initialQuery || "");
  const [items, setItems] = useState([]);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  const [cursor, setCursor] = useState(0);
  const inputRef = useRef(null);
  const listRef = useRef(null);
  const s = useStore.getState();

  useEffect(() => inputRef.current && inputRef.current.focus(), []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    const t = setTimeout(async () => {
      try {
        const res = await getSymbols(provider, query, 300);
        if (!cancelled) {
          setItems(res);
          setError(null);
          setCursor(0);
        }
      } catch (err) {
        if (!cancelled) {
          setItems([]);
          setError(err.message);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }, 150);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [provider, query]);

  const inWatchlist = useMemo(() => new Set(watchlist.map((w) => `${w.provider}|${w.symbol}`)), [watchlist]);

  const choose = (item) => {
    if (forWatchlist) {
      s.addToWatchlist(item.provider, item.symbol);
      return;
    }
    s.updatePane(pane.id, { provider: item.provider, symbol: item.symbol, digits: item.digits });
    s.closeDialog();
  };

  const onKey = (e) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setCursor((c) => Math.min(items.length - 1, c + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setCursor((c) => Math.max(0, c - 1));
    } else if (e.key === "Enter" && items[cursor]) {
      e.preventDefault();
      choose(items[cursor]);
    }
  };

  useEffect(() => {
    const el = listRef.current && listRef.current.children[cursor];
    if (el) el.scrollIntoView({ block: "nearest" });
  }, [cursor]);

  return (
    <Modal title={forWatchlist ? "Add symbol to watchlist" : "Symbol search"} onClose={() => s.closeDialog()} width={640} className="symbol-dialog">
      <div className="search-box">
        <Icon.search />
        <input ref={inputRef} value={query} onChange={(e) => setQuery(e.target.value)} onKeyDown={onKey} placeholder={provider === "upstox" ? "Search e.g. RELIANCE, NIFTY, BSE:SENSEX, CRUDEOIL" : "Search symbol or description"} spellCheck={false} />
      </div>
      <div className="provider-tabs">
        {providers.map((p) => (
          <button key={p.name} className={`chip ${provider === p.name ? "active" : ""}`} onClick={() => setProvider(p.name)} title={p.message}>
            <span className={`dot ${p.connected ? "ok" : "bad"}`} />
            {p.name}
            {p.details && p.details.active_source ? ` → ${p.details.active_source}` : ""}
          </button>
        ))}
      </div>
      <div className="symbol-list" ref={listRef}>
        {loading && !items.length && <div className="empty">Searching…</div>}
        {error && provider === "upstox" ? (
          <div className="broker-hint">
            <span className="error">{error}</span>
            <BrokerLoginButton />
          </div>
        ) : (
          error && <div className="empty error">{error}</div>
        )}
        {!loading && !error && !items.length && <div className="empty">No symbols found</div>}
        {items.map((it, i) => {
          const starred = inWatchlist.has(`${it.provider}|${it.symbol}`);
          return (
            <div key={`${it.provider}:${it.symbol}`} className={`symbol-row ${i === cursor ? "cursor" : ""}`} onMouseEnter={() => setCursor(i)} onClick={() => choose(it)}>
              <span className="sym">{it.symbol}</span>
              <span className="desc">{it.description}</span>
              <span className="cat">{it.category}</span>
              <button
                className={`star ${starred ? "on" : ""}`}
                title={starred ? "In watchlist" : "Add to watchlist"}
                onClick={(e) => {
                  e.stopPropagation();
                  if (starred) s.removeFromWatchlist(it.provider, it.symbol);
                  else s.addToWatchlist(it.provider, it.symbol);
                }}
              >
                ★
              </button>
            </div>
          );
        })}
      </div>
    </Modal>
  );
}
