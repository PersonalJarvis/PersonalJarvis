/**
 * The studio: the dressing room for the person's uniform.
 *
 * The live preview on the left wears the current loadout and the insignia of
 * the person's rank. On the right, one slot at a time (uniform, headwear,
 * decorations): "automatic" (always the finest unlocked piece), "own
 * clothes" (nothing from the slot), and every piece as a card with the rank
 * it comes with. An unlocked piece is put on with one click; a locked one is
 * tried on in the preview, marked as a try-on with the level and XP it still
 * needs. The pet wears no uniform: its page shows it with its rank.
 */
import { useMemo, useState } from "react";
import { useT } from "@/i18n";
import { useRunLocale } from "@/components/runs/format";
import { equippedFor, rewardRoad, type SlotChoice } from "../cosmetics";
import { RANK_INFO, rankAt, SLOTS, slotOf, type RankId, type RewardId, type Slot } from "../levelCatalog";
import { RankInsignia } from "../insignia/RankInsignia";
import type { RewardRow } from "../progressionApi";
import { useProgression } from "../progressionStore";
import { RewardIcon } from "../RewardIcon";
import { xpToReach } from "./hallModel";
import { LevelHero, LockGlyph, RankTag, TabIcon, type HallWho } from "./hallParts";
import { LoadoutPreview } from "./LoadoutPreview";

const NO_REWARDS: RewardRow[] = [];
const NO_CURVE: number[] = [0];
const NO_CHOICES: Partial<Record<Slot, SlotChoice>> = {};
const NO_TITLES: { level: number; title: string }[] = [];

/** The name plate as it shows over the figure: the name and the rank chip. */
export function RankPlate({ name, level, rank }: { name: string; level: number; rank: RankId }) {
  return (
    <span className="hall-plate">
      <span>{name}</span>
      <span className="level-chip"><RankInsignia rank={rank} size={16} /><b>{level}</b></span>
    </span>
  );
}

export function StudioTab({ who }: { who: HallWho }) {
  const t = useT();
  const locale = useRunLocale();
  const rewards = useProgression((s) => s.snapshot?.rewards ?? NO_REWARDS);
  const curve = useProgression((s) => s.snapshot?.levelXp ?? NO_CURVE);
  const titles = useProgression((s) => s.snapshot?.titles[who.kind] ?? NO_TITLES);
  const subject = useProgression((s) => s.subjects[who.subjectId]);
  const choices = useProgression((s) => s.choices[who.who] ?? NO_CHOICES);
  const choose = useProgression((s) => s.choose);
  const focus = useProgression((s) => s.focusReward);
  const level = subject?.level ?? 1;
  const rank = rankAt(titles, level);
  const road = useMemo(() => rewardRoad(rewards, who.kind), [rewards, who.kind]);
  const focusRow = focus ? road.find((r) => r.rewardId === focus) : undefined;
  const [slot, setSlot] = useState<Slot>(focusRow?.slot ?? "uniform");
  const [tryOn, setTryOn] = useState<RewardId | null>(focusRow && (focusRow.levels[who.kind] ?? 0) > level ? focusRow.rewardId : null);
  const equipped = useMemo(() => equippedFor(rewards, who.kind, level, choices), [rewards, who.kind, level, choices]);
  const loadout = tryOn ? { ...equipped, [slotOf(tryOn)]: tryOn } : equipped;
  const slots = SLOTS.filter((s) => road.some((r) => r.slot === s));
  const inSlot = road.filter((r) => r.slot === slot);
  const current: SlotChoice = choices[slot] ?? "auto";
  const tryRow = tryOn ? road.find((r) => r.rewardId === tryOn) : undefined;
  const tryAt = tryRow?.levels[who.kind] ?? 0;

  const pick = (choice: SlotChoice) => { choose(who.who, slot, choice); setTryOn(null); };

  return (
    <div className="hall-studio">
      <div className="hall-stage">
        <LoadoutPreview subject={who.preview} loadout={loadout} rank={rank}
          label={t("society.hall.preview_label").replace("{0}", who.name)} />
        <div className="hall-stage-bottom">
          {tryRow ? (
            <div className="hall-tryon" role="status">
              <span className="hall-tryon-badge">{t("society.hall.try_on")}</span>
              <span>
                <strong>{t(`society.level.reward.${tryRow.rewardId}`)}</strong>
                <em>{t("society.hall.try_on_line").replace("{0}", String(tryAt))
                  .replace("{1}", xpToReach(curve, subject?.xp ?? 0, tryAt).toLocaleString(locale))}</em>
              </span>
              <button type="button" className="hall-button" onClick={() => setTryOn(null)}>{t("society.hall.try_on_back")}</button>
            </div>
          ) : (
            <RankPlate name={who.name} level={level} rank={rank} />
          )}
          <span className="hall-stage-hint">{t("society.hall.drag_hint")}</span>
        </div>
        {rank !== "private" && (
          <div className="hall-stage-rank" aria-hidden>
            <RankInsignia rank={rank} size={46} />
            <span>{RANK_INFO[rank].grade}</span>
          </div>
        )}
      </div>

      <div className="hall-wardrobe">
        <LevelHero who={who} compact />
        {slots.length === 0 ? (
          <div className="hall-insignia-card">
            <RankInsignia rank={rank} size={120} />
            <strong>{t(`society.level.title.${rank}`)}</strong>
            <p className="hall-hint">{t("society.hall.pet_rank_only").replace("{0}", who.name)}</p>
          </div>
        ) : <>
          <p className="hall-lead">{t("society.hall.studio_body_you")}</p>
          <div className="hall-slots" role="tablist" aria-label={t("society.hall.slots_label")}>
            {slots.map((s) => {
              const worn = loadout[s];
              return (
                <button key={s} type="button" role="tab" aria-selected={slot === s} className="hall-slot" onClick={() => setSlot(s)}>
                  {worn ? <RewardIcon reward={worn} size={34} /> : <span className="hall-slot-empty" aria-hidden />}
                  <span>{t(`society.level.slot.${s}`)}</span>
                </button>
              );
            })}
          </div>
          <p className="hall-hint">{t(`society.hall.slot_hint.${slot}`)}</p>
          <div className="hall-items" role="group" aria-label={t(`society.level.slot.${slot}`)}>
            <button type="button" className="hall-item hall-item-plain" aria-pressed={current === "auto" && !tryOn} onClick={() => pick("auto")}>
              <span className="hall-item-tile" aria-hidden><TabIcon path="M12 3v3M12 18v3M3 12h3M18 12h3M6 6l2 2M16 16l2 2M6 18l2-2M16 8l2-2" size={22} /></span>
              <strong>{t("society.level.auto")}</strong>
              <em>{t("society.hall.auto_hint")}</em>
            </button>
            <button type="button" className="hall-item hall-item-plain" aria-pressed={current === "none" && !tryOn} onClick={() => pick("none")}>
              <span className="hall-item-tile" aria-hidden><TabIcon path="M5 5l14 14M12 3a9 9 0 1 1 0 18a9 9 0 1 1 0-18z" size={22} /></span>
              <strong>{t("society.level.none")}</strong>
              <em>{t("society.hall.none_hint")}</em>
            </button>
            {inSlot.map((reward) => {
              const at = reward.levels[who.kind] ?? 0;
              const open = level >= at;
              const worn = equipped[slot] === reward.rewardId;
              const pressed = tryOn ? tryOn === reward.rewardId : current === reward.rewardId;
              return (
                <button key={reward.rewardId} type="button" className="hall-item" data-open={open || undefined}
                  aria-pressed={pressed}
                  aria-label={open ? undefined : t("society.hall.locked_aria").replace("{0}", t(`society.level.reward.${reward.rewardId}`)).replace("{1}", String(at))}
                  onClick={() => (open ? pick(reward.rewardId) : setTryOn(reward.rewardId))}>
                  <RewardIcon reward={reward.rewardId} size={58} locked={!open} />
                  <strong>{t(`society.level.reward.${reward.rewardId}`)}</strong>
                  <RankTag rank={rankAt(titles, at)} />
                  <em data-open={open || undefined}>
                    {worn ? t("society.hall.equipped") : open ? t("society.hall.unlocked") : t("society.level.locked_at").replace("{0}", String(at))}
                  </em>
                  {!open && <LockGlyph />}
                  {worn && <span className="hall-item-check" aria-hidden><TabIcon path="M5 12.5l4.5 4.5L19 7.5" size={14} /></span>}
                </button>
              );
            })}
          </div>
        </>}
      </div>
    </div>
  );
}
