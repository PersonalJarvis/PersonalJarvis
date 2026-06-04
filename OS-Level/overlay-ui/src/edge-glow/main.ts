// Edge-Glow Renderer Entry — Phase 9.5.
//
// Boot-Sequenz:
//   1. CSS importieren (Vite injected den Style-Tag).
//   2. SVG-Filter ins DOM injecten (vor dem ersten Frame!).
//   3. 5 Layer-Divs in den .edge-glow Container haengen.
//   4. ?debug=1 Flag auf <html> setzen.
//   5. Effekt-Surfaces aufbauen (ripple-pool, cursor-trail-canvas,
//      typing-sweep).
//   6. Noise-Runner starten (12 Hz, ausser bei prefers-reduced-motion).
//   7. QWebChannel-Bridge connecten.
//      - State -> data-state Attribut auf <html>.
//      - Click -> ripple.triggerRipple.
//      - Cursor -> cursor-trail.pushCursorPoint.
//      - Action started/ended (kind=typing) -> typing-sweep.scheduleSweepBurst.
//
// Glow-Aktivierung erfolgt komplett ueber data-state-Attribute auf
// <html> — siehe edge-glow.css. JS pusht nur State-Strings, kein
// imperatives Animation-Driving.

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

  // Sofort idle als Default — matched die StateMachine.IDLE-Initial.
  applyState("idle");
  setStateDisplay("idle", "");

  // 12 Hz Simplex-Noise — startNoise no-op'd bei reduced-motion.
  const noiseRunner = startNoise();

  // Power-Saving: Wenn die WebView versteckt wird (z.B. Overlay aus, Workspace-
  // Wechsel), stoppen wir den 12-Hz-setInterval-Tick. Sonst laeuft er die
  // gesamte WebView-Lifetime und schreibt CSS-Custom-Properties auch im
  // hidden-State (Style-Recalc-Kosten ohne sichtbaren Effekt). stop() ist
  // idempotent + macht no-op wenn nicht gestartet (reduced-motion-Pfad).
  // Re-start bei visible ist nicht in scope — sichtbar werden bedeutet
  // Page-Reload.
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
          // Kein Hint -> einzelner Sweep beim Start.
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
    // Standalone-Vite-Dev oder QWebChannel-Boot-Race: idle bleibt
    // gesetzt (siehe oben), wir aktualisieren nur das Diagnose-Tag.
    console.warn("[edge-glow] bridge unavailable", err);
    setStateDisplay("idle", "no-bridge");
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot);
} else {
  void boot();
}
