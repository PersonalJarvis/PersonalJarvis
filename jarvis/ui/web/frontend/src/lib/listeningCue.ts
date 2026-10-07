/** A local, non-speech acknowledgement on the call's existing audio context. */
export function playListeningCue(ctx: AudioContext, volume: number): () => void {
  const voices: OscillatorNode[] = [];
  let gain: GainNode | undefined;
  let stopped = false;
  const stop = () => {
    if (stopped) return;
    stopped = true;
    for (const voice of voices) {
      voice.onended = null;
      voice.disconnect();
      try { voice.stop(); }
      catch { /* A partially constructed oscillator may not have started. */ }
    }
    gain?.disconnect();
  };
  try {
    if (ctx.state !== "running" || !Number.isFinite(volume) || volume <= 0) return stop;
    gain = ctx.createGain();
    const now = ctx.currentTime;
    gain.gain.setValueAtTime(0, now);
    gain.gain.linearRampToValueAtTime(0.09 * Math.min(1, volume), now + 0.01);
    gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.18);
    gain.connect(ctx.destination);
    for (const frequency of [880, 1320]) {
      const voice = ctx.createOscillator();
      voices.push(voice);
      voice.frequency.value = frequency;
      voice.connect(gain);
      voice.start(now);
      voice.stop(now + 0.18);
    }
    voices[voices.length - 1].onended = stop;
  } catch (error) {
    // Readiness feedback must never fail a working microphone connection.
    console.debug("Listening cue unavailable", error);
    stop();
  }
  return stop;
}
