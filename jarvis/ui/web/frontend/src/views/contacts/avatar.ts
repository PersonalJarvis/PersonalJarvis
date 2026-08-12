/**
 * Deterministic identity color + initials for contact avatars.
 *
 * Same name → same hue, every session: the color carries recognition, not
 * meaning. A solid mid-lightness background with a white glyph stays readable
 * on both themes, so no per-theme variant is needed.
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
export function contactInitials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return "?";
  const first = words[0]![0]!;
  const last = words.length > 1 ? words[words.length - 1]![0]! : "";
  return (first + last).toUpperCase();
}

/** Inline style for the avatar circle: hue from the name, fixed S/L. */
export function contactAvatarStyle(name: string): CSSProperties {
  const hue = hashString(name.trim().toLowerCase()) % 360;
  return { backgroundColor: `hsl(${hue} 42% 46%)`, color: "#fff" };
}
