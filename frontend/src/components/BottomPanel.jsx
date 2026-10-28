// Resizable bottom panel with tabs (Pine Editor, Strategy Tester, Journal).
import React, { useRef } from "react";
import { useStore } from "../store/useStore.js";
import PineEditor from "./PineEditor.jsx";
import StrategyTester from "./StrategyTester.jsx";
import JournalPanel from "./JournalPanel.jsx";
import { Icon } from "./Icons.jsx";

export const BOTTOM_TABS = [
  { id: "pine", label: "Pine Editor", Comp: PineEditor },
  { id: "tester", label: "Strategy Tester", Comp: StrategyTester },
  { id: "journal", label: "Journal", Comp: JournalPanel },
];

export default function BottomPanel({ extraTabs = [] }) {
  const settings = useStore((s) => s.settings);
  const s = useStore.getState();
  const dragRef = useRef(null);
  const tabs = [...BOTTOM_TABS, ...extraTabs];
  const tab = tabs.find((t) => t.id === settings.bottomTab) || tabs[0];

  const onDragStart = (e) => {
    e.preventDefault();
    const startY = e.clientY;
    const startH = settings.bottomHeight;
    const move = (ev) => {
      const h = Math.max(140, Math.min(window.innerHeight - 220, startH + (startY - ev.clientY)));
      useStore.getState().updateSettings({ bottomHeight: h });
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

  return (
    <section className="bottom-panel" style={{ height: settings.bottomHeight }}>
      <div className="bottom-resize" ref={dragRef} onPointerDown={onDragStart} title="Drag to resize" />
      <div className="bottom-tabs">
        {tabs.map((t) => (
          <button key={t.id} className={tab.id === t.id ? "active" : ""} onClick={() => s.updateSettings({ bottomTab: t.id })}>
            {t.label}
          </button>
        ))}
        <span className="flex1" />
        <button className="icon-btn" title="Close panel" onClick={() => s.updateSettings({ bottomPanel: false })}>
          <Icon.close />
        </button>
      </div>
      <div className="bottom-content">
        {tabs.map((t) => (
          <div key={t.id} className="bottom-tab-body" style={{ display: t.id === tab.id ? "flex" : "none" }}>
            <t.Comp />
          </div>
        ))}
      </div>
    </section>
  );
}
