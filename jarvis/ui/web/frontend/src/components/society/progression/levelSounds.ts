/**
 * Level-up sounds, synthesised with Web Audio: no files to ship, nothing to
 * load. A rising major arpeggio with a shimmer on top for the person and the
 * pet, a short two-note chime for an agent, a soft tick for "+XP". The
 * listener can switch them off in the progress panel. A browser without Web
 * Audio, or one that refuses to start it, plays nothing.
 */
import { create } from "zustand";

const SOUND_KEY = "jarvis.verse.level.sound.v1";

function readSoundOn(): boolean {
  try {
    return typeof localStorage === "undefined" || localStorage.getItem(SOUND_KEY) !== "off";
  } catch (error) {
    console.debug("Verse sound setting unavailable", error);
    return true;
  }
}

export const useLevelSound = create<{ on: boolean; set: (on: boolean) => void }>((set) => ({
  on: readSoundOn(),
  set: (on) => {
    set({ on });
    try { localStorage.setItem(SOUND_KEY, on ? "on" : "off"); } catch (error) { console.debug("Verse sound setting not saved", error); }
  },
}));

let context: AudioContext | null = null;

function audio(): AudioContext | null {
  if (!useLevelSound.getState().on) return null;
  try {
    const Ctor = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!Ctor) return null;
    context ??= new Ctor();
    if (context.state === "suspended") void context.resume().catch(() => undefined);
    return context;
  } catch (error) {
    console.debug("Web Audio unavailable", error);
    return null;
  }
}

function tone(ctx: AudioContext, freq: number, start: number, length: number, gain: number, type: OscillatorType = "triangle"): void {
  const osc = ctx.createOscillator();
  const amp = ctx.createGain();
  osc.type = type;
  osc.frequency.setValueAtTime(freq, start);
  amp.gain.setValueAtTime(0.0001, start);
  amp.gain.exponentialRampToValueAtTime(gain, start + 0.015);
  amp.gain.exponentialRampToValueAtTime(0.0001, start + length);
  osc.connect(amp).connect(ctx.destination);
  osc.start(start);
  osc.stop(start + length + 0.02);
}

/** C major up to the octave, then a sparkle on the fifth above. */
const FANFARE = [523.25, 659.25, 783.99, 1046.5];

export function playLevelUp(big: boolean): void {
  const ctx = audio();
  if (!ctx) return;
  const t = ctx.currentTime + 0.02;
  if (!big) {
    tone(ctx, 783.99, t, 0.22, 0.06);
    tone(ctx, 1046.5, t + 0.11, 0.35, 0.06);
    return;
  }
  FANFARE.forEach((f, i) => tone(ctx, f, t + i * 0.09, 0.32, 0.08));
  tone(ctx, 1046.5, t + 0.36, 0.9, 0.07);
  tone(ctx, 1318.5, t + 0.36, 0.9, 0.05);
  for (let i = 0; i < 6; i++) tone(ctx, 2093 + i * 220, t + 0.42 + i * 0.05, 0.18, 0.018, "sine");
}

export function playXpTick(): void {
  const ctx = audio();
  if (!ctx) return;
  const t = ctx.currentTime + 0.01;
  tone(ctx, 1567.98, t, 0.09, 0.025, "sine");
}
