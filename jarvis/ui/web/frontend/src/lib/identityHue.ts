/**
 * Identity marks — the one place outside status where this product carries hue.
 *
 * Design.md: "Every conversation, agent, provider and person carries a coloured
 * identity mark: a real vendor logo where one exists, otherwise a 36px round
 * avatar whose hue is derived deterministically from the name. Never a grey
 * placeholder." A grey disc with a letter in it is the thing that makes a black
 * app read as dead, and a conversation list is where the product feels alive or
 * does not.
 *
 * Three decisions worth stating, because each one had an obvious-looking
 * alternative that is wrong here:
 *
 * 1. The colour is COMPUTED, never looked up in a table. A fixed array of hex
 *    values is a literal colour by another name — the exact thing the system
 *    bans — and it also fossilises a palette that nothing can re-tune. A hash
 *    into evenly-spaced hue slots gives the same stability for free.
 *
 * 2. The slots step AROUND the three status bands (life ≈ 153°, degraded ≈ 41°,
 *    fault ≈ 4°). Identity must never be readable as state: a green avatar
 *    beside a green "running" dot teaches the eye that green means nothing.
 *
 * 3. The mark does NOT flip with the theme. Status does not, and identity is
 *    the same kind of absolute: the same person is the same colour on paper and
 *    in the dark. One mid-dark fill carrying light ink clears 4.5:1 against its
 *    own label and stays a distinct disc on both the near-black card (#212121)
 *    and the white one, so a single value serves both without a second branch
 *    that could drift.
 */

/**
 * Twelve hues, evenly spread and stepped clear of the status bands.
 *
 * Twelve rather than the more usual eight because a conversation list shows a
 * dozen rows at once and repeats inside one screen read as a relationship the
 * data does not have.
 */
const IDENTITY_HUES = [
  200, 214, 228, 244, 262, 280, 298, 316, 332, 88, 108, 128,
] as const;

/** Fill saturation/lightness. See note 3 above — one pair, both themes. */
const FILL_SATURATION = 46;
const FILL_LIGHTNESS = 38;

/**
 * The ink on a mark: the same hue carried up to the ink ceiling rather than a
 * flat white. It keeps the mark reading as one object, and it is derived from
 * the name like everything else here instead of being a colour written down.
 */
const INK_SATURATION = 32;
const INK_LIGHTNESS = 96;

/**
 * FNV-1a over the code points. Any stable hash would do; this one is chosen
 * because a plain character sum collides on anagrams, which in a chat list
 * means "Deploy notes" and "Notes deploy" share a colour.
 */
function hash(key: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < key.length; i += 1) {
    h ^= key.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

/** The hue this name always resolves to, in degrees. */
export function identityHue(name: string): number {
  const key = name.trim().toLowerCase();
  if (!key) return IDENTITY_HUES[0];
  return IDENTITY_HUES[hash(key) % IDENTITY_HUES.length];
}

export interface IdentityMark {
  /** Fill for the avatar disc. */
  background: string;
  /** Ink for the initial sitting on it. */
  color: string;
}

/**
 * The inline style for an identity avatar. Returned as a style object rather
 * than a class because the hue is data — Tailwind cannot generate a utility for
 * a value that only exists at runtime.
 */
export function identityMark(name: string): IdentityMark {
  const hue = identityHue(name);
  return {
    background: `hsl(${hue} ${FILL_SATURATION}% ${FILL_LIGHTNESS}%)`,
    color: `hsl(${hue} ${INK_SATURATION}% ${INK_LIGHTNESS}%)`,
  };
}

/**
 * The one or two letters a mark shows. Two initials where the name has two
 * words, one otherwise; emoji and other non-letters are skipped so a title like
 * "🚀 ship it" marks as "S" instead of half a surrogate pair.
 */
export function identityInitial(name: string): string {
  const words = name
    .split(/[\s/_-]+/)
    .map((w) => w.replace(/[^\p{L}\p{N}]/gu, ""))
    .filter(Boolean);
  if (words.length === 0) return "?";
  if (words.length === 1) return words[0].slice(0, 1).toUpperCase();
  return (words[0][0] + words[1][0]).toUpperCase();
}
