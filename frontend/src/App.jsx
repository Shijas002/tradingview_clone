// Application shell: layout, bootstrapping (providers + stream) and keyboard shortcuts.
import React, { useEffect } from "react";
import { getProviders } from "./api/client.js";
import { stream } from "./api/stream.js";
import { activeController, lastPrice } from "./chart/registry.js";
import { useStore } from "./store/useStore.js";
import TopBar from "./components/TopBar.jsx";
import DrawingToolbar from "./components/DrawingToolbar.jsx";
import ChartGrid from "./components/ChartGrid.jsx";
import BottomPanel from "./components/BottomPanel.jsx";
import RightPanel from "./components/RightPanel.jsx";
import AlertDialog from "./components/dialogs/AlertDialog.jsx";
import { startServerSync } from "./store/serverSync.js";
import { loadAlerts, notifyAlert } from "./alerts/alerts.js";
import { loadBroker } from "./broker/broker.js";
import StatusBar from "./components/StatusBar.jsx";
import ContextMenu from "./components/ContextMenu.jsx";
import SymbolSearch from "./components/dialogs/SymbolSearch.jsx";
import IndicatorsDialog from "./components/dialogs/IndicatorsDialog.jsx";
import IndicatorSettings from "./components/dialogs/IndicatorSettings.jsx";
import ChartSettings from "./components/dialogs/ChartSettings.jsx";
import TextDialog from "./components/dialogs/TextDialog.jsx";
import JournalDialog from "./components/dialogs/JournalDialog.jsx";
import { loadJournal, loadJournalSoon } from "./journal/journal.js";

function pickDefaultProvider(providers) {
  const connected = providers.filter((p) => p.connected);
  return (connected.find((p) => p.name === "auto") || connected.find((p) => p.name === "mt5") || connected[0] || providers[0] || {}).name || null;
}

function useBootstrap() {
  useEffect(() => {
    const s = useStore.getState();
    const offConn = stream.onConnection((st) => useStore.getState().setConnection(st));
    const offStatus = stream.onStatus((p) => {
      const prev = useStore.getState().providers.find((x) => x.name === p.name);
      useStore.getState().upsertProviderStatus(p);
      if (p.name === "upstox" && (!prev || prev.connected !== p.connected || (prev.details || {}).authenticated !== (p.details || {}).authenticated)) loadBroker();
    });
    const offErr = stream.onError((msg, req) => {
      if (req && req.action === "subscribe") return; // shown on the chart itself
      useStore.getState().toast(msg);
    });
    const offAlert = stream.on("alert", (data) => {
      notifyAlert(data);
      loadAlerts();
    });
    const offAlertsChanged = stream.on("alerts_changed", () => loadAlerts());
    const offJournal = stream.on("journal_changed", () => loadJournalSoon());
    const offJournalStatus = stream.on("journal_status", (data) => useStore.getState().setJournal({ notion: data }));
    stream.connect();

    let cancelled = false;
    const load = async (attempt = 0) => {
      try {
        const providers = await getProviders();
        if (cancelled) return;
        s.setProviders(providers);
        const def = pickDefaultProvider(providers);
        const names = new Set(providers.map((p) => p.name));
        const st = useStore.getState();
        for (const pane of st.panes) {
          if (!pane.provider || !names.has(pane.provider)) {
            st.updatePane(pane.id, { provider: def, symbol: def === "simulated" ? "EURUSD" : pane.symbol || "EURUSD", digits: undefined });
          }
        }
        if (!st.activePaneId && st.panes[0]) st.setActivePane(st.panes[0].id);
        if (!st.watchlist.length && def) {
          for (const sym of ["EURUSD", "GBPUSD", "XAUUSD", "BTCUSD"]) st.addToWatchlist(def, sym);
        }
      } catch (err) {
        if (cancelled) return;
        if (attempt === 0) useStore.getState().toast(`Backend not reachable (${err.message}). Is "python run.py" running? Retrying…`, "error", 8000);
        setTimeout(() => load(attempt + 1), Math.min(15000, 2000 * (attempt + 1)));
      }
    };
    // workspace from the database first, then providers (so saved panes keep their provider)
    startServerSync().finally(() => {
      if (!cancelled) load();
    });
    loadAlerts();
    loadBroker();
    loadJournal();
    return () => {
      cancelled = true;
      offJournal();
      offJournalStatus();
      offAlert();
      offAlertsChanged();
      offConn();
      offStatus();
      offErr();
    };
  }, []);
}

function isTyping(e) {
  const t = e.target;
  if (!t) return false;
  const tag = t.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || t.isContentEditable || (t.closest && t.closest(".cm-editor"));
}

const TOOL_KEYS = { t: "trendline", h: "hline", v: "vline", f: "fib", r: "rect", c: "channel" };

function useShortcuts() {
  useEffect(() => {
    const onKey = (e) => {
      const s = useStore.getState();
      if (s.dialog) return;
      if (isTyping(e)) return;
      const ctrl = activeController(s);
      const key = e.key;
      if ((e.ctrlKey || e.metaKey) && key.toLowerCase() === "z") {
        e.preventDefault();
        if (e.shiftKey) s.redoDrawings();
        else s.undoDrawings();
        return;
      }
      if ((e.ctrlKey || e.metaKey) && key.toLowerCase() === "y") {
        e.preventDefault();
        s.redoDrawings();
        return;
      }
      if (key === "Delete" || key === "Backspace") {
        if (ctrl && ctrl.deleteSelectedDrawing()) e.preventDefault();
        return;
      }
      if (key === "Escape") {
        if (s.tool !== "cursor") s.setTool("cursor");
        if (ctrl) ctrl.cancelDrawing();
        s.setContextMenu(null);
        return;
      }
      if (e.altKey && key.toLowerCase() === "a") {
        e.preventDefault();
        const pane = s.activePane();
        s.openDialog({ type: "alert", paneId: pane.id, price: lastPrice(pane.id) });
        return;
      }
      if (e.altKey && key.toLowerCase() === "j") {
        e.preventDefault();
        s.openDialog({ type: "journal", paneId: s.activePane().id });
        return;
      }
      if (e.altKey && TOOL_KEYS[key.toLowerCase()]) {
        e.preventDefault();
        s.setTool(TOOL_KEYS[key.toLowerCase()]);
        return;
      }
      if (key === "/" && !e.ctrlKey && !e.metaKey) {
        e.preventDefault();
        s.openDialog({ type: "indicators", paneId: s.activePane().id });
        return;
      }
      if (e.altKey && key.toLowerCase() === "r") return;
      // typing a letter opens symbol search, like TradingView
      if (!e.ctrlKey && !e.metaKey && !e.altKey && /^[a-zA-Z0-9]$/.test(key)) {
        s.openDialog({ type: "symbol", paneId: s.activePane().id, initialQuery: key.toUpperCase() });
        e.preventDefault();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
}

function Dialogs({ extra }) {
  const dialog = useStore((s) => s.dialog);
  if (!dialog) return null;
  switch (dialog.type) {
    case "symbol":
      return <SymbolSearch dialog={dialog} />;
    case "indicators":
      return <IndicatorsDialog dialog={dialog} />;
    case "indicatorSettings":
      return <IndicatorSettings key={dialog.indicatorId} dialog={dialog} />;
    case "chartSettings":
      return <ChartSettings />;
    case "text":
      return <TextDialog dialog={dialog} />;
    case "alert":
      return <AlertDialog key={dialog.alert ? dialog.alert.id : "new"} dialog={dialog} />;
    case "journal":
      return <JournalDialog key={dialog.entry ? dialog.entry.id : "new"} dialog={dialog} />;
    default:
      return extra ? extra(dialog) : null;
  }
}

function Toasts() {
  const toasts = useStore((s) => s.toasts);
  return (
    <div className="toasts">
      {toasts.map((t) => (
        <div key={t.id} className={`toast toast-${t.kind}`}>
          {t.message}
        </div>
      ))}
    </div>
  );
}

export default function App() {
  useBootstrap();
  useShortcuts();
  const theme = useStore((s) => s.settings.theme);
  const rightPanel = useStore((s) => s.settings.rightPanel);
  const bottomPanel = useStore((s) => s.settings.bottomPanel);
  return (
    <div className={`app theme-${theme}`}>
      <TopBar />
      <div className="main">
        <DrawingToolbar />
        <div className="center">
          <ChartGrid />
          {bottomPanel && <BottomPanel />}
        </div>
        {rightPanel && <RightPanel />}
      </div>
      <StatusBar />
      <Dialogs />
      <ContextMenu />
      <Toasts />
    </div>
  );
}
