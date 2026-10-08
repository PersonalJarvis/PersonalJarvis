import pcmWorkletUrl from "./pcm-worklet.ts?worker&url";

export type PreparedRealtimeAudio = {
  context: AudioContext;
  loaded: Promise<void>;
  prepared: boolean;
};

// Only local code and a suspended output context are prepared. There is no
// microphone, audio graph, peer, ticket or provider session until activation.
let idle: PreparedRealtimeAudio | null = null;
const borrowed = new Set<PreparedRealtimeAudio>();
const retiring = new Set<PreparedRealtimeAudio>();
const owners = new Set<symbol>();
let cancelScheduled: (() => void) | null = null;
let pageHidden = false;

async function close(entry: PreparedRealtimeAudio): Promise<void> {
  try { await entry.context.close(); }
  catch (error) { console.warn("Prepared voice audio could not close", error); }
}

function create(prepared: boolean): PreparedRealtimeAudio {
  const context = new AudioContext({ latencyHint: "interactive" });
  try {
    // Suspend immediately, before loading local DSP code. Never resume an
    // idle context; it must neither render nor hold the output device open.
    const suspended = prepared ? context.suspend() : Promise.resolve();
    const loaded = Promise.all([suspended, context.audioWorklet.addModule(pcmWorkletUrl)])
      .then(() => undefined);
    return { context, loaded, prepared };
  } catch (error) {
    void context.close().catch(closeError => console.warn("Voice audio cleanup failed", closeError));
    throw error;
  }
}

function schedule(): void {
  if (!owners.size || borrowed.size || retiring.size || idle || cancelScheduled || pageHidden ||
      typeof AudioContext !== "function" || typeof AudioWorkletNode !== "function") return;
  const prepare = () => {
    cancelScheduled = null;
    if (!owners.size || borrowed.size || retiring.size || idle || pageHidden) return;
    try {
      const entry = create(true);
      idle = entry;
      void entry.loaded.catch(error => {
        console.warn("Local voice audio preparation failed", error);
        if (idle === entry) { idle = null; void close(entry); }
      });
    } catch (error) { console.warn("Local voice audio preparation failed", error); }
  };
  if (typeof window.requestIdleCallback === "function") {
    const handle = window.requestIdleCallback(prepare, { timeout: 2_000 });
    cancelScheduled = () => window.cancelIdleCallback(handle);
  } else {
    const handle = window.setTimeout(prepare, 500);
    cancelScheduled = () => window.clearTimeout(handle);
  }
}

function discardIdle(): void {
  cancelScheduled?.();
  cancelScheduled = null;
  const entry = idle;
  idle = null;
  if (entry) void close(entry);
}

function onPageHide(): void { pageHidden = true; discardIdle(); }
function onPageShow(): void { pageHidden = false; schedule(); }

/** The selected voice surface owns preparation, including its disposal. */
export function registerRealtimeAudioPreparation(): () => void {
  const owner = Symbol();
  if (!owners.size) {
    pageHidden = false;
    window.addEventListener("pagehide", onPageHide);
    window.addEventListener("pageshow", onPageShow);
  }
  owners.add(owner);
  schedule();
  return () => {
    owners.delete(owner);
    if (!owners.size) {
      discardIdle();
      window.removeEventListener("pagehide", onPageHide);
      window.removeEventListener("pageshow", onPageShow);
    }
  };
}

/** Exclusive transfer: two calls never share one audio context or graph. */
export function acquireRealtimeAudio(): PreparedRealtimeAudio {
  cancelScheduled?.();
  cancelScheduled = null;
  if (idle?.context.state === "closed") idle = null;
  const entry = idle ?? create(false);
  idle = null;
  borrowed.add(entry);
  return entry;
}

export async function releaseRealtimeAudio(entry: PreparedRealtimeAudio): Promise<void> {
  if (!borrowed.delete(entry)) return;
  retiring.add(entry);
  try { await close(entry); }
  finally { retiring.delete(entry); }
  // No session nodes, tracks or captured frames survive into preparation for
  // the next call. Its fresh graph is constructed only after another start.
  schedule();
}

if (import.meta.hot) import.meta.hot.dispose(() => {
  owners.clear();
  discardIdle();
  window.removeEventListener("pagehide", onPageHide);
  window.removeEventListener("pageshow", onPageShow);
});
