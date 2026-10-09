import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  acquireRealtimeAudio,
  registerRealtimeAudioPreparation,
  releaseRealtimeAudio,
  type PreparedRealtimeAudio,
} from "./realtimeAudioPreparation";

class LocalAudioContext {
  static instances: LocalAudioContext[] = [];
  static moduleDelayMs = 0;
  state = "running";
  constructor() { LocalAudioContext.instances.push(this); }
  audioWorklet = { addModule: vi.fn(() => new Promise<void>(resolve => {
    setTimeout(resolve, LocalAudioContext.moduleDelayMs);
  })) };
  suspend = vi.fn(async () => { this.state = "suspended"; });
  resume = vi.fn(async () => { this.state = "running"; });
  close = vi.fn(async () => { this.state = "closed"; });
}

const disposers: Array<() => void> = [];
const leases: PreparedRealtimeAudio[] = [];
function register() { const dispose = registerRealtimeAudioPreparation(); disposers.push(dispose); return dispose; }
function acquire() { const entry = acquireRealtimeAudio(); leases.push(entry); return entry; }

beforeEach(() => {
  vi.useFakeTimers();
  LocalAudioContext.instances = [];
  LocalAudioContext.moduleDelayMs = 0;
  vi.stubGlobal("AudioContext", LocalAudioContext);
  vi.stubGlobal("AudioWorkletNode", vi.fn());
  vi.stubGlobal("RTCPeerConnection", vi.fn());
  vi.stubGlobal("fetch", vi.fn());
  vi.stubGlobal("navigator", { mediaDevices: { getUserMedia: vi.fn() } });
});

afterEach(async () => {
  disposers.splice(0).forEach(dispose => dispose());
  await Promise.all(leases.splice(0).map(releaseRealtimeAudio));
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it("prepares a single suspended worklet without microphone, peer or provider requests", async () => {
  register(); register();
  expect(LocalAudioContext.instances).toHaveLength(0);
  await vi.advanceTimersByTimeAsync(501);
  expect(LocalAudioContext.instances).toHaveLength(1);
  const context = LocalAudioContext.instances[0];
  expect(context.state).toBe("suspended");
  expect(context.resume).not.toHaveBeenCalled();
  expect(context.audioWorklet.addModule).toHaveBeenCalledOnce();
  expect(navigator.mediaDevices.getUserMedia).not.toHaveBeenCalled();
  expect(RTCPeerConnection).not.toHaveBeenCalled();
  expect(fetch).not.toHaveBeenCalled();
  expect(acquire().context).toBe(context);
});

it("moves a simulated two-second worklet load entirely before activation", async () => {
  LocalAudioContext.moduleDelayMs = 2_000;
  const coldAt = Date.now();
  const cold = acquire();
  await vi.advanceTimersByTimeAsync(2_000);
  await cold.loaded;
  expect(Date.now() - coldAt).toBe(2_000);
  await releaseRealtimeAudio(cold);
  register();
  await vi.advanceTimersByTimeAsync(2_501);
  const warmAt = Date.now();
  const warm = acquire();
  await warm.loaded;
  expect(warm.prepared).toBe(true);
  expect(Date.now() - warmAt).toBe(0);
  expect(LocalAudioContext.instances).toHaveLength(2);
});

it("cancels StrictMode's first effect and never replenishes during a call", async () => {
  const dispose = register();
  dispose();
  register();
  await vi.advanceTimersByTimeAsync(501);
  const entry = acquire();
  register();
  await vi.advanceTimersByTimeAsync(5_000);
  expect(LocalAudioContext.instances).toHaveLength(1);
  await releaseRealtimeAudio(entry);
  await vi.advanceTimersByTimeAsync(501);
  expect(LocalAudioContext.instances).toHaveLength(2);
  expect(LocalAudioContext.instances[0].close).toHaveBeenCalledOnce();
});

it("transfers an unfinished preparation exclusively without duplicate module loads", async () => {
  LocalAudioContext.moduleDelayMs = 2_000;
  register();
  await vi.advanceTimersByTimeAsync(501);
  const first = acquire();
  const second = acquire();
  expect(first.context).not.toBe(second.context);
  expect(first.prepared).toBe(true);
  expect(second.prepared).toBe(false);
  await vi.advanceTimersByTimeAsync(2_000);
  await Promise.all([first.loaded, second.loaded]);
  expect(LocalAudioContext.instances.every(context => context.audioWorklet.addModule.mock.calls.length === 1)).toBe(true);
});

it("disposes idle contexts on disable and pagehide and prepares again after pageshow", async () => {
  const dispose = register();
  await vi.advanceTimersByTimeAsync(501);
  window.dispatchEvent(new Event("pagehide"));
  expect(LocalAudioContext.instances[0].close).toHaveBeenCalledOnce();
  await vi.advanceTimersByTimeAsync(5_000);
  expect(LocalAudioContext.instances).toHaveLength(1);
  window.dispatchEvent(new Event("pageshow"));
  await vi.advanceTimersByTimeAsync(501);
  expect(LocalAudioContext.instances).toHaveLength(2);
  dispose();
  expect(LocalAudioContext.instances[1].close).toHaveBeenCalledOnce();
});

it("keeps an acquired context alive until its call releases it after unmount", async () => {
  const dispose = register();
  await vi.advanceTimersByTimeAsync(501);
  const entry = acquire();
  dispose();
  expect(LocalAudioContext.instances[0].close).not.toHaveBeenCalled();
  await releaseRealtimeAudio(entry);
  await releaseRealtimeAudio(entry);
  expect(LocalAudioContext.instances[0].close).toHaveBeenCalledOnce();
  await vi.advanceTimersByTimeAsync(5_000);
  expect(LocalAudioContext.instances).toHaveLength(1);
});

it("does not prepare a replacement while native context retirement is stalled", async () => {
  register();
  await vi.advanceTimersByTimeAsync(501);
  const entry = acquire();
  let finishClose!: () => void;
  LocalAudioContext.instances[0].close = vi.fn(() => new Promise<void>(resolve => { finishClose = resolve; }));
  const retiring = releaseRealtimeAudio(entry);
  register();
  await vi.advanceTimersByTimeAsync(10_000);
  expect(LocalAudioContext.instances).toHaveLength(1);
  finishClose();
  await retiring;
  await vi.advanceTimersByTimeAsync(501);
  expect(LocalAudioContext.instances).toHaveLength(2);
});
