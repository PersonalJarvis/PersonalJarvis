/**
 * A stable colour for a folder, so the same project reads the same everywhere.
 *
 * Derived from the path rather than stored, which means it needs no setting and
 * survives a lost store. The palette is fixed rather than a free hue rotation:
 * arbitrary HSL produces colours that vanish against one of the two themes.
 */
const FOLDER_COLORS = [
  "#e7c46e",
  "#7dd3fc",
  "#a5b4fc",
  "#86efac",
  "#fca5a5",
  "#f0abfc",
  "#fdba74",
  "#5eead4",
] as const;

export function folderColor(key: string): string {
  let sum = 0;
  for (const char of key) sum = (sum + char.charCodeAt(0)) % 4096;
  return FOLDER_COLORS[sum % FOLDER_COLORS.length];
}
