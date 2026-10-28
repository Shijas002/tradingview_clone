import { describe, expect, it } from "vitest";
import { chartTimeFormatters, clockText, formatDateTime, formatStamp, resolveTimezone, sourceShiftSeconds, wallClock } from "../chart/timezone.js";

// 2025-11-03 03:45:00 UTC = 09:15 IST (NSE open)
const NSE_OPEN = Date.UTC(2025, 10, 3, 3, 45) / 1000;

describe("chart time zone", () => {
  it("shows Indian symbols in IST by default and FX in UTC", () => {
    const up = resolveTimezone("exchange", "upstox", null);
    expect(up.tz).toBe("Asia/Kolkata");
    expect(up.label).toBe("UTC+5:30");
    expect(formatStamp(NSE_OPEN, up.tz, up.shift)).toBe("2025-11-03 09:15");
    expect(resolveTimezone("exchange", "oanda", null).tz).toBe("UTC");
  });

  it("keeps MT5 in broker time, and undoes the server offset for an explicit zone", () => {
    const status = { details: { server_utc_offset_hours: 2 } };
    expect(sourceShiftSeconds(status)).toBe(7200);
    expect(sourceShiftSeconds({ details: { time_shift_hours: 3 } })).toBe(10800);
    const broker = resolveTimezone("exchange", "mt5", status);
    expect(broker.label).toBe("broker time");
    const serverStamp = NSE_OPEN + 7200; // MT5 writes server time (UTC+2) as if it were UTC
    expect(formatStamp(serverStamp, broker.tz, broker.shift)).toBe("2025-11-03 05:45");
    const ist = resolveTimezone("Asia/Kolkata", "mt5", status);
    expect(formatStamp(serverStamp, ist.tz, ist.shift)).toBe("2025-11-03 09:15");
    expect(clockText(NSE_OPEN * 1000, broker)).toBe("05:45:00");
    expect(clockText(NSE_OPEN * 1000, ist)).toBe("09:15:00");
  });

  it("formats crosshair labels and tick marks", () => {
    const { timeFormatter, tickMarkFormatter } = chartTimeFormatters("Asia/Kolkata", 0, () => true);
    expect(timeFormatter(NSE_OPEN)).toBe("Mon 03 Nov '25 09:15");
    expect(tickMarkFormatter(NSE_OPEN, 3)).toBe("09:15");
    expect(tickMarkFormatter(NSE_OPEN, 2)).toBe("3");
    expect(tickMarkFormatter(NSE_OPEN, 1)).toBe("Nov");
    expect(tickMarkFormatter(NSE_OPEN, 0)).toBe("2025");
    expect(formatDateTime(NSE_OPEN, "UTC", 0, false)).toBe("Mon 03 Nov '25");
    expect(wallClock(Date.UTC(2025, 10, 3, 18, 30) / 1000, "Asia/Kolkata").day).toBe("04"); // date rolls at IST midnight
  });
});
