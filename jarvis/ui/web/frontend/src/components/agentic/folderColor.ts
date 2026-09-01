import { identityHue } from "@/lib/identityHue";

/**
 * A stable colour for a folder, so the same project reads the same everywhere.
 *
 * Derived from the path rather than stored, which means it needs no setting and
 * survives a lost store. It now derives through the app's one identity helper
 * (`lib/identityHue`) instead of a private list of eight hex values: a project,
 * a conversation and an agent are the same kind of thing to a reader, so they
 * should not be coloured by two unrelated tables that can drift apart — and a
 * hard-coded palette is a literal colour by another name.
 *
 * This mark rides an icon rather than a filled disc, so it is lifted well above
 * the avatar's fill lightness: a 38%-lightness stroke disappears into a
 * near-black rail, where a filled circle at the same value reads perfectly.
 */
const STROKE_SATURATION = 58;
const STROKE_LIGHTNESS = 62;

export function folderColor(key: string): string {
  return `hsl(${identityHue(key)} ${STROKE_SATURATION}% ${STROKE_LIGHTNESS}%)`;
}
