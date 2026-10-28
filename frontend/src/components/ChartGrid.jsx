// Multi-chart layout (1, 2, 3 or 4 charts).
import React from "react";
import { useStore } from "../store/useStore.js";
import ChartPane from "./ChartPane.jsx";

export default function ChartGrid() {
  const layout = useStore((s) => s.layout);
  const panes = useStore((s) => s.panes);
  const activePaneId = useStore((s) => s.activePaneId) || (panes[0] && panes[0].id);
  return (
    <div className={`chart-grid layout-${layout}`}>
      {panes.map((p) => (
        <ChartPane key={p.id} pane={p} active={p.id === activePaneId && panes.length > 0} />
      ))}
    </div>
  );
}
