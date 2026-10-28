// Indian broker (Upstox) login state + the login popup.
import { getBrokerStatus, logoutUpstox } from "../api/client.js";
import { useStore } from "../store/useStore.js";

export const LOGIN_URL = "/api/broker/upstox/login";

export async function loadBroker() {
  try {
    const status = await getBrokerStatus();
    useStore.getState().setBroker(status);
    return status;
  } catch {
    return null;
  }
}

let listening = false;
function listen() {
  if (listening) return;
  listening = true;
  window.addEventListener("message", (e) => {
    if (e.origin !== window.location.origin || !e.data || e.data.type !== "broker-login") return;
    loadBroker();
    if (e.data.ok) useStore.getState().toast("Upstox connected - live Indian market data is starting", "ok", 4000);
  });
}

/**
 * Open Upstox's login page in a popup. The backend's callback page closes the
 * popup and notifies this window; polling `closed` covers browsers that block
 * the message (e.g. when the app runs on the Vite dev server port).
 */
export function openUpstoxLogin() {
  listen();
  const w = window.open(LOGIN_URL, "upstox-login", "width=520,height=760");
  if (!w) {
    window.location.href = LOGIN_URL; // popup blocked -> full-page login, the callback returns to /app/
    return;
  }
  const timer = setInterval(() => {
    if (w.closed) {
      clearInterval(timer);
      loadBroker();
    }
  }, 1000);
}

export async function logoutBroker() {
  try {
    await logoutUpstox();
  } catch (err) {
    useStore.getState().toast(`Logout failed: ${err.message}`);
  }
  await loadBroker();
}
