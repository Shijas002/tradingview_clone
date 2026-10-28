// Server persistence (Phase 3): keeps the zustand workspace in sync with SQLite.
//
// Startup: load workspace + drawings + scripts from the backend. If the server is
// still empty (first run after Phase 2) the browser's local copy is uploaded instead.
// Afterwards every change is debounced and written back; only the parts that
// actually changed are sent (workspace JSON, individual drawing sets, scripts).

import { deleteScriptApi, getDrawings, getScripts, getWorkspace, putDrawings, putScript, putWorkspace } from "../api/client.js";
import { useStore } from "./useStore.js";

const DEBOUNCE_MS = 700;

function workspacePart(s) {
  return JSON.stringify({
    layout: s.layout,
    panes: s.panes,
    activePaneId: s.activePaneId,
    watchlist: s.watchlist,
    settings: s.settings,
  });
}

let started = false;

export async function startServerSync() {
  if (started) return;
  started = true;
  const st = useStore.getState();
  st.setSyncState("loading");

  let ws;
  let drawings;
  let scripts;
  try {
    [ws, drawings, scripts] = await Promise.all([getWorkspace(), getDrawings(), getScripts()]);
  } catch (err) {
    started = false;
    st.setSyncState("offline", err.message);
    setTimeout(startServerSync, 5000);
    return;
  }

  const serverScripts = scripts.map((x) => ({ id: x.id, name: x.name, source: x.source, updatedAt: x.updated_at }));
  if (ws.version > 0) {
    // the server is the source of truth
    st.hydrateWorkspace({ ...ws.data, drawingsBySymbol: drawings, scripts: serverScripts });
  } else {
    // first run against an empty database: upload what this browser already has
    const local = useStore.getState();
    try {
      await putWorkspace(JSON.parse(workspacePart(local)));
      for (const [key, list] of Object.entries(local.drawingsBySymbol)) if (list && list.length) await putDrawings(key, list);
      for (const sc of local.scripts) await putScript(sc.id, sc.name, sc.source);
    } catch (err) {
      st.setSyncState("error", err.message);
    }
  }

  // ---- change tracking
  let prev = useStore.getState();
  let lastWorkspace = workspacePart(prev);
  let pendingDrawingKeys = new Set();
  let timer = null;
  let retryTimer = null;

  const flush = async () => {
    timer = null;
    const cur = useStore.getState();
    const jobs = [];
    const wsNow = workspacePart(cur);
    if (wsNow !== lastWorkspace) {
      const snapshot = wsNow;
      jobs.push(putWorkspace(JSON.parse(snapshot)).then(() => (lastWorkspace = snapshot)));
    }
    const keys = [...pendingDrawingKeys];
    pendingDrawingKeys = new Set();
    for (const key of keys) {
      jobs.push(
        putDrawings(key, cur.drawingsBySymbol[key] || []).catch((err) => {
          pendingDrawingKeys.add(key); // retry later
          throw err;
        }),
      );
    }
    const scriptJobs = cur._scriptOps || [];
    if (scriptJobs.length) {
      useStore.setState({ _scriptOps: [] });
      for (const op of scriptJobs) {
        const p = op.type === "delete" ? deleteScriptApi(op.id).catch((e) => (e.status === 404 ? null : Promise.reject(e))) : putScript(op.id, op.name, op.source);
        jobs.push(
          p.catch((err) => {
            useStore.setState((s) => ({ _scriptOps: [...(s._scriptOps || []), op] }));
            throw err;
          }),
        );
      }
    }
    if (!jobs.length) return;
    useStore.getState().setSyncState("saving");
    const results = await Promise.allSettled(jobs);
    const failed = results.find((r) => r.status === "rejected");
    if (failed) {
      useStore.getState().setSyncState("error", failed.reason && failed.reason.message);
      clearTimeout(retryTimer);
      retryTimer = setTimeout(schedule, 5000);
    } else useStore.getState().setSyncState("saved");
  };

  const schedule = () => {
    clearTimeout(timer);
    timer = setTimeout(flush, DEBOUNCE_MS);
  };

  useStore.subscribe((state) => {
    let dirty = false;
    if (state.drawingsBySymbol !== prev.drawingsBySymbol) {
      for (const key of new Set([...Object.keys(state.drawingsBySymbol), ...Object.keys(prev.drawingsBySymbol)])) {
        if (state.drawingsBySymbol[key] !== prev.drawingsBySymbol[key]) pendingDrawingKeys.add(key);
      }
      dirty = true;
    }
    if (state.layout !== prev.layout || state.panes !== prev.panes || state.activePaneId !== prev.activePaneId || state.watchlist !== prev.watchlist || state.settings !== prev.settings) dirty = true;
    if (state._scriptOps !== prev._scriptOps && (state._scriptOps || []).length) dirty = true;
    prev = state;
    if (dirty) schedule();
  });

  window.addEventListener("beforeunload", () => {
    if (timer) flush();
  });
  useStore.getState().setSyncState("saved");
}
