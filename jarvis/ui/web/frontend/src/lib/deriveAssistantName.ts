/**
 * Derive the assistant's display name from a wake phrase — the TS mirror of
 * `jarvis.speech.wake_constants.phrase_core` + the title-casing in
 * `jarvis.brain.assistant_name.resolve_assistant_name`. Used for the live
 * "Your assistant will be called: X" hint while the user sets the wake word.
 *
 * Keep in lockstep with the backend WAKE_PREFIXES set.
 */
const WAKE_PREFIXES = new Set([
  "hey", "hi", "ok", "okay", "hello", "hallo", "yo", "hej",
]);

export function deriveAssistantName(phrase: string): string {
  // normalize_phrase: lower-case, punctuation→space, split (keeps umlauts/ß).
  const tokens = (phrase || "")
    .toLowerCase()
    .replace(/[^0-9a-zäöüß]+/g, " ") // i18n-allow: German-diacritics character class matched in logic
    .trim()
    .split(/\s+/)
    .filter(Boolean);
  if (tokens.length === 0) return "";

  // phrase_core: drop leading wake prefixes, but never empty a non-empty phrase.
  let core = [...tokens];
  while (core.length > 0 && WAKE_PREFIXES.has(core[0])) core.shift();
  if (core.length === 0) core = tokens;

  return core.map((tok) => tok.charAt(0).toUpperCase() + tok.slice(1)).join(" ");
}
