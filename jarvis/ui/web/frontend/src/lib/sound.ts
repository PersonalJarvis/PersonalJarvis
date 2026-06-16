/**
 * A tiny, dependency-free confirmation chime for UI affordances (currently the
 * JarvisDock "mission captured" feedback). WebAudio is a browser built-in, so
 * this works on any cloud-first client and adds nothing to the bundle.
 *
 * Brief from the product: quiet and smooth. Two soft sine voices a gentle
 * interval apart, a low peak gain, a short attack and a smooth exponential
 * release — a "tding", never a beep.
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
