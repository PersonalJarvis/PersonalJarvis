// glow.ts — State-driven Edge-Glow setup. Plan §6.1 + §7.4.
//
// Responsibilities:
//   - Inject the inline SVG filter into document.body at boot.
//   - Set the data-state attribute on <html> (state machine -> CSS selectors).
//   - Set the data-debug attribute when ?debug=1 is in the URL.
//   - State-display text for diagnostics, gated by the debug flag.
//
// No animation control here — that all runs in CSS
// (@property compositor animation). noise.ts does the 12 Hz modulation.

import glowSvgRaw from "./glow.svg?raw";

import type { StateName } from "../schema";

const ROOT = document.documentElement;

/**
 * Injects the inline SVG filter into the DOM. Plan §7.6 — the filter
 * must be there before the first frame, otherwise `filter:
 * url(#jarvis-glow)` has nothing to resolve for one frame.
 */
export function injectGlowFilter(): void {
  if (document.getElementById("jarvis-glow")) return;
  // DOMParser instead of innerHTML — defense in depth against
  // supply-chain XSS (even though glowSvgRaw comes from the build via
  // ?raw). DOMParser marks non-SVG content with a <parsererror>
  // element, which we catch.
  const doc = new DOMParser().parseFromString(glowSvgRaw, "image/svg+xml");
  const svg = doc.documentElement;
  if (svg.getElementsByTagName("parsererror").length > 0) return;
  if (svg.nodeName.toLowerCase() === "svg") {
    document.body.insertAdjacentElement("afterbegin", svg as unknown as Element);
  }
}

/**
 * Appends 5 layer divs into the `.edge-glow` container. Plan §7.4 —
 * multi-layer composition with a phase offset (5 layers, each staggered
 * by 200 ms via animation-delay in the CSS).
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
 * Sets the ``data-state`` attribute on `<html>` — CSS reacts to it
 * via `:root[data-state="typing"] .edge-glow { ... }`.
 */
export function applyState(state: StateName): void {
  ROOT.dataset["state"] = state;
}

/**
 * Optional: explicit intensity modulation. Default 1.0 — Phase 9.5
 * might bump this to 1.2 for activity bursts.
 */
export function setIntensity(value: number): void {
  const clamped = Math.max(0, Math.min(2, value));
  ROOT.style.setProperty("--intensity", String(clamped));
}

/**
 * ?debug=1 in the URL query → the state-display marker in the top
 * right becomes visible. Otherwise #state-display stays hidden via
 * CSS display:none.
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
 * Writes the current state + reason into #state-display. On the
 * production path (no ?debug=1) the element is hidden via CSS — the
 * update still costs nothing besides a dataset write.
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
