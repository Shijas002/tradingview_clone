// "Log in to Upstox" button - only rendered when a daily login is needed.
import React from "react";
import { useStore } from "../store/useStore.js";
import { openUpstoxLogin } from "./broker.js";

export function BrokerLoginButton({ compact = false }) {
  const broker = useStore((s) => s.broker);
  if (!broker || !broker.enabled || broker.authenticated || !broker.login_available) return null;
  return (
    <button
      className={`btn ${compact ? "tiny" : "small"} primary broker-login`}
      onClick={(e) => {
        e.stopPropagation();
        openUpstoxLogin();
      }}
      title="Upstox access tokens end at 03:30 IST every day - log in once per day"
    >
      Log in to Upstox
    </button>
  );
}
