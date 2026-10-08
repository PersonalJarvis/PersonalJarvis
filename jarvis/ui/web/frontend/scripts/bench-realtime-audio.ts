/** Run via Vite's /scripts/bench-realtime-audio.html in a real visible browser. */
import {
  acquireRealtimeAudio,
  registerRealtimeAudioPreparation,
  releaseRealtimeAudio,
  type PreparedRealtimeAudio,
} from "../src/lib/realtimeAudioPreparation";

const cold = document.querySelector<HTMLButtonElement>("#cold")!;
const prepare = document.querySelector<HTMLButtonElement>("#prepare")!;
const warm = document.querySelector<HTMLButtonElement>("#warm")!;
const status = document.querySelector<HTMLElement>("#status")!;
const results = document.querySelector<HTMLElement>("#results")!;
const samples: Record<string, number | string | boolean>[] = [];
let held: PreparedRealtimeAudio | null = null;
let unregister: (() => void) | null = null;

function buttons(busy: boolean): void {
  cold.disabled = prepare.disabled = busy;
  warm.disabled = busy || !held;
}

async function dispose(): Promise<void> {
  unregister?.();
  unregister = null;
  const entry = held;
  held = null;
  if (entry) await releaseRealtimeAudio(entry);
}

async function run(prepared: boolean): Promise<void> {
  buttons(true);
  let entry: PreparedRealtimeAudio | null = null;
  let source: ConstantSourceNode | null = null;
  let capture: AudioWorkletNode | null = null;
  let sink: GainNode | null = null;
  try {
    if (!prepared) await dispose();
    const started = performance.now();
    entry = held ?? acquireRealtimeAudio();
    held = null;
    const context = entry.context;
    const sample: Record<string, number | string | boolean> = {
      mode: prepared ? "prepared" : "cold",
      used_prepared_context: entry.prepared,
      state_before_activation: context.state,
      acquire_ms: performance.now() - started,
    };
    await entry.loaded;
    sample.module_ready_ms = performance.now() - started;
    await context.resume();
    sample.running_ms = performance.now() - started;
    capture = new AudioWorkletNode(context, "pcm-capture");
    source = context.createConstantSource();
    source.offset.value = 0.01;
    sink = context.createGain();
    sink.gain.value = 0;
    source.connect(capture);
    capture.connect(sink);
    sink.connect(context.destination);
    const rendered = new Promise<void>((resolve, reject) => {
      const timer = window.setTimeout(() => reject(new Error("No rendered capture packet")), 5000);
      capture!.port.onmessage = (event: MessageEvent) => {
        if (!(event.data instanceof ArrayBuffer)) return;
        window.clearTimeout(timer);
        resolve();
      };
    });
    source.start();
    await rendered;
    sample.first_rendered_packet_ms = performance.now() - started;
    sample.remote_resource_requests = performance.getEntriesByType("resource")
      .filter(resource => new URL(resource.name, location.href).origin !== location.origin).length;
    samples.push(sample);
    results.textContent = JSON.stringify(samples, null, 2);
    status.textContent = "Measured a real local audio render packet; no microphone was opened.";
  } catch (error) {
    status.textContent = `Measurement failed: ${String(error)}`;
  } finally {
    source?.stop();
    source?.disconnect();
    capture?.disconnect();
    sink?.disconnect();
    unregister?.();
    unregister = null;
    if (entry) await releaseRealtimeAudio(entry);
    buttons(false);
  }
}

cold.addEventListener("click", () => void run(false));
warm.addEventListener("click", () => void run(true));
prepare.addEventListener("click", async () => {
  buttons(true);
  try {
    await dispose();
    unregister = registerRealtimeAudioPreparation();
    // The helper's idle callback is queued first. Acquiring afterward exposes
    // whether it really prepared a context; an unsupported scheduler fails the
    // assertion rather than being called a warm result.
    await new Promise<void>(resolve => {
      if (window.requestIdleCallback) window.requestIdleCallback(() => resolve(), { timeout: 2500 });
      else window.setTimeout(resolve, 750);
    });
    held = acquireRealtimeAudio();
    await held.loaded;
    if (!held.prepared || held.context.state !== "suspended") {
      throw new Error("Preparation did not produce a suspended context");
    }
    status.textContent = "Prepared: context suspended, local worklet loaded, microphone unopened.";
  } catch (error) {
    status.textContent = `Preparation failed: ${String(error)}`;
    await dispose();
  } finally { buttons(false); }
});
window.addEventListener("pagehide", () => { void dispose(); });
