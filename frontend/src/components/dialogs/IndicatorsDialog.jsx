// "Indicators & Strategies" picker: built-in library (Pine) + the user's own scripts.
import React, { useEffect, useMemo, useRef, useState } from "react";
import { BUILTIN_INDICATORS } from "../../pine/builtins.js";
import { useStore } from "../../store/useStore.js";
import { Modal } from "../ui.jsx";
import { Icon } from "../Icons.jsx";

const CATEGORIES = ["All", "Trend", "Oscillators", "Volatility", "Volume", "Strategies", "My scripts"];

export default function IndicatorsDialog({ dialog }) {
  const scripts = useStore((s) => s.scripts);
  const panes = useStore((s) => s.panes);
  const pane = panes.find((p) => p.id === dialog.paneId) || panes[0];
  const [cat, setCat] = useState("All");
  const [q, setQ] = useState("");
  const [added, setAdded] = useState(null);
  const inputRef = useRef(null);
  const s = useStore.getState();

  useEffect(() => inputRef.current && inputRef.current.focus(), []);

  const items = useMemo(() => {
    const mine = scripts.map((sc) => ({ id: sc.id, name: sc.name, category: "My scripts", source: sc.source, script: true }));
    const all = [...BUILTIN_INDICATORS, ...mine];
    const qq = q.trim().toLowerCase();
    return all.filter((i) => (cat === "All" || i.category === cat) && (!qq || i.name.toLowerCase().includes(qq) || i.id.toLowerCase().includes(qq)));
  }, [scripts, cat, q]);

  const add = (item) => {
    s.addIndicator(pane.id, {
      name: item.name,
      source: item.source,
      scriptId: item.script ? item.id : null,
      builtinId: item.builtin ? item.id : null,
    });
    setAdded(item.name);
    setTimeout(() => setAdded(null), 1500);
  };

  return (
    <Modal title="Indicators, metrics & strategies" onClose={() => s.closeDialog()} width={720} className="ind-dialog">
      <div className="search-box">
        <Icon.search />
        <input ref={inputRef} value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search" onKeyDown={(e) => e.key === "Enter" && items[0] && add(items[0])} />
      </div>
      <div className="ind-body">
        <div className="ind-cats">
          {CATEGORIES.map((c) => (
            <button key={c} className={cat === c ? "active" : ""} onClick={() => setCat(c)}>
              {c}
            </button>
          ))}
        </div>
        <div className="ind-list">
          {items.map((it) => (
            <button key={(it.script ? "s:" : "b:") + it.id} className="ind-row" onClick={() => add(it)} title="Add to chart">
              <span>{it.name}</span>
              <span className="ind-cat">{it.script ? "my script" : it.category}</span>
            </button>
          ))}
          {!items.length && (
            <div className="empty">
              {cat === "My scripts" ? "No saved scripts yet — write one in the Pine Editor (bottom panel) and click Save." : "Nothing matches your search."}
            </div>
          )}
        </div>
      </div>
      {added && <div className="ind-added">Added {added}</div>}
    </Modal>
  );
}
