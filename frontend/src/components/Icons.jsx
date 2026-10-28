// Inline SVG icons (no external icon font, works offline).
import React from "react";

const P = { fill: "none", stroke: "currentColor", strokeWidth: 1.4, strokeLinecap: "round", strokeLinejoin: "round" };

export const Icon = {
  cursor: () => (
    <svg viewBox="0 0 28 28" width="28" height="28"><path {...P} d="M9 6l11 8-5 1 3 6-2 1-3-6-4 3z" /></svg>
  ),
  trendline: () => (
    <svg viewBox="0 0 28 28" width="28" height="28"><path {...P} d="M6 21L22 7" /><circle {...P} cx="6" cy="21" r="2" /><circle {...P} cx="22" cy="7" r="2" /></svg>
  ),
  ray: () => (
    <svg viewBox="0 0 28 28" width="28" height="28"><path {...P} d="M7 20L24 8" /><circle {...P} cx="7" cy="20" r="2" /></svg>
  ),
  extended: () => (
    <svg viewBox="0 0 28 28" width="28" height="28"><path {...P} d="M3 23L25 5" /><circle {...P} cx="10" cy="17" r="2" /><circle {...P} cx="18" cy="11" r="2" /></svg>
  ),
  arrow: () => (
    <svg viewBox="0 0 28 28" width="28" height="28"><path {...P} d="M6 21L21 8M21 8h-6M21 8v6" /></svg>
  ),
  hline: () => (
    <svg viewBox="0 0 28 28" width="28" height="28"><path {...P} d="M4 14h20" /><circle {...P} cx="14" cy="14" r="2" /></svg>
  ),
  hray: () => (
    <svg viewBox="0 0 28 28" width="28" height="28"><path {...P} d="M9 14h16" /><circle {...P} cx="7" cy="14" r="2" /></svg>
  ),
  vline: () => (
    <svg viewBox="0 0 28 28" width="28" height="28"><path {...P} d="M14 4v20" /><circle {...P} cx="14" cy="14" r="2" /></svg>
  ),
  cross: () => (
    <svg viewBox="0 0 28 28" width="28" height="28"><path {...P} d="M14 4v20M4 14h20" /></svg>
  ),
  channel: () => (
    <svg viewBox="0 0 28 28" width="28" height="28"><path {...P} d="M4 18L20 6M8 24L24 12" /></svg>
  ),
  fib: () => (
    <svg viewBox="0 0 28 28" width="28" height="28"><path {...P} d="M4 6h20M4 11h20M4 15h20M4 19h20M4 23h20" /></svg>
  ),
  rect: () => (
    <svg viewBox="0 0 28 28" width="28" height="28"><rect {...P} x="6" y="8" width="16" height="12" /></svg>
  ),
  brush: () => (
    <svg viewBox="0 0 28 28" width="28" height="28"><path {...P} d="M5 20c3-6 6 2 9-4s5-6 9-8" /></svg>
  ),
  text: () => (
    <svg viewBox="0 0 28 28" width="28" height="28"><path {...P} d="M8 8h12M14 8v13M11 21h6" /></svg>
  ),
  pricerange: () => (
    <svg viewBox="0 0 28 28" width="28" height="28"><path {...P} d="M6 6h16M6 22h16M14 8v12M11 11l3-3 3 3M11 17l3 3 3-3" /></svg>
  ),
  long: () => (
    <svg viewBox="0 0 28 28" width="28" height="28"><rect {...P} x="5" y="6" width="18" height="8" stroke="#089981" /><rect {...P} x="5" y="14" width="18" height="7" stroke="#F23645" /></svg>
  ),
  short: () => (
    <svg viewBox="0 0 28 28" width="28" height="28"><rect {...P} x="5" y="6" width="18" height="7" stroke="#F23645" /><rect {...P} x="5" y="13" width="18" height="8" stroke="#089981" /></svg>
  ),
  magnet: () => (
    <svg viewBox="0 0 28 28" width="28" height="28"><path {...P} d="M8 6v8a6 6 0 0012 0V6h-4v8a2 2 0 01-4 0V6z" /></svg>
  ),
  lock: () => (
    <svg viewBox="0 0 28 28" width="28" height="28"><rect {...P} x="8" y="13" width="12" height="9" rx="1" /><path {...P} d="M10 13v-3a4 4 0 018 0v3" /></svg>
  ),
  eye: () => (
    <svg viewBox="0 0 28 28" width="28" height="28"><path {...P} d="M4 14s4-6 10-6 10 6 10 6-4 6-10 6S4 14 4 14z" /><circle {...P} cx="14" cy="14" r="3" /></svg>
  ),
  eyeOff: () => (
    <svg viewBox="0 0 28 28" width="28" height="28"><path {...P} d="M4 14s4-6 10-6 10 6 10 6-4 6-10 6S4 14 4 14z" /><path {...P} d="M6 22L22 6" /></svg>
  ),
  trash: () => (
    <svg viewBox="0 0 28 28" width="28" height="28"><path {...P} d="M7 9h14M11 9V7h6v2M9 9l1 13h8l1-13" /></svg>
  ),
  gear: () => (
    <svg viewBox="0 0 28 28" width="28" height="28"><circle {...P} cx="14" cy="14" r="3" /><path {...P} d="M14 5v3M14 20v3M5 14h3M20 14h3M7.6 7.6l2.1 2.1M18.3 18.3l2.1 2.1M7.6 20.4l2.1-2.1M18.3 9.7l2.1-2.1" /></svg>
  ),
  close: () => (
    <svg viewBox="0 0 28 28" width="28" height="28"><path {...P} d="M8 8l12 12M20 8L8 20" /></svg>
  ),
  camera: () => (
    <svg viewBox="0 0 28 28" width="28" height="28"><path {...P} d="M5 10h4l2-3h6l2 3h4v11H5z" /><circle {...P} cx="14" cy="15" r="3.5" /></svg>
  ),
  undo: () => (
    <svg viewBox="0 0 28 28" width="28" height="28"><path {...P} d="M9 9H17a5 5 0 010 10h-6M9 9l3-3M9 9l3 3" /></svg>
  ),
  redo: () => (
    <svg viewBox="0 0 28 28" width="28" height="28"><path {...P} d="M19 9H11a5 5 0 000 10h6M19 9l-3-3M19 9l-3 3" /></svg>
  ),
  fx: () => (
    <svg viewBox="0 0 28 28" width="28" height="28"><path {...P} d="M11 22c2 0 2-2 2.5-5l1.5-9c.5-2 1-3 3-3M10 12h7M17 15l5 6M22 15l-5 6" /></svg>
  ),
  layout: () => (
    <svg viewBox="0 0 28 28" width="28" height="28"><rect {...P} x="5" y="6" width="18" height="16" /><path {...P} d="M14 6v16" /></svg>
  ),
  panelRight: () => (
    <svg viewBox="0 0 28 28" width="28" height="28"><rect {...P} x="5" y="6" width="18" height="16" /><path {...P} d="M17 6v16" /></svg>
  ),
  panelBottom: () => (
    <svg viewBox="0 0 28 28" width="28" height="28"><rect {...P} x="5" y="6" width="18" height="16" /><path {...P} d="M5 16h18" /></svg>
  ),
  plus: () => (
    <svg viewBox="0 0 28 28" width="28" height="28"><path {...P} d="M14 7v14M7 14h14" /></svg>
  ),
  search: () => (
    <svg viewBox="0 0 28 28" width="28" height="28"><circle {...P} cx="12" cy="12" r="6" /><path {...P} d="M17 17l5 5" /></svg>
  ),
  code: () => (
    <svg viewBox="0 0 28 28" width="28" height="28"><path {...P} d="M10 9l-5 5 5 5M18 9l5 5-5 5M16 7l-4 14" /></svg>
  ),
  copy: () => (
    <svg viewBox="0 0 28 28" width="28" height="28"><rect {...P} x="9" y="9" width="12" height="13" rx="1" /><path {...P} d="M7 18V6h11" /></svg>
  ),
  candles: () => (
    <svg viewBox="0 0 28 28" width="28" height="28"><path {...P} d="M9 5v4M9 19v4M19 7v3M19 18v3" /><rect {...P} x="7" y="9" width="4" height="10" /><rect {...P} x="17" y="10" width="4" height="8" /></svg>
  ),
  bell: () => (
    <svg viewBox="0 0 28 28" width="28" height="28"><path {...P} d="M9 19V13a5 5 0 0110 0v6l2 2H7zM12 23h4" /></svg>
  ),
  star: () => (
    <svg viewBox="0 0 28 28" width="28" height="28"><path {...P} d="M14 5l2.8 5.7 6.2.9-4.5 4.4 1 6.2L14 19.3 8.5 22.2l1-6.2L5 11.6l6.2-.9z" /></svg>
  ),
};
