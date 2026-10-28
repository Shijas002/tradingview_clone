// Alerts manager (right panel tab): list, pause/resume, edit, test, delete + trigger log.
import React, { useState } from "react";
import { clearAlertEvents, deleteAlert, testAlert, updateAlert } from "../api/client.js";
import { alertBody, describeAlert, loadAlerts } from "../alerts/alerts.js";
import { useStore } from "../store/useStore.js";
import { Icon } from "./Icons.jsx";

function ago(ms) {
  if (!ms) return "";
  const s = Math.round((Date.now() - ms) / 1000);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return new Date(ms).toLocaleDateString();
}

export default function AlertsPanel() {
  const alerts = useStore((s) => s.alerts);
  const events = useStore((s) => s.alertEvents);
  const pane = useStore((s) => s.panes.find((p) => p.id === s.activePaneId) || s.panes[0]);
  const [view, setView] = useState("alerts");
  const s = useStore.getState();

  const run = async (fn, okMsg) => {
    try {
      await fn();
      await loadAlerts();
      if (okMsg) s.toast(okMsg, "ok", 2000);
    } catch (err) {
      s.toast(err.message);
    }
  };

  return (
    <div className="alerts-panel">
      <div className="rp-subhead">
        <button className={view === "alerts" ? "active" : ""} onClick={() => setView("alerts")}>
          Alerts <small>{alerts.filter((a) => a.active).length}</small>
        </button>
        <button className={view === "log" ? "active" : ""} onClick={() => setView("log")}>
          Log <small>{events.length}</small>
        </button>
        <span className="flex1" />
        <button className="icon-btn" title="Create alert" onClick={() => s.openDialog({ type: "alert", paneId: pane.id })}>
          <Icon.plus />
        </button>
      </div>
      {view === "alerts" && (
        <div className="alerts-list">
          {!alerts.length && <div className="empty">No alerts yet. Click + , right-click the chart, or select a drawing and click 🔔.</div>}
          {alerts.map((a) => (
            <div key={a.id} className={`alert-row status-${a.status}`}>
              <div className="alert-main">
                <span className="alert-name">{a.name || a.symbol}</span>
                <span className={`alert-status ${a.status}`}>{a.status}</span>
              </div>
              <div className="alert-desc">
                {a.provider}:{a.symbol} · {describeAlert(a, pane.digits ?? 5)}
              </div>
              <div className="alert-meta">
                {a.trigger_count ? `fired ${a.trigger_count}× · last ${ago(a.last_triggered_at)}` : "never fired"}
                {a.expires_at ? ` · expires ${new Date(a.expires_at).toLocaleString()}` : ""}
              </div>
              <div className="alert-actions">
                {a.active ? (
                  <button onClick={() => run(() => updateAlert(a.id, alertBody(a, { active: false })), "Alert paused")}>Pause</button>
                ) : (
                  <button onClick={() => run(() => updateAlert(a.id, alertBody(a, { active: true })), "Alert restarted")}>{a.status === "paused" ? "Resume" : "Restart"}</button>
                )}
                <button onClick={() => s.openDialog({ type: "alert", alert: a, paneId: pane.id })}>Edit</button>
                <button onClick={() => run(() => testAlert(a.id))}>Test</button>
                <button
                  className="danger"
                  onClick={() => {
                    if (window.confirm(`Delete alert "${a.name || a.symbol}"?`)) run(() => deleteAlert(a.id), "Alert deleted");
                  }}
                >
                  Delete
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
      {view === "log" && (
        <div className="alerts-list">
          {events.length > 0 && (
            <div className="log-actions">
              <button className="btn small" onClick={() => window.confirm("Clear the alert log?") && run(() => clearAlertEvents(), "Log cleared")}>
                Clear log
              </button>
            </div>
          )}
          {!events.length && <div className="empty">No alerts have fired yet.</div>}
          {events.map((e) => (
            <div key={e.id} className="event-row">
              <span className="event-time">{new Date(e.triggered_at).toLocaleString()}</span>
              <span className="event-name">{e.alert_name || e.symbol}</span>
              <span className="event-msg">{e.message}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
