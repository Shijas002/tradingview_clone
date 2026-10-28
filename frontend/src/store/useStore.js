// Global application state (zustand).
//
// Workspace data (layout, panes, indicators, drawings, watchlist, scripts, settings)
// is persisted. Phase 3 syncs it to the backend SQLite database; localStorage is
// kept as an offline cache so the app still restores instantly.

import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import { uid } from "../utils/market.js";

export const LAYOUTS = {
  "1": { label: "Single", panes: 1 },
  "2v": { label: "2 side by side", panes: 2 },
  "2h": { label: "2 stacked", panes: 2 },
  "3": { label: "1 + 2", panes: 3 },
  "4": { label: "Grid 2×2", panes: 4 },
};

function newPane(overrides = {}) {
  return { id: uid("pane"), provider: null, symbol: "EURUSD", timeframe: "M1", chartType: "candles", digits: 5, indicators: [], priceScale: { mode: "normal", inverted: false }, ...overrides };
}

export const symbolKey = (provider, symbol) => `${provider}:${symbol}`.toLowerCase();

const HISTORY_LIMIT = 100;

export const useStore = create(
  persist(
    (set, get) => ({
      // -------------------------------------------------------- runtime only
      providers: [],
      broker: null,
      journal: [],
      journalStats: null,
      notion: null,
      connection: "connecting",
      toasts: [],
      dialog: null, // { type: "symbol"|"indicators"|"settings"|"indicatorSettings"|"text"|"chartSettings", ... }
      contextMenu: null,
      selectedDrawing: null, // { paneId, drawing }
      paneRuntime: {}, // paneId -> { indicators: [...runtime info], loading, error, quote }
      drawingHistory: { past: [], future: [] },
      syncState: { state: "local", message: "" }, // local | loading | saving | saved | error | offline
      _scriptOps: [], // pending script writes for the server (see serverSync.js)
      alerts: [],
      alertEvents: [],

      // ---------------------------------------------------------- persisted
      layout: "1",
      panes: [newPane()],
      activePaneId: null,
      drawingsBySymbol: {},
      watchlist: [],
      scripts: [], // user Pine scripts { id, name, source, updatedAt }
      settings: { theme: "dark", showVolume: true, grid: true, crosshair: "normal", rightPanel: true, bottomPanel: true, bottomHeight: 300, bottomTab: "pine", stayInDrawingMode: false, timezone: "exchange" },
      tool: "cursor",
      magnet: false,
      drawingsLocked: false,
      drawingsHidden: false,
      editor: { scriptId: null, source: null, dirty: false },

      // ----------------------------------------------------------- helpers
      activePane() {
        const s = get();
        return s.panes.find((p) => p.id === s.activePaneId) || s.panes[0];
      },

      toast(message, kind = "error", ms = 5000) {
        const id = uid("t");
        set((s) => ({ toasts: [...s.toasts, { id, message, kind }].slice(-5) }));
        setTimeout(() => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })), ms);
      },

      setSyncState(state, message = "") {
        set({ syncState: { state, message } });
      },

      setAlerts(alerts) {
        set({ alerts });
      },

      setAlertEvents(alertEvents) {
        set({ alertEvents });
      },

      setBroker(broker) {
        set({ broker });
      },

      setJournal(patch) {
        set(patch);
      },

      setProviders(providers) {
        set({ providers });
      },

      upsertProviderStatus(p) {
        set((s) => {
          const list = s.providers.slice();
          const i = list.findIndex((x) => x.name === p.name);
          if (i >= 0) list[i] = p;
          else list.push(p);
          return { providers: list };
        });
      },

      setConnection(connection) {
        set({ connection });
      },

      openDialog(dialog) {
        set({ dialog });
      },

      closeDialog() {
        set({ dialog: null });
      },

      setContextMenu(contextMenu) {
        set({ contextMenu });
      },

      // ------------------------------------------------------------ layout
      setLayout(layout) {
        const need = LAYOUTS[layout].panes;
        set((s) => {
          let panes = s.panes.slice(0, need);
          const base = panes[0] || newPane();
          while (panes.length < need) panes.push(newPane({ provider: base.provider, symbol: base.symbol, digits: base.digits, timeframe: ["M1", "M15", "H1", "D1"][panes.length] || base.timeframe }));
          const activePaneId = panes.some((p) => p.id === s.activePaneId) ? s.activePaneId : panes[0].id;
          return { layout, panes, activePaneId };
        });
      },

      setActivePane(id) {
        if (get().activePaneId !== id) set({ activePaneId: id, selectedDrawing: null });
      },

      updatePane(id, patch) {
        set((s) => ({ panes: s.panes.map((p) => (p.id === id ? { ...p, ...patch } : p)) }));
      },

      setPaneRuntime(id, patch) {
        set((s) => ({ paneRuntime: { ...s.paneRuntime, [id]: { ...(s.paneRuntime[id] || {}), ...patch } } }));
      },

      // -------------------------------------------------------- indicators
      addIndicator(paneId, { name, source, scriptId = null, builtinId = null, inputs = {} }) {
        const ind = { id: uid("ind"), name, source, scriptId, builtinId, inputs, visible: true };
        set((s) => ({ panes: s.panes.map((p) => (p.id === paneId ? { ...p, indicators: [...p.indicators, ind] } : p)) }));
        return ind.id;
      },

      updateIndicator(paneId, indId, patch) {
        set((s) => ({ panes: s.panes.map((p) => (p.id === paneId ? { ...p, indicators: p.indicators.map((i) => (i.id === indId ? { ...i, ...patch } : i)) } : p)) }));
      },

      removeIndicator(paneId, indId) {
        set((s) => ({ panes: s.panes.map((p) => (p.id === paneId ? { ...p, indicators: p.indicators.filter((i) => i.id !== indId) } : p)) }));
      },

      // ---------------------------------------------------------- drawings
      setDrawings(key, list, { record = true } = {}) {
        set((s) => {
          const prev = s.drawingsBySymbol[key] || [];
          const history = record
            ? { past: [...s.drawingHistory.past, { key, list: prev }].slice(-HISTORY_LIMIT), future: [] }
            : s.drawingHistory;
          return { drawingsBySymbol: { ...s.drawingsBySymbol, [key]: list }, drawingHistory: history };
        });
      },

      undoDrawings() {
        const { past, future } = get().drawingHistory;
        if (!past.length) return false;
        const last = past[past.length - 1];
        set((s) => ({
          drawingsBySymbol: { ...s.drawingsBySymbol, [last.key]: last.list },
          drawingHistory: { past: past.slice(0, -1), future: [...future, { key: last.key, list: s.drawingsBySymbol[last.key] || [] }] },
          selectedDrawing: null,
        }));
        return true;
      },

      redoDrawings() {
        const { past, future } = get().drawingHistory;
        if (!future.length) return false;
        const next = future[future.length - 1];
        set((s) => ({
          drawingsBySymbol: { ...s.drawingsBySymbol, [next.key]: next.list },
          drawingHistory: { past: [...past, { key: next.key, list: s.drawingsBySymbol[next.key] || [] }], future: future.slice(0, -1) },
          selectedDrawing: null,
        }));
        return true;
      },

      setSelectedDrawing(selectedDrawing) {
        set({ selectedDrawing });
      },

      setTool(tool) {
        set({ tool });
      },

      toggle(key) {
        set((s) => ({ [key]: !s[key] }));
      },

      // ------------------------------------------------------- watchlist
      addToWatchlist(provider, symbol) {
        set((s) => (s.watchlist.some((w) => w.provider === provider && w.symbol === symbol) ? {} : { watchlist: [...s.watchlist, { provider, symbol }] }));
      },

      removeFromWatchlist(provider, symbol) {
        set((s) => ({ watchlist: s.watchlist.filter((w) => !(w.provider === provider && w.symbol === symbol)) }));
      },

      moveWatchlistItem(from, to) {
        set((s) => {
          const list = s.watchlist.slice();
          const [item] = list.splice(from, 1);
          list.splice(to, 0, item);
          return { watchlist: list };
        });
      },

      // ---------------------------------------------------------- scripts
      saveScript({ id, name, source }) {
        const now = Date.now();
        let savedId = id;
        set((s) => {
          if (id && s.scripts.some((x) => x.id === id)) {
            return { scripts: s.scripts.map((x) => (x.id === id ? { ...x, name, source, updatedAt: now } : x)), _scriptOps: [...s._scriptOps, { type: "put", id, name, source }] };
          }
          savedId = uid("scr");
          return { scripts: [...s.scripts, { id: savedId, name, source, updatedAt: now }], _scriptOps: [...s._scriptOps, { type: "put", id: savedId, name, source }] };
        });
        // refresh every chart instance of this script
        set((s) => ({ panes: s.panes.map((p) => ({ ...p, indicators: p.indicators.map((i) => (i.scriptId === savedId ? { ...i, source, name } : i)) })) }));
        return savedId;
      },

      deleteScript(id) {
        set((s) => ({ scripts: s.scripts.filter((x) => x.id !== id), _scriptOps: [...s._scriptOps, { type: "delete", id }] }));
      },

      setEditor(patch) {
        set((s) => ({ editor: { ...s.editor, ...patch } }));
      },

      // --------------------------------------------------------- settings
      updateSettings(patch) {
        set((s) => ({ settings: { ...s.settings, ...patch } }));
      },

      /** Replace the persisted workspace (used by Phase 3 server sync). */
      hydrateWorkspace(ws) {
        set((s) => ({
          layout: ws.layout || s.layout,
          panes: ws.panes && ws.panes.length ? ws.panes : s.panes,
          activePaneId: ws.activePaneId || s.activePaneId,
          drawingsBySymbol: ws.drawingsBySymbol || s.drawingsBySymbol,
          watchlist: ws.watchlist || s.watchlist,
          scripts: ws.scripts || s.scripts,
          settings: { ...s.settings, ...(ws.settings || {}) },
        }));
      },
    }),
    {
      name: "tvclone-workspace-v1",
      storage: createJSONStorage(() => {
        try {
          const k = "__tvclone_probe__";
          localStorage.setItem(k, "1");
          localStorage.removeItem(k);
          return localStorage;
        } catch {
          const mem = new Map();
          return { getItem: (k) => mem.get(k) ?? null, setItem: (k, v) => mem.set(k, v), removeItem: (k) => mem.delete(k) };
        }
      }),
      partialize: (s) => ({
        layout: s.layout,
        panes: s.panes,
        activePaneId: s.activePaneId,
        drawingsBySymbol: s.drawingsBySymbol,
        watchlist: s.watchlist,
        scripts: s.scripts,
        settings: s.settings,
        magnet: s.magnet,
        editor: s.editor,
      }),
    },
  ),
);

export function workspaceSnapshot() {
  const s = useStore.getState();
  return { layout: s.layout, panes: s.panes, activePaneId: s.activePaneId, drawingsBySymbol: s.drawingsBySymbol, watchlist: s.watchlist, scripts: s.scripts, settings: s.settings };
}
