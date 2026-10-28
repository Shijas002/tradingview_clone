// Trade journal (bottom panel tab, Phase 5): stats, filters, trade list, Notion sync status.
import React, { useEffect, useMemo, useState } from "react";
import { deleteJournal, retryNotion, syncJournal } from "../api/client.js";
import { lastPrice } from "../chart/registry.js";
import { fmtMoney, formatEntryTime, loadJournal } from "../journal/journal.js";
import { useStore } from "../store/useStore.js";
import { TF } from "../utils/market.js";
import { Icon } from "./Icons.jsx";

function NotionChip({ notion }) {
  const s = useStore.getState();
  if (!notion) return null;
  if (!notion.configured) {
    return (
      <span className="notion-chip off" title="Add NOTION_API_KEY and NOTION_DATABASE_ID to backend/.env to copy every trade into Notion (TUTORIAL.md, Step 16)">
        <i className="dot" /> Notion off
      </span>
    );
  }
  const retry = async () => {
    try {
      const r = await retryNotion();
      s.toast(r.retried ? `Retrying ${r.retried} entr${r.retried === 1 ? "y" : "ies"}` : "Reconnecting to Notion…", "ok", 2000);
      setTimeout(loadJournal, 1500);
    } catch (err) {
      s.toast(err.message);
    }
  };
  return (
    <span className={`notion-chip ${notion.connected ? "ok" : "bad"}`} title={notion.message}>
      <i className={`dot ${notion.connected ? "ok" : "bad"}`} />
      {notion.connected ? (
        notion.database_url ? (
          <a href={notion.database_url} target="_blank" rel="noreferrer">
            Notion · {notion.database_title}
          </a>
        ) : (
          `Notion · ${notion.database_title}`
        )
      ) : (
        "Notion offline"
      )}
      {notion.pending > 0 && <span className="muted"> · {notion.pending} syncing</span>}
      {notion.errors > 0 && <span className="down"> · {notion.errors} failed</span>}
      {(notion.errors > 0 || !notion.connected) && (
        <button className="btn tiny" onClick={retry}>
          Retry
        </button>
      )}
    </span>
  );
}

function SyncIcon({ e }) {
  const s = useStore.getState();
  if (e.notion_status === "synced")
    return e.notion_url ? (
      <a className="sync-ic ok" href={e.notion_url} target="_blank" rel="noreferrer" title="Open in Notion">
        N✓
      </a>
    ) : (
      <span className="sync-ic ok">N✓</span>
    );
  if (e.notion_status === "pending") return <span className="sync-ic muted" title="Waiting to sync to Notion">⏳</span>;
  if (e.notion_status === "error")
    return (
      <button
        className="sync-ic bad"
        title={`Notion sync failed: ${e.notion_error}\nClick to retry`}
        onClick={async () => {
          try {
            await syncJournal(e.id);
            loadJournal();
          } catch (err) {
            s.toast(err.message);
          }
        }}
      >
        ⚠
      </button>
    );
  return <span className="sync-ic muted" title="Saved locally only">•</span>;
}

export default function JournalPanel() {
  const journal = useStore((s) => s.journal);
  const stats = useStore((s) => s.journalStats);
  const notion = useStore((s) => s.notion);
  const pane = useStore((s) => s.panes.find((p) => p.id === s.activePaneId) || s.panes[0]);
  useStore((s) => s.providers); // re-render times when provider offsets change
  useStore((s) => s.settings.timezone);
  const [status, setStatus] = useState("all");
  const [onlySymbol, setOnlySymbol] = useState(false);
  const [query, setQuery] = useState("");
  const s = useStore.getState();

  useEffect(() => {
    loadJournal();
  }, []);

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return journal.filter(
      (e) =>
        (status === "all" || e.status === status) &&
        (!onlySymbol || (pane && e.symbol === pane.symbol)) &&
        (!q || e.symbol.toLowerCase().includes(q) || (e.setup || "").toLowerCase().includes(q) || (e.tags || []).some((t) => t.toLowerCase().includes(q)) || (e.notes || "").toLowerCase().includes(q)),
    );
  }, [journal, status, onlySymbol, query, pane]);

  const newTrade = () => pane && s.openDialog({ type: "journal", paneId: pane.id });
  const edit = (e) => s.openDialog({ type: "journal", paneId: pane?.id, entry: e });
  const closeTrade = (e) => {
    const onChart = s.panes.find((p) => p.symbol === e.symbol && p.provider === e.provider);
    const px = onChart ? lastPrice(onChart.id) : null;
    s.openDialog({ type: "journal", paneId: onChart ? onChart.id : pane?.id, entry: e, close: { status: "closed", exit_price: px ?? e.exit_price, exit_time: Math.floor(Date.now() / 1000) } });
  };
  const remove = async (e) => {
    if (!window.confirm(`Delete the ${e.side} ${e.symbol} trade from the journal${e.notion_url ? " (and move its Notion page to the trash)" : ""}?`)) return;
    try {
      await deleteJournal(e.id);
      await loadJournal();
    } catch (err) {
      s.toast(err.message);
    }
  };
  const showOnChart = (e) => {
    if (!pane || !e.provider) return;
    s.updatePane(pane.id, { provider: e.provider, symbol: e.symbol, ...(e.timeframe ? { timeframe: e.timeframe } : {}), digits: undefined });
  };

  return (
    <div className="journal-panel">
      <div className="journal-head">
        <div className="journal-stats">
          <span>
            <b>{stats ? stats.trades : 0}</b> trades
          </span>
          <span>
            Win rate <b>{stats && stats.win_rate !== null ? `${stats.win_rate.toFixed(1)}%` : "—"}</b>
          </span>
          <span>
            Net P&amp;L <b className={stats && stats.net_pnl >= 0 ? "up" : "down"}>{stats ? fmtMoney(stats.net_pnl) : "—"}</b>
          </span>
          <span>
            Profit factor <b>{stats && stats.profit_factor !== null ? stats.profit_factor.toFixed(2) : "—"}</b>
          </span>
          <span>
            Avg R <b>{stats && stats.avg_r !== null ? stats.avg_r.toFixed(2) : "—"}</b>
          </span>
          <span>
            Open <b>{stats ? stats.open : 0}</b>
          </span>
        </div>
        <span className="flex1" />
        <NotionChip notion={notion} />
      </div>
      <div className="journal-toolbar">
        <span className="seg">
          {["all", "open", "closed"].map((v) => (
            <button key={v} className={status === v ? "active" : ""} onClick={() => setStatus(v)}>
              {v[0].toUpperCase() + v.slice(1)}
            </button>
          ))}
        </span>
        <label className="chk">
          <input type="checkbox" checked={onlySymbol} onChange={(e) => setOnlySymbol(e.target.checked)} /> {pane ? pane.symbol : "This symbol"} only
        </label>
        <input className="journal-search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search symbol, setup, tag, note…" />
        <span className="flex1" />
        <a className="btn small" href="/api/journal/export.csv" title="Download all trades as CSV (Excel)">
          CSV
        </a>
        <button className="btn small primary" onClick={newTrade} title="Log a trade on the active chart (Alt+J)">
          <Icon.plus /> Log trade
        </button>
      </div>
      <div className="journal-table-wrap">
        <table className="journal-table">
          <thead>
            <tr>
              <th>Entry time</th>
              <th>Symbol</th>
              <th>Side</th>
              <th>Entry</th>
              <th>Exit</th>
              <th>Qty</th>
              <th>P&amp;L</th>
              <th>R</th>
              <th>Setup / tags</th>
              <th title="Chart screenshot">📷</th>
              <th title="Notion">N</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.map((e) => (
              <tr key={e.id} className={e.status === "open" ? "is-open" : ""} onDoubleClick={() => edit(e)}>
                <td className="nowrap">{formatEntryTime(e.entry_time, e.provider)}</td>
                <td>
                  <button className="linklike" onClick={() => showOnChart(e)} title={`Show ${e.symbol} on the active chart`}>
                    {e.symbol}
                  </button>
                  {e.timeframe && <span className="muted"> {TF[e.timeframe] ? TF[e.timeframe].l : e.timeframe}</span>}
                </td>
                <td className={e.side === "long" ? "up" : "down"}>{e.side === "long" ? "Long" : "Short"}</td>
                <td>{e.entry_price}</td>
                <td>{e.status === "closed" ? e.exit_price : <span className="badge">OPEN</span>}</td>
                <td>{e.quantity}</td>
                <td className={e.pnl === null ? "" : e.pnl >= 0 ? "up" : "down"}>{fmtMoney(e.pnl)}</td>
                <td className={e.r_multiple === null ? "" : e.r_multiple >= 0 ? "up" : "down"}>{e.r_multiple === null ? "—" : e.r_multiple.toFixed(2)}</td>
                <td className="journal-tags">
                  {e.setup && <span className="tag setup">{e.setup}</span>}
                  {(e.tags || []).map((t) => (
                    <span key={t} className="tag">
                      {t}
                    </span>
                  ))}
                  {e.rating ? <span className="stars">{"★".repeat(e.rating)}</span> : null}
                </td>
                <td>
                  {e.has_screenshot ? (
                    <a href={`/api/journal/${e.id}/screenshot?v=${e.screenshot_version}`} target="_blank" rel="noreferrer" title="Open screenshot">
                      🖼
                    </a>
                  ) : (
                    ""
                  )}
                </td>
                <td>
                  <SyncIcon e={e} />
                </td>
                <td className="journal-actions">
                  {e.status === "open" && (
                    <button className="btn tiny" onClick={() => closeTrade(e)} title="Close this trade (exit at the current price)">
                      Close
                    </button>
                  )}
                  <button className="icon-btn" onClick={() => edit(e)} title="Edit">
                    ✎
                  </button>
                  <button className="icon-btn danger" onClick={() => remove(e)} title="Delete">
                    <Icon.trash />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!rows.length && (
          <div className="empty">
            {journal.length
              ? "No trades match the filter."
              : "No trades yet. Click “Log trade”, press Alt+J, use 📓 on a Long/Short position drawing, or log a backtest trade from the Strategy Tester."}
          </div>
        )}
      </div>
    </div>
  );
}
