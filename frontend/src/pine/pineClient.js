// Promise-based wrapper around the Pine worker (single shared worker for all panes).

let worker = null;
let nextId = 1;
const pending = new Map();

function getWorker() {
  if (!worker) {
    worker = new Worker(new URL("./pine.worker.js", import.meta.url), { type: "module" });
    worker.onmessage = (ev) => {
      const { reqId, ok, result, error } = ev.data;
      const p = pending.get(reqId);
      if (!p) return;
      pending.delete(reqId);
      if (ok) p.resolve(result);
      else p.reject(new Error(error));
    };
    worker.onerror = (ev) => {
      const err = new Error(`Pine worker crashed: ${ev.message || "unknown error"}`);
      for (const p of pending.values()) p.reject(err);
      pending.clear();
      worker = null; // recreated on next call
    };
  }
  return worker;
}

function request(msg) {
  const reqId = nextId++;
  return new Promise((resolve, reject) => {
    pending.set(reqId, { resolve, reject });
    getWorker().postMessage({ ...msg, reqId });
  });
}

export const pine = {
  setData(datasetId, bars, { tickerId, timeframe, tfSeconds, provider, backendTf, digits }) {
    getWorker().postMessage({ type: "setData", datasetId, bars, tickerId, timeframe, tfSeconds, provider, backendTf, digits });
  },
  updateBar(datasetId, bar) {
    getWorker().postMessage({ type: "updateBar", datasetId, bar });
  },
  dropData(datasetId) {
    getWorker().postMessage({ type: "dropData", datasetId });
  },
  run(datasetId, source, inputs) {
    return request({ type: "run", datasetId, source, inputs });
  },
  inspect(source) {
    return request({ type: "inspect", source });
  },
};

/** Extract "line:col" from PineTS error messages, e.g. "Unexpected token EOF '' at 3:21". */
export function errorLocation(message) {
  const m = /at (\d+):(\d+)/.exec(message || "") || /line (\d+)(?:[^\d]+(\d+))?/i.exec(message || "");
  if (!m) return null;
  return { line: Number(m[1]), col: Number(m[2] || 1) };
}
