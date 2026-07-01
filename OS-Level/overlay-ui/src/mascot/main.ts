// Mascot Renderer Entry. Plan §13 + AD-16.
//
// Phase 9.6 DECISION (documented in the commit): PNG fallback only.
// Rationale: Rive files (.riv) are a proprietary binary format that
// can only be created in the Rive editor; it can't be produced
// generatively. Plan AD-16 explicitly allows the fallback.
// A real Rive asset can be supplied later via 'overlay-ui/src/mascot/
// mascot.riv' — the code path below tries to load it first and falls
// back to the PNG if it's not there.
//
// Boot:
//   1. Import the CSS.
//   2. PNG via fallback.png import (Vite hashes the URL).
//   3. Try to load mascot.riv with @rive-app/canvas-lite. If the
//      asset isn't there (404 or decode failure), show the PNG.
//   4. Connect QWebChannel -> set the data-state attribute on <html>.
//      With Rive: additionally trigger state-machine inputs.

import "./mascot.css";

import fallbackPng from "./mascot-fallback.png";

import { connectBridge } from "../bridge";
import type { StateName } from "../schema";

const ROOT = document.documentElement;
// Vite ignores the URL — we explicitly want an optional file lookup at
// runtime. Without @vite-ignore, Vite would emit a build warning
// because the asset doesn't exist yet.
const RIVE_ASSET_URL = new URL(
  /* @vite-ignore */ "./mascot.riv",
  import.meta.url,
).href;

interface RiveLikeRuntime {
  on(event: string, cb: () => void): void;
  stateMachineInputs(name: string): RiveStateMachineInput[] | null;
  cleanup?: () => void;
}

interface RiveStateMachineInput {
  name: string;
  fire?: () => void;
  value?: unknown;
}

let rive: RiveLikeRuntime | null = null;

function showFallback(): void {
  const img = document.getElementById("mascot-fallback") as HTMLImageElement | null;
  const canvas = document.getElementById("mascot-canvas");
  if (img !== null) {
    img.src = fallbackPng;
    img.hidden = false;
  }
  if (canvas !== null) {
    canvas.setAttribute("hidden", "");
  }
}

async function attemptRiveLoad(): Promise<boolean> {
  // Lazy import so @rive-app/canvas-lite doesn't have to end up in the
  // main bundle when the asset is missing.
  let RiveModule: { Rive: new (cfg: Record<string, unknown>) => RiveLikeRuntime; Layout?: unknown };
  try {
    RiveModule = (await import("@rive-app/canvas-lite")) as unknown as typeof RiveModule;
  } catch (err) {
    console.warn("[mascot] @rive-app/canvas-lite import failed", err);
    return false;
  }

  // No HEAD preflight: avoids console network errors on file:// URLs
  // (QtWebEngine production) and potential network leaks on remote URLs.
  // Rive's onLoadError callback (below) is the canonical path for
  // 404 -> PNG fallback.
  const canvas = document.getElementById("mascot-canvas") as HTMLCanvasElement | null;
  if (canvas === null) {
    console.warn("[mascot] canvas#mascot-canvas missing");
    return false;
  }

  return new Promise((resolve) => {
    try {
      const r = new RiveModule.Rive({
        src: RIVE_ASSET_URL,
        canvas,
        autoplay: true,
        stateMachines: "State Machine 1",
        onLoad: () => {
          canvas.removeAttribute("hidden");
          rive = r;
          resolve(true);
        },
        onLoadError: (err: unknown) => {
          console.warn("[mascot] Rive load error", err);
          resolve(false);
        },
      });
    } catch (err) {
      console.warn("[mascot] Rive constructor error", err);
      resolve(false);
    }
  });
}

function applyStateToRive(state: StateName): void {
  if (rive === null) return;
  const inputs = rive.stateMachineInputs("State Machine 1");
  if (inputs === null) return;
  // Plan §13.2: trigger-named input per state.
  const triggerName = state === "typing" || state === "clicking" ? "acting" : state;
  for (const inp of inputs) {
    if (inp.name === triggerName && typeof inp.fire === "function") {
      inp.fire();
      return;
    }
  }
}

async function boot(): Promise<void> {
  console.log("[mascot] booting");
  ROOT.dataset["state"] = "idle";

  const riveLoaded = await attemptRiveLoad();
  if (!riveLoaded) {
    showFallback();
  }

  try {
    const bridge = await connectBridge();
    const initial = await bridge.currentState();
    ROOT.dataset["state"] = initial;
    applyStateToRive(initial);

    bridge.onStateChange(({ next }) => {
      ROOT.dataset["state"] = next;
      applyStateToRive(next);
    });
  } catch (err) {
    console.warn("[mascot] bridge unavailable", err);
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot);
} else {
  void boot();
}
