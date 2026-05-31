// Mascot Renderer Entry. Plan §13 + AD-16.
//
// Phase 9.6 ENTSCHEIDUNG (im Commit dokumentiert): PNG-Fallback only.
// Begruendung: Rive-Files (.riv) sind ein proprietaeres Binary-Format
// das nur im Rive-Editor erstellt werden kann; ich kann das nicht
// generativ produzieren. Plan AD-16 erlaubt explizit den Fallback.
// Ein echtes Rive-Asset kann spaeter via 'overlay-ui/src/mascot/
// mascot.riv' nachgereicht werden — der Code-Pfad unten lade-versucht
// es zuerst und faellt auf das PNG zurueck wenn nicht da.
//
// Boot:
//   1. CSS importieren.
//   2. PNG via fallback.png import (Vite hashed das URL).
//   3. Versuche mascot.riv zu laden mit @rive-app/canvas-lite. Wenn
//      das Asset nicht da ist (404 oder Decode-Fail), zeige PNG.
//   4. QWebChannel connecten -> data-state Attribut auf <html> setzen.
//      Bei Rive: zusaetzlich State-Machine-Inputs triggern.

import "./mascot.css";

import fallbackPng from "./mascot-fallback.png";

import { connectBridge } from "../bridge";
import type { StateName } from "../schema";

const ROOT = document.documentElement;
// Vite ignoriert das URL — wir wollen explizit Optional-File-Lookup
// zur Laufzeit. Ohne @vite-ignore wuerde Vite eine Build-Warning
// emittieren weil das Asset noch nicht existiert.
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
  // Lazy-import damit @rive-app/canvas-lite nicht in den Main-Bundle
  // muss wenn das Asset fehlt.
  let RiveModule: { Rive: new (cfg: Record<string, unknown>) => RiveLikeRuntime; Layout?: unknown };
  try {
    RiveModule = (await import("@rive-app/canvas-lite")) as unknown as typeof RiveModule;
  } catch (err) {
    console.warn("[mascot] @rive-app/canvas-lite import failed", err);
    return false;
  }

  // Kein HEAD-Preflight: vermeidet Console-Network-Errors bei file://-URLs
  // (QtWebEngine-Production) und potenzielle Network-Leaks bei remote URLs.
  // Rive's onLoadError Callback (unten) ist der canonical Path fuer 404 ->
  // PNG-Fallback.
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
  // Plan §13.2: trigger-named-Input je State.
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
