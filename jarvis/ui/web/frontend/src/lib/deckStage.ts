/**
 * The deck's centre stage: how large the orb reticle is for the room it has,
 * and the vignette the wallpaper gets under it.
 */

const ORB_MIN = 200;
const ORB_MAX = 320;
/** Vertical room kept for the headline under the orb (two lines plus the gap). */
const HEADLINE_RESERVE = 72;

/**
 * The reticle size for a stage of the given size — as large as the stage
 * allows, never taller than the room left under the headline, and never past
 * the point where the reticle stops reading as an instrument. The maximum
 * until the stage has been measured.
 */
export function orbSizeFor(width: number, height: number): number {
  if (!width || !height) return ORB_MAX;
  const fit = Math.min(width - 16, height - HEADLINE_RESERVE);
  return Math.max(ORB_MIN, Math.min(ORB_MAX, Math.floor(fit)));
}

/**
 * The stage under the centre: the wallpaper darkens softly around the orb, in
 * the theme's own ground colour (so it is a light pool in light mode), and the
 * orb stands on a stage instead of sitting on whatever the wallpaper puts
 * there. Sized off the reticle so it reads the same at every stage size.
 */
export function stageVignette(size: number): string {
  const inner = Math.round(size * 0.55);
  const outer = Math.round(size * 1.05);
  return `radial-gradient(circle at 50% 46%, hsl(var(--background) / 0.9) 0px, hsl(var(--background) / 0.55) ${inner}px, hsl(var(--background) / 0) ${outer}px)`;
}
