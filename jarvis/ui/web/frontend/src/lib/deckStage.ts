/**
 * The deck's centre stage: how large the stage is for the room it has.
 *
 * There used to be a `stageVignette` here too — a radial wash the view drew
 * behind the centre. It is gone (2026-08-20). The stage now carries its own
 * single light (`DeckOrb`'s `.deck-stage-light`, index.css), because two
 * pools drawn from two places fought each other: one lifting the centre out
 * of the wallpaper, one lighting the figure, and together they framed the
 * mascot in a lit niche.
 */

const STAGE_MIN = 200;
const STAGE_MAX = 320;
/**
 * Vertical room kept under the figure for the wave, the readout row and the
 * headline.
 */
const BELOW_RESERVE = 108;

/**
 * The stage size for a room of the given size — as large as the room allows,
 * never taller than what is left under the headline, and never past the point
 * where the mascot stops reading as a character and starts reading as a
 * poster. The maximum until the room has been measured.
 */
export function orbSizeFor(width: number, height: number): number {
  if (!width || !height) return STAGE_MAX;
  const fit = Math.min(width - 16, height - BELOW_RESERVE);
  return Math.max(STAGE_MIN, Math.min(STAGE_MAX, Math.floor(fit)));
}
