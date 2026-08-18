/**
 * Tiny, dependency-free UI sounds: the JarvisDock "mission captured" chime and
 * the deck dock's detent tick. WebAudio is a browser built-in, so this works on
 * any cloud-first client and adds nothing to the bundle.
 *
 * Brief from the product: quiet and smooth. The chime is two soft sine voices
 * a gentle interval apart, a low peak gain, a short attack and a smooth
 * exponential release — a "tding", never a beep. The tick is a sub-40 ms
 * detent, the sound a picker wheel makes as it passes a notch.
 *
 * Every call is safe from any event handler: it is a no-op (never throws) when
 * WebAudio is unavailable (headless / jsdom) or the user muted UI sounds via
 * the `jarvis.ui.sound` localStorage flag (`"off"` = muted; absent = audible).
 */

const SOUND_PREF_KEY = "jarvis.ui.sound";

type AudioCtor = new () => AudioContext;

// One shared context, lazily created — browsers cap concurrent AudioContexts.
let sharedCtx: AudioContext | null = null;

function soundEnabled(): boolean {
  try {
    return localStorage.getItem(SOUND_PREF_KEY) !== "off";
  } catch {
    return true; // private mode / SSR — still guarded by AudioContext presence
  }
}

function audioCtor(): AudioCtor | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as {
    AudioContext?: AudioCtor;
    webkitAudioContext?: AudioCtor;
  };
  return w.AudioContext ?? w.webkitAudioContext ?? null;
}

function getCtx(): AudioContext | null {
  if (sharedCtx) return sharedCtx;
  const Ctor = audioCtor();
  if (!Ctor) return null;
  try {
    sharedCtx = new Ctor();
  } catch {
    return null;
  }
  return sharedCtx;
}

/**
 * Has the page seen a user gesture yet? Browsers refuse to start audio before
 * one, and a context created too early logs a warning and stays suspended.
 * Unknown (older engines, jsdom) counts as yes — the play paths still guard
 * against a suspended context.
 */
function hadUserGesture(): boolean {
  if (typeof navigator === "undefined") return true;
  const ua = (navigator as unknown as { userActivation?: { hasBeenActive?: boolean } })
    .userActivation;
  return ua?.hasBeenActive !== false;
}

/**
 * Play the soft "mission captured" confirmation chime. No-op when WebAudio is
 * unavailable or UI sound is muted; any audio quirk is swallowed so it can
 * never break the drop interaction it accompanies.
 */
export function playDropConfirm(): void {
  if (!soundEnabled()) return;
  const ctx = getCtx();
  if (!ctx) return;
  try {
    // The drop is a user gesture, so resuming here satisfies autoplay policy.
    if (ctx.state === "suspended" && typeof ctx.resume === "function") {
      void ctx.resume();
    }
    const now = ctx.currentTime;

    // Master envelope: quick swell to a low peak, then a smooth long tail.
    // Exponential ramps need a non-zero floor, hence 0.0001.
    const master = ctx.createGain();
    master.gain.setValueAtTime(0.0001, now);
    master.gain.exponentialRampToValueAtTime(0.06, now + 0.04);
    master.gain.exponentialRampToValueAtTime(0.0001, now + 0.45);
    master.connect(ctx.destination);

    // Two voices a gentle interval apart (E5 → B5) for a warm, non-beepy
    // timbre, brought in as a soft, quick arpeggio.
    const voices: Array<{ freq: number; gain: number; delay: number }> = [
      { freq: 659.25, gain: 1.0, delay: 0 },
      { freq: 987.77, gain: 0.5, delay: 0.06 },
    ];
    for (const v of voices) {
      const osc = ctx.createOscillator();
      osc.type = "sine";
      osc.frequency.value = v.freq;
      const voiceGain = ctx.createGain();
      voiceGain.gain.value = v.gain;
      osc.connect(voiceGain);
      voiceGain.connect(master);
      osc.start(now + v.delay);
      osc.stop(now + 0.5);
    }
  } catch {
    // Never let an audio hiccup surface to the interaction.
  }
}

/** Two flavours of the dock tick: passing a notch, and picking one. */
export type DockTick = "hover" | "select";

/** Fastest the tick may repeat; a sweep across the dock still reads as a
 *  ratchet, but two pointer events on one frame never double-fire. */
const TICK_MIN_INTERVAL_S = 0.024;
let lastTickAt = -Infinity;
let noiseBuffer: AudioBuffer | null = null;

interface TickVoice {
  /** Sine sweep: start and end frequency (Hz) and how long the glide takes. */
  from: number;
  to: number;
  sweep: number;
  /** Peak gain and how long the body rings. */
  peak: number;
  decay: number;
  /** Band-passed noise transient: centre frequency and peak gain. */
  click: number;
  clickPeak: number;
}

// The hover tick is short and bright, the pick a shade lower, longer and
// firmer — same family, so the two read as one mechanism.
const TICK_VOICES: Record<DockTick, TickVoice> = {
  hover: { from: 2100, to: 700, sweep: 0.018, peak: 0.035, decay: 0.03, click: 3200, clickPeak: 0.02 },
  select: { from: 1300, to: 420, sweep: 0.03, peak: 0.06, decay: 0.05, click: 2200, clickPeak: 0.03 },
};

function noiseFor(ctx: AudioContext): AudioBuffer | null {
  if (typeof ctx.createBuffer !== "function") return null;
  if (!noiseBuffer || noiseBuffer.sampleRate !== ctx.sampleRate) {
    const length = Math.max(1, Math.round(ctx.sampleRate * 0.03));
    noiseBuffer = ctx.createBuffer(1, length, ctx.sampleRate);
    const data = noiseBuffer.getChannelData(0);
    for (let i = 0; i < data.length; i++) data[i] = Math.random() * 2 - 1;
  }
  return noiseBuffer;
}

/**
 * Play one detent tick of the deck dock — the pointer moved onto another icon
 * (`hover`) or picked one (`select`). No-op when WebAudio is unavailable, the
 * page has not been interacted with yet, UI sound is muted, or the last tick
 * was under `TICK_MIN_INTERVAL_S` ago; any audio quirk is swallowed so it can
 * never break the hover it accompanies.
 */
export function playDockTick(kind: DockTick = "hover"): void {
  if (!soundEnabled() || !hadUserGesture()) return;
  const ctx = getCtx();
  if (!ctx) return;
  try {
    if (ctx.state === "suspended") {
      // A hover is not a gesture; the page has had one, so this may succeed.
      // The tick itself is skipped: scheduling into a suspended context would
      // fire every queued tick at once when it finally starts.
      if (typeof ctx.resume === "function") void ctx.resume();
      return;
    }
    const now = ctx.currentTime;
    if (now - lastTickAt < TICK_MIN_INTERVAL_S) return;
    lastTickAt = now;
    const v = TICK_VOICES[kind];

    // Body: a short downward sine glide with a near-instant attack — the
    // "tock" of a notch. A few percent of pitch jitter keeps a fast sweep
    // from sounding like a machine.
    const jitter = 1 + (Math.random() - 0.5) * 0.08;
    const osc = ctx.createOscillator();
    osc.type = "sine";
    osc.frequency.setValueAtTime(v.from * jitter, now);
    osc.frequency.exponentialRampToValueAtTime(v.to * jitter, now + v.sweep);
    const body = ctx.createGain();
    body.gain.setValueAtTime(0.0001, now);
    body.gain.exponentialRampToValueAtTime(v.peak, now + 0.0015);
    body.gain.exponentialRampToValueAtTime(0.0001, now + v.decay);
    osc.connect(body);
    body.connect(ctx.destination);
    osc.start(now);
    osc.stop(now + v.decay + 0.01);

    // Transient: a few milliseconds of band-passed noise on top gives the
    // click its "tick" — without it the sine alone is a blip. Optional: an
    // engine without buffers or filters still gets the body.
    const noise = noiseFor(ctx);
    if (noise && typeof ctx.createBufferSource === "function") {
      const src = ctx.createBufferSource();
      src.buffer = noise;
      let tail: AudioNode = src;
      if (typeof ctx.createBiquadFilter === "function") {
        const band = ctx.createBiquadFilter();
        band.type = "bandpass";
        band.frequency.value = v.click;
        band.Q.value = 1.2;
        src.connect(band);
        tail = band;
      }
      const clickGain = ctx.createGain();
      clickGain.gain.setValueAtTime(v.clickPeak, now);
      clickGain.gain.exponentialRampToValueAtTime(0.0001, now + 0.012);
      tail.connect(clickGain);
      clickGain.connect(ctx.destination);
      src.start(now);
      src.stop(now + 0.02);
    }
  } catch {
    // Never let an audio hiccup surface to the interaction.
  }
}
