/** Both path separators — this app's paths come from Windows, macOS and Linux. */
const SEPARATOR = /[\\/]/;
const TRAILING_SEPARATORS = /[\\/]+$/;

/**
 * The last segment of a folder path — what a person calls that folder.
 *
 * Platform-neutral by construction: both separators are split on, because the
 * path being shown came from whichever machine the backend is on, not from the
 * browser drawing it.
 *
 * "~" for an empty path: no folder chosen yet means the runner's own default,
 * and a blank chip says nothing at all.
 */
export function folderLeaf(path: string): string {
  if (!path) return "~";
  const trimmed = path.replace(TRAILING_SEPARATORS, "");
  const parts = trimmed.split(SEPARATOR);
  return parts[parts.length - 1] || trimmed;
}
