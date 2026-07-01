// Edge-Glow Renderer Entry — Phase 9.5.
//
// Boot sequence:
//   1. Import the CSS (Vite injects the style tag).
//   2. Inject the SVG filter into the DOM (before the first frame!).
//   3. Append 5 layer divs into the .edge-glow container.
//   4. Set the ?debug=1 flag on <html>.
//   5. Build the effect surfaces (ripple pool, cursor-trail canvas,
//      typing sweep).
//   6. Start the noise runner (12 Hz, except with prefers-reduced-motion).
//   7. Connect the QWebChannel bridge.
//      - State -> data-state attribute on <html>.
//      - Click -> ripple.triggerRipple.
//      - Cursor -> cursor-trail.pushCursorPoint.
//      - Action started/ended (kind=typing) -> typing-sweep.scheduleSweepBurst.
//
// Glow activation happens entirely via data-state attributes on
// <html> — see edge-glow.css. JS only pushes state strings, no
// imperative animation driving.

import "./edge-glow.css";

import { connectBridge } from "../bridge";
import { isGlowActive } from "../schema";
import { clearCursorTrail, initCursorTrail, pushCursorPoint } from "./cursor-trail";
import {
  applyDebugFlag,
  applyState,
  buildLayers,
  injectGlowFilter,
  setStateDisplay,
} from "./glow";
import { startNoise } from "./noise";
import { buildRipplePool, triggerRipple } from "./ripple";
import {
  applySweepBaseline,
  buildTypingSweep,
  cancelSweepBurst,
  scheduleSweepBurst,
} from "./typing-sweep";

function bootEnv(): { debug: boolean; reducedMotion: boolean } {
  injectGlowFilter();
  const container = document.querySelector<HTMLElement>(".edge-glow");
  if (container !== null) {
    buildLayers(container);
  } else {
    console.warn("[edge-glow] .edge-glow container missing in HTML");
  }
  buildRipplePool();
  buildTypingSweep();
  initCursorTrail();
  const debug = applyDebugFlag();
  const reducedMotion =
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  return { debug, reducedMotion };
}

const TYPING_KINDS = new Set(["type", "hotkey"]);

async function boot(): Promise<void> {
  const env = bootEnv();
  console.log(
    `[edge-glow] booting (debug=${env.debug}, reduced-motion=${env.reducedMotion})`,
  );

  // Idle immediately as the default — matches StateMachine.IDLE's initial.
  applyState("idle");
  setStateDisplay("idle", "");

  // 12 Hz simplex noise — startNoise no-ops with reduced motion.
  const noiseRunner = startNoise();

  // Power saving: when the WebView is hidden (e.g. overlay off, workspace
  // switch), we stop the 12-Hz setInterval tick. Otherwise it keeps running
  // for the whole WebView lifetime and writes CSS custom properties even in
  // the hidden state (style-recalc cost with no visible effect). stop() is
  // idempotent + no-ops if it was never started (reduced-motion path).
  // Restarting on visible is out of scope — becoming visible means a
  // page reload.
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      noiseRunner.stop();
    }
  });

  try {
    const bridge = await connectBridge();
    const initial = await bridge.currentState();
    console.log("[edge-glow] initial state", initial);
    applyState(initial);
    setStateDisplay(initial, "");

    bridge.onStateChange(({ old, next, reason }) => {
      const willGlow = isGlowActive(next);
      console.log(
        `[edge-glow] ${old} -> ${next} (${reason || "n/a"}) glow=${willGlow}`,
      );
      applyState(next);
      setStateDisplay(next, reason);
    });

    // Phase 9.5 Effekte — alle drei Bridge-Hooks.
    bridge.onClickEvent(({ x, y }) => {
      triggerRipple(x, y);
    });

    bridge.onCursorMoved((x, y) => {
      pushCursorPoint(x, y);
    });

    bridge.onActionStarted((kind, durationHintMs) => {
      if (TYPING_KINDS.has(kind)) {
        applySweepBaseline(true);
        if (durationHintMs > 0) {
          scheduleSweepBurst(durationHintMs);
        } else {
          // No hint -> a single sweep at the start.
          scheduleSweepBurst(0);
        }
      }
    });

    bridge.onActionEnded(() => {
      cancelSweepBurst();
      applySweepBaseline(false);
      clearCursorTrail();
    });
  } catch (err) {
    // Standalone Vite dev or a QWebChannel boot race: idle stays set
    // (see above), we only update the diagnostic tag.
    console.warn("[edge-glow] bridge unavailable", err);
    setStateDisplay("idle", "no-bridge");
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot);
} else {
  void boot();
}
