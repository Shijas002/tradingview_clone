// Chart time zone: which clock the time axis, crosshair label, trade list and
// status-bar clock show.
//
// Candle timestamps from the backend are real UTC for simulated / oanda / upstox.
// MT5 (and "auto") timestamps are the broker's *server* clock written as UTC, so
// to show another zone we first undo that shift (taken from the provider status).
//
// "exchange" (the default) shows each market in its own clock, like TradingView:
//   upstox -> Asia/Kolkata (IST), mt5/auto -> broker server time, others -> UTC.

export const TIMEZONES = [
  { value: "exchange", label: "Exchange (auto)" },
  { value: "local", label: "My computer's time zone" },
  { value: "UTC", label: "UTC" },
  { value: "Asia/Kolkata", label: "(UTC+5:30) Kolkata / Mumbai" },
  { value: "Asia/Dubai", label: "(UTC+4) Dubai" },
  { value: "Asia/Singapore", label: "(UTC+8) Singapore" },
  { value: "Asia/Tokyo", label: "(UTC+9) Tokyo" },
  { value: "Australia/Sydney", label: "Sydney" },
  { value: "Europe/London", label: "London" },
  { value: "Europe/Berlin", label: "Berlin / Frankfurt" },
  { value: "America/New_York", label: "New York" },
  { value: "America/Chicago", label: "Chicago" },
  { value: "America/Los_Angeles", label: "Los Angeles" },
];

const EXCHANGE_TZ = { upstox: "Asia/Kolkata", oanda: "UTC", simulated: "UTC" };
const SERVER_CLOCK = "SERVER"; // render raw timestamps (broker server time)

/** Seconds the provider's timestamps are ahead of real UTC (MT5 server time). */
export function sourceShiftSeconds(providerStatus) {
  const d = (providerStatus && providerStatus.details) || {};
  const hours = d.time_shift_hours ?? d.server_utc_offset_hours;
  return typeof hours === "number" && Number.isFinite(hours) ? Math.round(hours * 3600) : 0;
}

/** Resolve the setting into { tz, shift } used by the formatters. */
export function resolveTimezone(setting, provider, providerStatus) {
  const shift = sourceShiftSeconds(providerStatus);
  const isServerClock = provider === "mt5" || provider === "auto";
  if (!setting || setting === "exchange") {
    if (isServerClock) return { tz: SERVER_CLOCK, shift: 0, label: "broker time", sourceShift: shift };
    const tz = EXCHANGE_TZ[provider] || "UTC";
    return { tz, shift: 0, label: tzLabel(tz), sourceShift: 0 };
  }
  const tz = setting === "local" ? Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC" : setting;
  return { tz, shift: isServerClock ? shift : 0, label: tzLabel(tz), sourceShift: isServerClock ? shift : 0 };
}

/** Current wall-clock time "HH:MM:SS" in the resolved zone. */
export function clockText(nowMs, zone) {
  const secs = Math.floor(nowMs / 1000);
  const w = zone.tz === SERVER_CLOCK ? wallClock(secs + (zone.sourceShift || 0), zone.tz, 0) : wallClock(secs, zone.tz, 0);
  return `${w.hour}:${w.minute}:${w.second}`;
}

const fmtCache = new Map();
function partsFormatter(tz) {
  const zone = tz === SERVER_CLOCK ? "UTC" : tz;
  let f = fmtCache.get(zone);
  if (!f) {
    try {
      f = new Intl.DateTimeFormat("en-GB", {
        timeZone: zone, year: "numeric", month: "short", day: "2-digit", weekday: "short",
        hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
      });
    } catch {
      f = new Intl.DateTimeFormat("en-GB", {
        timeZone: "UTC", year: "numeric", month: "short", day: "2-digit", weekday: "short",
        hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
      });
    }
    fmtCache.set(zone, f);
  }
  return f;
}

/** Wall-clock parts of a chart timestamp (seconds) in the resolved zone. */
export function wallClock(timeSec, tz, shift = 0) {
  const parts = {};
  for (const p of partsFormatter(tz).formatToParts(new Date((timeSec - shift) * 1000))) parts[p.type] = p.value;
  return {
    year: parts.year, month: parts.month, day: parts.day, weekday: parts.weekday,
    hour: parts.hour === "24" ? "00" : parts.hour, minute: parts.minute, second: parts.second,
  };
}

export function tzLabel(tz) {
  if (tz === SERVER_CLOCK) return "broker time";
  if (tz === "UTC") return "UTC";
  try {
    const name = new Intl.DateTimeFormat("en-US", { timeZone: tz, timeZoneName: "shortOffset" })
      .formatToParts(new Date()).find((p) => p.type === "timeZoneName");
    return name ? name.value.replace("GMT", "UTC") : tz;
  } catch {
    return tz;
  }
}

/** "Mon 03 Nov '25 09:15" (or just the date for daily+ bars). */
export function formatDateTime(timeSec, tz, shift = 0, withTime = true) {
  const w = wallClock(timeSec, tz, shift);
  const date = `${w.weekday} ${w.day} ${w.month} '${String(w.year).slice(-2)}`;
  return withTime ? `${date} ${w.hour}:${w.minute}` : date;
}

/** Compact "2025-11-03 09:15" for tables. */
export function formatStamp(timeSec, tz, shift = 0) {
  const w = wallClock(timeSec, tz, shift);
  const m = String(MONTHS.indexOf(w.month) + 1).padStart(2, "0");
  return `${w.year}-${m}-${w.day} ${w.hour}:${w.minute}`;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/**
 * lightweight-charts formatters for the given zone.
 * TickMarkType: 0 Year, 1 Month, 2 DayOfMonth, 3 Time, 4 TimeWithSeconds.
 */
export function chartTimeFormatters(tz, shift, isIntraday) {
  return {
    timeFormatter: (time) => (typeof time === "number" ? formatDateTime(time, tz, shift, isIntraday()) : ""),
    tickMarkFormatter: (time, type) => {
      if (typeof time !== "number") return null;
      const w = wallClock(time, tz, shift);
      switch (type) {
        case 0:
          return String(w.year);
        case 1:
          return w.month;
        case 2:
          return String(Number(w.day));
        case 4:
          return `${w.hour}:${w.minute}:${w.second}`;
        default:
          return `${w.hour}:${w.minute}`;
      }
    },
  };
}
