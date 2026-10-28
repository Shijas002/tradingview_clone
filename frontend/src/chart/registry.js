// paneId -> ChartController, so toolbars and shortcuts can reach the right chart.
export const controllers = new Map();

export function activeController(state) {
  const id = state.activePaneId || (state.panes[0] && state.panes[0].id);
  return controllers.get(id) || null;
}

/** Last close of a chart (used to pre-fill the alert dialog). */
export function lastPrice(paneId) {
  const c = controllers.get(paneId);
  return c && c.bars.length ? c.bars[c.bars.length - 1].close : null;
}

// Diagnostics handle (used by the automated UI tests; harmless in production).
if (typeof window !== "undefined") window.__tvControllers = controllers;
