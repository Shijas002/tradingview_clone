// Alerts client helpers: loading, notifications (toast + sound + browser notification).
import { listAlertEvents, listAlerts } from "../api/client.js";
import { useStore } from "../store/useStore.js";

export const CONDITIONS = [
  { v: "crossing", l: "Crossing" },
  { v: "crossing_up", l: "Crossing up" },
  { v: "crossing_down", l: "Crossing down" },
  { v: "greater_than", l: "Greater than" },
  { v: "less_than", l: "Less than" },
  { v: "entering_channel", l: "Entering channel" },
  { v: "exiting_channel", l: "Exiting channel" },
];
export const CONDITION_LABEL = Object.fromEntries(CONDITIONS.map((c) => [c.v, c.l]));
export const TRIGGERS = [
  { v: "once", l: "Only once" },
  { v: "once_per_minute", l: "Once per minute" },
  { v: "every_time", l: "Every time (max 1/s)" },
];

export async function loadAlerts() {
  try {
    const [alerts, events] = await Promise.all([listAlerts(), listAlertEvents(200)]);
    useStore.getState().setAlerts(alerts);
    useStore.getState().setAlertEvents(events);
  } catch (err) {
    useStore.getState().toast(`Could not load alerts: ${err.message}`);
  }
}

let audioCtx = null;
export function beep() {
  try {
    audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
    const now = audioCtx.currentTime;
    [0, 0.18].forEach((offset, i) => {
      const osc = audioCtx.createOscillator();
      const gain = audioCtx.createGain();
      osc.type = "sine";
      osc.frequency.value = i === 0 ? 880 : 1175;
      gain.gain.setValueAtTime(0.0001, now + offset);
      gain.gain.exponentialRampToValueAtTime(0.25, now + offset + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + offset + 0.16);
      osc.connect(gain).connect(audioCtx.destination);
      osc.start(now + offset);
      osc.stop(now + offset + 0.18);
    });
  } catch {
    /* audio not available */
  }
}

export function requestNotificationPermission() {
  try {
    if ("Notification" in window && Notification.permission === "default") Notification.requestPermission();
  } catch {
    /* not supported */
  }
}

export function notifyAlert(data) {
  const st = useStore.getState();
  const title = `🔔 ${data.name || data.symbol}`;
  st.toast(`${title}: ${data.message}`, "alert", 12000);
  if (st.settings.alertSound !== false) beep();
  try {
    if ("Notification" in window && Notification.permission === "granted" && document.visibilityState !== "visible") {
      new Notification(title, { body: data.message, tag: data.alert_id });
    }
  } catch {
    /* ignore */
  }
}

/** Build the API body for an alert row (used by pause/resume and edit). */
export function alertBody(a, patch = {}) {
  return {
    name: a.name || "",
    provider: a.provider,
    symbol: a.symbol,
    condition: a.condition,
    price: a.price,
    price2: a.price2,
    line: a.line,
    trigger: a.trigger,
    message: a.message || "",
    expires_at: a.expires_at && a.expires_at > Date.now() ? a.expires_at : null,
    drawing_id: a.drawing_id,
    active: a.active,
    ...patch,
  };
}

export function describeAlert(a, digits = 5) {
  const cond = CONDITION_LABEL[a.condition] || a.condition;
  if (a.line) return `${cond} trend line`;
  if (a.price2 !== null && a.price2 !== undefined) return `${cond} ${Number(a.price).toFixed(digits)} – ${Number(a.price2).toFixed(digits)}`;
  return `${cond} ${Number(a.price).toFixed(digits)}`;
}
