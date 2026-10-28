// Bottom status bar: connection, providers, data source, clock.
import React, { useEffect, useState } from "react";
import { useStore } from "../store/useStore.js";
import { clockText } from "../chart/timezone.js";
import { useChartZone } from "../chart/useChartZone.js";
import { BrokerLoginButton } from "../broker/BrokerLogin.jsx";

export default function StatusBar() {
  const connection = useStore((s) => s.connection);
  const providers = useStore((s) => s.providers);
  const sync = useStore((s) => s.syncState);
  const zone = useChartZone();
  const [now, setNow] = useState(new Date());
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);
  const connCls = connection === "open" ? "ok" : connection === "connecting" ? "warn" : "bad";
  const connText = connection === "open" ? "Live" : connection === "connecting" ? "Connecting…" : "Disconnected — retrying";
  return (
    <footer className="statusbar">
      <span title="WebSocket to the backend">
        <i className={`dot ${connCls}`} />
        {connText}
      </span>
      {providers.map((p) => (
        <span key={p.name} title={p.message}>
          <i className={`dot ${p.connected ? "ok" : "bad"}`} />
          {p.name}
          {p.details && p.details.active_source ? ` → ${p.details.active_source}` : ""}
          {p.name === "upstox" && <BrokerLoginButton compact />}
        </span>
      ))}
      <span className="flex1" />
      <span className={`sync sync-${sync.state}`} title={sync.message || "Workspace is saved to backend/data/tradingview.db"}>
        {{ local: "Not synced", loading: "Loading workspace…", saving: "Saving…", saved: "✓ Saved", error: "⚠ Save failed — retrying", offline: "⚠ Offline — saved in browser" }[sync.state] || sync.state}
      </span>
      <span className="muted" title="Chart time zone - change it in ⚙ Chart settings">
        {clockText(now.getTime(), zone)} {zone.label}
      </span>
    </footer>
  );
}
