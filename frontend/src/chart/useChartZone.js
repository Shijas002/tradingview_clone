// React hook: the resolved chart time zone of a pane (or the active pane).
import { useMemo } from "react";
import { useStore } from "../store/useStore.js";
import { resolveTimezone, sourceShiftSeconds } from "./timezone.js";

export function useChartZone(paneId = null) {
  const setting = useStore((s) => s.settings.timezone || "exchange");
  const pane = useStore((s) => s.panes.find((p) => p.id === (paneId || s.activePaneId)) || s.panes[0]);
  const provider = pane ? pane.provider : null;
  const status = useStore((s) => s.providers.find((p) => p.name === provider));
  const shift = sourceShiftSeconds(status);
  return useMemo(() => resolveTimezone(setting, provider, status), [setting, provider, shift]); // eslint-disable-line react-hooks/exhaustive-deps
}
