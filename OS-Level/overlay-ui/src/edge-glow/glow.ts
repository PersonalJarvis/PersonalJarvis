// glow.ts — State-driven Edge-Glow Setup. Plan §6.1 + §7.4.
//
// Aufgaben:
//   - Inline-SVG-Filter beim Boot in document.body injecten.
//   - data-state-Attribut auf <html> setzen (StateMachine -> CSS-Selectors).
//   - data-debug-Attribut wenn ?debug=1 in der URL.
//   - State-Display-Text fuer Diagnose, gegated via debug-Flag.
//
// Keine Animations-Steuerung hier — die laeuft komplett im CSS
// (@property-Compositor-Animation). noise.ts macht die 12 Hz Modulation.

import glowSvgRaw from "./glow.svg?raw";

import type { StateName } from "../schema";

const ROOT = document.documentElement;

/**
 * Inline-SVG-Filter ins DOM injecten. Plan §7.6 — der Filter muss vor
 * dem ersten Frame da sein, sonst hat `filter: url(#jarvis-glow)` einen
 * Frame lang nichts zum Auflösen.
 */
export function injectGlowFilter(): void {
  if (document.getElementById("jarvis-glow")) return;
  // DOMParser statt innerHTML — Defense-in-Depth gegen Supply-Chain-XSS
  // (auch wenn glowSvgRaw via ?raw aus dem Build kommt). DOMParser markiert
  // nicht-SVG-Inhalt mit einem <parsererror>-Element, das wir abfangen.
  const doc = new DOMParser().parseFromString(glowSvgRaw, "image/svg+xml");
  const svg = doc.documentElement;
  if (svg.getElementsByTagName("parsererror").length > 0) return;
  if (svg.nodeName.toLowerCase() === "svg") {
    document.body.insertAdjacentElement("afterbegin", svg as unknown as Element);
  }
}

/**
 * 5 Layer-Divs in den `.edge-glow` Container haengen. Plan §7.4 —
 * Multi-Layer-Composition mit Phase-Offset (5 Layers, je 200 ms
 * versetzt via animation-delay im CSS).
 */
export function buildLayers(container: HTMLElement): void {
  if (container.children.length > 0) return;
  for (let i = 1; i <= 5; i += 1) {
    const layer = document.createElement("div");
    layer.className = `layer l${i}`;
    container.appendChild(layer);
  }
}

/**
 * Setzt das ``data-state``-Attribut auf `<html>` — CSS reagiert darauf
 * via `:root[data-state="typing"] .edge-glow { ... }`.
 */
export function applyState(state: StateName): void {
  ROOT.dataset["state"] = state;
}

/**
 * Optional: explicit intensity-Modulation. Default 1.0 — Phase 9.5
 * koennte das fuer Activity-Bursts auf 1.2 hochziehen.
 */
export function setIntensity(value: number): void {
  const clamped = Math.max(0, Math.min(2, value));
  ROOT.style.setProperty("--intensity", String(clamped));
}

/**
 * ?debug=1 im URL-Query → State-Display-Marker oben rechts sichtbar.
 * Sonst bleibt #state-display via CSS display:none.
 */
export function applyDebugFlag(): boolean {
  const params = new URLSearchParams(window.location.search);
  const debug = params.get("debug") === "1";
  if (debug) {
    ROOT.dataset["debug"] = "1";
  } else {
    delete ROOT.dataset["debug"];
  }
  return debug;
}

/**
 * Schreibt den aktuellen State + reason in #state-display. Im
 * Production-Pfad (kein ?debug=1) ist das Element via CSS hidden — der
 * Update kostet trotzdem nichts ausser einem dataset-write.
 */
export function setStateDisplay(state: StateName, reason: string): void {
  const el = document.getElementById("state-display");
  if (el === null) return;
  const stateSpan = document.createElement("span");
  stateSpan.className = "state";
  stateSpan.textContent = state;
  if (reason !== "") {
    const reasonSpan = document.createElement("span");
    reasonSpan.className = "reason";
    reasonSpan.textContent = ` (${reason})`;
    el.replaceChildren(stateSpan, reasonSpan);
  } else {
    el.replaceChildren(stateSpan);
  }
  el.dataset["state"] = state;
}
