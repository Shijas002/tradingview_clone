// Edit the text of a text drawing.
import React, { useEffect, useRef, useState } from "react";
import { symbolKey, useStore } from "../../store/useStore.js";
import { Modal, toHex6 } from "../ui.jsx";

export default function TextDialog({ dialog }) {
  const pane = useStore((s) => s.panes.find((p) => p.id === dialog.paneId));
  const key = pane && pane.provider ? symbolKey(pane.provider, pane.symbol) : null;
  const drawing = useStore((s) => (key ? (s.drawingsBySymbol[key] || []).find((d) => d.id === dialog.drawingId) : null));
  const [text, setText] = useState(drawing ? drawing.style.text || "" : "");
  const [color, setColor] = useState(drawing ? toHex6(drawing.style.color) : "#d1d4dc");
  const [size, setSize] = useState(drawing ? drawing.style.fontSize || 14 : 14);
  const ref = useRef(null);
  const s = useStore.getState();
  useEffect(() => {
    if (ref.current) {
      ref.current.focus();
      ref.current.select();
    }
  }, []);
  if (!drawing) return null;

  const save = () => {
    const list = (useStore.getState().drawingsBySymbol[key] || []).map((d) => (d.id === drawing.id ? { ...d, style: { ...d.style, text, color, fontSize: size } } : d));
    s.setDrawings(key, list);
    s.closeDialog();
  };

  return (
    <Modal
      title="Text"
      onClose={() => s.closeDialog()}
      width={380}
      footer={
        <>
          <button className="btn" onClick={() => s.closeDialog()}>
            Cancel
          </button>
          <button className="btn primary" onClick={save}>
            Ok
          </button>
        </>
      }
    >
      <textarea ref={ref} className="text-edit" rows={4} value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === "Enter" && (e.ctrlKey || e.metaKey) && save()} />
      <div className="form-grid">
        <label className="form-row">
          <span>Color</span>
          <input type="color" value={color} onChange={(e) => setColor(e.target.value)} />
        </label>
        <label className="form-row">
          <span>Font size</span>
          <select value={size} onChange={(e) => setSize(Number(e.target.value))}>
            {[10, 12, 14, 16, 20, 24, 32, 40].map((f) => (
              <option key={f} value={f}>
                {f}
              </option>
            ))}
          </select>
        </label>
      </div>
    </Modal>
  );
}
