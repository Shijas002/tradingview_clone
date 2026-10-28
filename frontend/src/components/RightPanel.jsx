// Right sidebar with tabs: Watchlist and Alerts.
import React from "react";
import { useStore } from "../store/useStore.js";
import Watchlist from "./Watchlist.jsx";
import AlertsPanel from "./AlertsPanel.jsx";
import { Icon } from "./Icons.jsx";

export default function RightPanel() {
  const tab = useStore((s) => s.settings.rightTab || "watchlist");
  const activeAlerts = useStore((s) => s.alerts.filter((a) => a.active).length);
  const s = useStore.getState();
  return (
    <aside className="right-panel">
      <div className="rp-tabs">
        <button className={tab === "watchlist" ? "active" : ""} onClick={() => s.updateSettings({ rightTab: "watchlist" })} title="Watchlist">
          <Icon.star />
          <span>Watchlist</span>
        </button>
        <button className={tab === "alerts" ? "active" : ""} onClick={() => s.updateSettings({ rightTab: "alerts" })} title="Alerts">
          <Icon.bell />
          <span>Alerts{activeAlerts ? ` (${activeAlerts})` : ""}</span>
        </button>
      </div>
      <div className="rp-body">{tab === "alerts" ? <AlertsPanel /> : <Watchlist />}</div>
    </aside>
  );
}
