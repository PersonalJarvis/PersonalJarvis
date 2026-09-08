/** Opt-in measurements for the development lab; dormant in normal windows. */
let enabled = false;
let warmup = 0;
const frames: Array<{ ms: number; calls: number; triangles: number }> = [];
export function enableWorldDiagnostics(): void { enabled = true; warmup = 0; frames.length = 0; }
export function diagnosticsEnabled(): boolean { return enabled; }
export function recordWorldFrame(ms: number, calls: number, triangles: number): void {
  if (!enabled || warmup++ < 90) return;
  if (frames.length >= 600) frames.shift();
  frames.push({ ms, calls, triangles });
}
export function worldDiagnostics() {
  const times = frames.map(f => f.ms).sort((a, b) => a - b);
  return { frames: times.length, frameP95Ms: times[Math.floor((times.length - 1) * .95)] ?? 0,
    drawCalls: Math.max(0, ...frames.map(f => f.calls)), triangles: Math.max(0, ...frames.map(f => f.triangles)) };
}
