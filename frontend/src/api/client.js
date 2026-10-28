// REST client for the FastAPI backend. All paths are same-origin: the built app is
// served by the backend itself, and the Vite dev server proxies /api to it.

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

export async function api(path, { method = "GET", body, timeout = 20000, signal } = {}) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeout);
  if (signal) signal.addEventListener("abort", () => ctrl.abort(), { once: true });
  try {
    const res = await fetch(path, {
      method,
      signal: ctrl.signal,
      headers: body !== undefined ? { "Content-Type": "application/json", Accept: "application/json" } : { Accept: "application/json" },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    const ct = res.headers.get("content-type") || "";
    const data = ct.includes("application/json") ? await res.json() : null;
    if (!res.ok) {
      const detail = data && data.detail;
      const detailText = Array.isArray(detail)
        ? detail.map((d) => `${(d.loc || []).filter((x) => x !== "body").join(".")}: ${String(d.msg || "").replace(/^Value error, /, "")}`).join("; ")
        : typeof detail === "string" ? detail : detail ? JSON.stringify(detail) : null;
      const msg = (data && (data.error || detailText)) || `HTTP ${res.status}`;
      throw new ApiError(msg, res.status);
    }
    return data;
  } catch (err) {
    if (err.name === "AbortError") throw new ApiError(`Request timed out: ${path}`, 0);
    if (err instanceof ApiError) throw err;
    throw new ApiError(`Network error: ${err.message}`, 0);
  } finally {
    clearTimeout(timer);
  }
}

export const getHealth = () => api("/api/health", { timeout: 4000 });
export const getProviders = () => api("/api/providers");

export function getSymbols(provider, query = "", limit = 300) {
  const q = new URLSearchParams({ provider, limit: String(limit) });
  if (query) q.set("query", query);
  return api(`/api/symbols?${q}`);
}

export async function getHistory(provider, symbol, timeframe, count = 1000, to = null) {
  const q = new URLSearchParams({ provider, symbol, timeframe, count: String(count) });
  if (to) q.set("to", String(to));
  const data = await api(`/api/history?${q}`, { timeout: 30000 });
  return data.candles;
}

// ------------------------------------------------------------ persistence (Phase 3)
export const getWorkspace = () => api("/api/workspace");
export const putWorkspace = (data) => api("/api/workspace", { method: "PUT", body: { data } });
export const getDrawings = () => api("/api/drawings");
export const putDrawings = (key, drawings) => api(`/api/drawings/${encodeURIComponent(key)}`, { method: "PUT", body: { drawings } });
export const getScripts = () => api("/api/scripts");
export const putScript = (id, name, source) => api(`/api/scripts/${encodeURIComponent(id)}`, { method: "PUT", body: { name, source } });
export const deleteScriptApi = (id) => api(`/api/scripts/${encodeURIComponent(id)}`, { method: "DELETE" });
export const exportBackup = () => api("/api/export", { timeout: 60000 });
export const importBackup = (backup) => api("/api/import", { method: "POST", body: backup, timeout: 60000 });

// ------------------------------------------------------------------ alerts (Phase 3)
export const listAlerts = () => api("/api/alerts");
export const createAlert = (alert) => api("/api/alerts", { method: "POST", body: alert });
export const updateAlert = (id, alert) => api(`/api/alerts/${id}`, { method: "PUT", body: alert });
export const deleteAlert = (id) => api(`/api/alerts/${id}`, { method: "DELETE" });
export const listAlertEvents = (limit = 200) => api(`/api/alerts/events?limit=${limit}`);
export const clearAlertEvents = () => api("/api/alerts/events", { method: "DELETE" });
export const testAlert = (id) => api(`/api/alerts/${id}/test`, { method: "POST" });

// ------------------------------------------------------------ broker (Phase 4)
export const getBrokerStatus = () => api("/api/broker/status", { timeout: 5000 });
export const logoutUpstox = () => api("/api/broker/upstox/logout", { method: "POST" });

// ------------------------------------------------------------ journal (Phase 5)
export const getJournal = (params = {}) => api(`/api/journal?${new URLSearchParams(params)}`);
export const getJournalStats = () => api("/api/journal/stats");
export const createJournal = (body) => api("/api/journal", { method: "POST", body, timeout: 30000 });
export const updateJournal = (id, body) => api(`/api/journal/${encodeURIComponent(id)}`, { method: "PUT", body, timeout: 30000 });
export const deleteJournal = (id) => api(`/api/journal/${encodeURIComponent(id)}`, { method: "DELETE" });
export const syncJournal = (id) => api(`/api/journal/${encodeURIComponent(id)}/sync`, { method: "POST" });
export const getNotionStatus = () => api("/api/journal/notion");
export const retryNotion = () => api("/api/journal/notion/retry", { method: "POST" });
