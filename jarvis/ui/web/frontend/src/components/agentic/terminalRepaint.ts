/** Shared protocol cases also test ReplayBuffer's backend acknowledgement. */
const FULL_SCREEN_ERASE = /\x1b\[2J|\x1b\[(?:0*1?)(?:;0*1?)?[Hf](?:\x1b\[[0-9;:]*m|\x1b\[\?(?:25|2026)[hl])*\x1b\[0*J/;

/** Retain enough of a split cursor-home/erase pair and its color controls. */
export const FULL_SCREEN_ERASE_SCAN_TAIL = 128;

/** ED 0 clears the whole screen only immediately after a cursor-home. */
export function hasFullScreenErase(text: string): boolean {
  return FULL_SCREEN_ERASE.test(text);
}
