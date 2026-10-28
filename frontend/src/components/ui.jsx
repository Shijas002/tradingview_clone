// Small reusable UI building blocks: Dropdown menu, Modal dialog, icon buttons.
import React, { useEffect, useRef, useState } from "react";
import { Icon } from "./Icons.jsx";

export function useOutsideClick(ref, onOutside, active = true) {
  useEffect(() => {
    if (!active) return undefined;
    const h = (e) => {
      if (ref.current && !ref.current.contains(e.target)) onOutside();
    };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, [ref, onOutside, active]);
}

export function Dropdown({ label, title, children, className = "", align = "left", buttonClass = "tb-btn" }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useOutsideClick(ref, () => setOpen(false), open);
  return (
    <div className={`dropdown ${className}`} ref={ref}>
      <button className={`${buttonClass} ${open ? "active" : ""}`} title={title} onClick={() => setOpen((o) => !o)}>
        {label}
        <span className="caret">▾</span>
      </button>
      {open && (
        <div className={`dropdown-menu align-${align}`} onClick={(e) => e.target.closest("[data-close]") && setOpen(false)}>
          {typeof children === "function" ? children(() => setOpen(false)) : children}
        </div>
      )}
    </div>
  );
}

export function MenuItem({ children, onClick, active, danger, hint, disabled }) {
  return (
    <button data-close className={`menu-item ${active ? "active" : ""} ${danger ? "danger" : ""}`} onClick={onClick} disabled={disabled}>
      <span>{children}</span>
      {hint && <span className="menu-hint">{hint}</span>}
    </button>
  );
}

export function Modal({ title, onClose, children, width = 520, footer, className = "" }) {
  useEffect(() => {
    const h = (e) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    window.addEventListener("keydown", h, true);
    return () => window.removeEventListener("keydown", h, true);
  }, [onClose]);
  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`modal ${className}`} style={{ width }} role="dialog" aria-label={title}>
        <div className="modal-header">
          <span>{title}</span>
          <button className="icon-btn" onClick={onClose} title="Close">
            <Icon.close />
          </button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-footer">{footer}</div>}
      </div>
    </div>
  );
}

export function IconButton({ icon, title, onClick, active, className = "", disabled }) {
  const I = Icon[icon];
  return (
    <button className={`icon-btn ${active ? "active" : ""} ${className}`} title={title} onClick={onClick} disabled={disabled}>
      {I ? <I /> : icon}
    </button>
  );
}

/** Convert Pine "#RRGGBBAA" to "#RRGGBB" for <input type=color>. */
export function toHex6(c) {
  if (!c || typeof c !== "string") return "#2962ff";
  if (c.startsWith("#") && (c.length === 9 || c.length === 7)) return c.slice(0, 7).toLowerCase();
  const m = c.match(/rgba?\(([^)]+)\)/);
  if (m) {
    const [r, g, b] = m[1].split(",").map((x) => parseInt(x, 10));
    return "#" + [r, g, b].map((v) => v.toString(16).padStart(2, "0")).join("");
  }
  return "#2962ff";
}
