/**
 * A cosmetic's small picture for the HUD and the progress panel: its slot's
 * glyph on a tile in the effect's own colours. Purely decorative — the name
 * always sits next to it.
 */
import { EFFECT_COLOURS, FRAME_STYLE, slotOf, type RewardId } from "./levelCatalog";

function tileColours(reward: RewardId): [string, string] {
  if (reward.startsWith("frame_")) {
    const style = FRAME_STYLE[reward as keyof typeof FRAME_STYLE];
    return [style.ring, style.fill];
  }
  const colours = EFFECT_COLOURS[reward as keyof typeof EFFECT_COLOURS];
  return [colours[0], colours[colours.length - 1]];
}

function Glyph({ reward }: { reward: RewardId }) {
  const slot = slotOf(reward);
  if (slot === "frame") return <path d="M7 2.5h10l5 9.5-5 9.5H7l-5-9.5z" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinejoin="round" />;
  if (slot === "aura") return <><ellipse cx="12" cy="16" rx="9" ry="3.6" fill="none" stroke="currentColor" strokeWidth="2" /><circle cx="12" cy="9" r="3" fill="currentColor" /></>;
  if (slot === "trail") return <><circle cx="6" cy="17" r="2.2" fill="currentColor" /><circle cx="12" cy="12" r="2.6" fill="currentColor" /><circle cx="18.5" cy="6.5" r="3" fill="currentColor" /></>;
  return <path d="M12 2.8l2.6 5.6 6.1.7-4.5 4.2 1.2 6-5.4-3-5.4 3 1.2-6L3.3 9.1l6.1-.7z" fill="currentColor" />;
}

export function RewardIcon({ reward, locked = false, size = 30 }: { reward: RewardId; locked?: boolean; size?: number }) {
  const [a, b] = tileColours(reward);
  return (
    <span className="level-reward-icon" data-locked={locked || undefined} aria-hidden
      style={{ width: size, height: size, background: locked ? undefined : `linear-gradient(145deg, ${a}, ${b})` }}>
      <svg viewBox="0 0 24 24" width={size * 0.6} height={size * 0.6}><Glyph reward={reward} /></svg>
    </span>
  );
}
