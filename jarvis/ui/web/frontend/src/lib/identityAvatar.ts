/**
 * The coloured identity mark — one helper for every person in the product.
 *
 * Design.md gives colour exactly three jobs: life, fault, and identity. This is
 * the identity one. A person without a real portrait gets a round avatar whose
 * hue is derived deterministically from their name, so the same person is the
 * same colour in Profile, Contacts, Friends and the review queue, in every
 * session, on every machine. A grey disc with a monogram is explicitly NOT the
 * fallback: it is the thing that makes a near-black app feel dead.
 *
 * Both the disc and the glyph are DERIVED from that one hue rather than written
 * as literals — the glyph is the same hue lifted to 96% lightness, which stays
 * legible on the mid-lightness disc in both themes without needing a per-theme
 * variant (the disc carries its own contrast; it is not painted on the page).
 *
 * Lives in `lib/` on purpose: it is imported from several unrelated view
 * clusters, and a second copy of it would silently give one person two colours.
 */
import type { CSSProperties } from "react";

/** Stable djb2-xor string hash — platform-independent, no Math.random. */
function hashString(value: string): number {
  let hash = 5381;
  for (let i = 0; i < value.length; i++) {
    hash = (Math.imul(hash, 33) ^ value.charCodeAt(i)) >>> 0;
  }
  return hash;
}

/** Up to two initials: first letter of the first and of the last word. */
export function identityInitials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return "?";
  const first = words[0]![0]!;
  const last = words.length > 1 ? words[words.length - 1]![0]! : "";
  return (first + last).toUpperCase();
}

/** The person's hue, 0–359. Same name in, same hue out, forever. */
export function identityHue(name: string): number {
  return hashString(name.trim().toLowerCase()) % 360;
}

/**
 * Inline style for the avatar disc: the identity hue as the fill, the same hue
 * at 96% lightness as the glyph. Saturation and lightness are fixed so no name
 * can hash its way to an unreadable pairing.
 */
export function identityAvatarStyle(name: string): CSSProperties {
  const hue = identityHue(name);
  return {
    backgroundColor: `hsl(${hue} 42% 46%)`,
    color: `hsl(${hue} 45% 96%)`,
  };
}
