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
 * How wide the wash under the centre has to be drawn: twice its own outer
 * radius, so the circle reaches zero INSIDE its element.
 *
 * This is the whole point of the pair. The wash used to be a background on
 * the stage column itself, which is far wider than it is tall — the circle
 * ran off the top and bottom edges while still at a third of its opacity and
 * was cut there, so the "soft pool" was a rectangle with two straight edges.
 * On the dark deck that read as a slightly darker box; in light mode, where
 * the ground colour is near-white over a dark wallpaper, it was a bright slab
 * behind the mascot — the same defect the orb PNG had, from the other side.
 * On its own square element nothing can clip it.
 */
export function stageWashSize(size: number): number {
  return Math.round(size * 1.05) * 2;
}

/**
 * The stage under the centre: the wallpaper darkens softly around the mascot,
 * in the theme's own ground colour (so it is a light pool in light mode), and
 * the mascot stands on a stage instead of on whatever the wallpaper puts
 * there. Sized off the reticle so it reads the same at every stage size, and
 * centred on its own element (see `stageWashSize`).
 */
export function stageVignette(size: number): string {
  const inner = Math.round(size * 0.55);
  const outer = Math.round(size * 1.05);
  return `radial-gradient(circle at 50% 50%, hsl(var(--background) / 0.9) 0px, hsl(var(--background) / 0.55) ${inner}px, hsl(var(--background) / 0) ${outer}px)`;
}
