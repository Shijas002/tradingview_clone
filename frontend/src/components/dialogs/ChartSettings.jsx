// Chart settings: appearance + price scale of the active chart.
import React, { useEffect } from "react";
import { exportBackup, importBackup } from "../../api/client.js";
import { useStore } from "../../store/useStore.js";
import { Modal } from "../ui.jsx";
import { TIMEZONES } from "../../chart/timezone.js";
import { loadBroker, logoutBroker, openUpstoxLogin } from "../../broker/broker.js";

function BrokerSection() {
  const broker = useStore((s) => s.broker);
  if (!broker) return <div className="form-row muted">Broker status unavailable (backend offline?)</div>;
  if (!broker.enabled) return <div className="form-row muted">{broker.message}</div>;
  const expires = broker.expires_at ? new Date(broker.expires_at * 1000).toLocaleString() : null;
  return (
    <div className="broker-box">
      <div>
        <b>Upstox</b> ·{" "}
        {broker.mode === "analytics" ? "Analytics Token (1 year, read-only)" : broker.mode === "oauth" ? "Daily login" : "Not configured"}
      </div>
      <div className={broker.authenticated ? "ok" : "bad"}>
        {broker.authenticated ? "● Connected" : "● Not connected"}
        {broker.user_id ? ` as ${broker.user_id}` : ""}
        {expires ? ` · valid until ${expires}` : ""}
      </div>
      <div className="muted">{broker.message}</div>
      {broker.login_available && (
        <div className="btn-row">
          <button className="btn small primary" onClick={openUpstoxLogin}>
            {broker.authenticated ? "Log in again" : "Log in to Upstox"}
          </button>
          {broker.authenticated && (
            <button className="btn small" onClick={logoutBroker}>
              Log out
            </button>
          )}
        </div>
      )}
    </div>
  );
}

async function downloadBackup() {
  const st = useStore.getState();
  try {
    const data = await exportBackup();
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `tvclone-backup-${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  } catch (err) {
    st.toast(`Export failed: ${err.message}`);
  }
}

async function uploadBackup(e) {
  const st = useStore.getState();
  const file = e.target.files && e.target.files[0];
  e.target.value = "";
  if (!file) return;
  try {
    const data = JSON.parse(await file.text());
    if (!window.confirm("Importing replaces your current layouts, drawings, scripts, alerts and journal. Continue?")) return;
    await importBackup(data);
    st.toast("Backup imported — reloading…", "ok", 3000);
    setTimeout(() => window.location.reload(), 800);
  } catch (err) {
    st.toast(`Import failed: ${err.message}`);
  }
}

export default function ChartSettings() {
  const settings = useStore((s) => s.settings);
  const pane = useStore((s) => s.panes.find((p) => p.id === s.activePaneId) || s.panes[0]);
  const s = useStore.getState();
  const ps = pane.priceScale || { mode: "normal", inverted: false };
  useEffect(() => {
    loadBroker();
  }, []);
  return (
    <Modal title="Chart settings" onClose={() => s.closeDialog()} width={420} footer={<button className="btn primary" onClick={() => s.closeDialog()}>Done</button>}>
      <div className="form-grid">
        <div className="form-group-title">Appearance</div>
        <label className="form-row">
          <span>Theme</span>
          <select value={settings.theme} onChange={(e) => s.updateSettings({ theme: e.target.value })}>
            <option value="dark">Dark</option>
            <option value="light">Light</option>
          </select>
        </label>
        <label className="form-row">
          <span>Time zone</span>
          <select value={settings.timezone || "exchange"} onChange={(e) => s.updateSettings({ timezone: e.target.value })}>
            {TIMEZONES.map((z) => (
              <option key={z.value} value={z.value}>
                {z.label}
              </option>
            ))}
          </select>
        </label>
        <label className="form-row">
          <span>Volume</span>
          <input type="checkbox" checked={settings.showVolume} onChange={(e) => s.updateSettings({ showVolume: e.target.checked })} />
        </label>
        <label className="form-row">
          <span>Grid lines</span>
          <input type="checkbox" checked={settings.grid} onChange={(e) => s.updateSettings({ grid: e.target.checked })} />
        </label>
        <label className="form-row">
          <span>Crosshair</span>
          <select value={settings.crosshair} onChange={(e) => s.updateSettings({ crosshair: e.target.value })}>
            <option value="normal">Free</option>
            <option value="magnet">Magnet to close</option>
          </select>
        </label>
        <div className="form-group-title">Price scale ({pane.symbol})</div>
        <label className="form-row">
          <span>Scale</span>
          <select value={ps.mode} onChange={(e) => s.updatePane(pane.id, { priceScale: { ...ps, mode: e.target.value } })}>
            <option value="normal">Regular</option>
            <option value="log">Logarithmic</option>
            <option value="percent">Percent</option>
            <option value="indexed">Indexed to 100</option>
          </select>
        </label>
        <label className="form-row">
          <span>Invert scale</span>
          <input type="checkbox" checked={!!ps.inverted} onChange={(e) => s.updatePane(pane.id, { priceScale: { ...ps, inverted: e.target.checked } })} />
        </label>
        <div className="form-group-title">Alerts</div>
        <label className="form-row">
          <span>Alert sound</span>
          <input type="checkbox" checked={settings.alertSound !== false} onChange={(e) => s.updateSettings({ alertSound: e.target.checked })} />
        </label>
        <div className="form-group-title">Indian broker</div>
        <BrokerSection />
        <div className="form-group-title">Backup</div>
        <div className="form-row">
          <span>Layouts, drawings, scripts, alerts, journal</span>
          <span className="btn-row">
            <button className="btn small" onClick={downloadBackup}>
              Export
            </button>
            <label className="btn small">
              Import
              <input type="file" accept="application/json,.json" hidden onChange={uploadBackup} />
            </label>
          </span>
        </div>
        <div className="form-group-title">Drawing</div>
        <label className="form-row">
          <span>Stay in drawing mode</span>
          <input type="checkbox" checked={!!settings.stayInDrawingMode} onChange={(e) => s.updateSettings({ stayInDrawingMode: e.target.checked })} />
        </label>
      </div>
    </Modal>
  );
}
