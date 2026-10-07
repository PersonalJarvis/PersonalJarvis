/** Playback-confirmed text positions. Generation/level events are not positions. */
export type SpeechPlayback = {
  id: number;
  text: string;
  chars: number;
  state: "queued" | "playing" | "paused" | "ended" | "cancelled";
};

let serial = 0;
let current: SpeechPlayback | null = null;
const listeners = new Set<() => void>();
const timedLines = new Map<string, { text: string; chars: number }>();
let timedSession = "";

export function readTimedSpeechSession(): string { return timedSession; }

export function setTimedSpeechSession(session: string): void {
  if (session === timedSession) return;
  timedSession = session;
  for (const listener of listeners) listener();
}

export function readTimedSpeechPlayback(id: string): { text: string; chars: number } | null {
  return timedLines.get(id) ?? null;
}

export function publishTimedSpeechPlayback(id: string, text: string, chars: number): void {
  const old = timedLines.get(id);
  if (old?.text === text && old.chars === chars) return;
  timedLines.set(id, { text, chars });
  while (timedLines.size > 128) timedLines.delete(timedLines.keys().next().value!);
  for (const listener of listeners) listener();
}

export function subscribeSpeechPlayback(listener: () => void): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

export function readSpeechPlayback(): SpeechPlayback | null { return current; }

function publish(value: SpeechPlayback | null): void {
  current = value;
  for (const listener of listeners) listener();
}

/** The player owns this id; late callbacks cannot advance a replacement. */
export function beginSpeechPlayback(text: string): number {
  const id = ++serial;
  publish({ id, text, chars: 0, state: "queued" });
  return id;
}

export function updateSpeechPlayback(
  id: number,
  state: SpeechPlayback["state"],
  chars = current?.chars ?? 0,
): void {
  if (!current || current.id !== id || !Number.isFinite(chars)) return;
  if (current.state === "ended" || current.state === "cancelled") return;
  const next = Math.max(current.chars, Math.min(current.text.length, Math.floor(chars)));
  if (current.state !== state || current.chars !== next) publish({ ...current, state, chars: next });
}

/** A different playback surface has no word positions; never inherit old ones. */
export function clearSpeechPlayback(): void {
  if (current) publish(null);
}

/** Highlight the current whole word only when its playback boundary fires. */
export function spokenWordEnd(text: string, index: number): number {
  if (!Number.isInteger(index) || index < 0 || index >= text.length) return 0;
  const nextWord = text.slice(index).search(/\S/);
  if (nextWord < 0) return text.length;
  index += nextWord;
  const end = text.slice(index).search(/\s/);
  return end < 0 ? text.length : index + end;
}
