/**
 * The reward road: every level that brings something — pieces to wear and
 * new titles — from the first unlock to the cap, as one horizontal road with
 * the subject's own position on it. Choosing a step shows it below: the
 * subject in the preview wearing that piece, how rare it is, at which level
 * it opens and how much XP is still missing, and a button to put it on once
 * it is open.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useT } from "@/i18n";
import { equippedFor, rewardRoad, type SlotChoice } from "../cosmetics";
import { slotOf, type RewardId, type Slot } from "../levelCatalog";
import type { RewardRow } from "../progressionApi";
import { useProgression } from "../progressionStore";
import { RewardIcon } from "../RewardIcon";
import { rarityOf, xpToReach } from "./hallModel";
import { LockGlyph, TabIcon, type HallWho } from "./hallParts";
import { LoadoutPreview } from "./LoadoutPreview";
import { FramePlate } from "./StudioTab";

const NO_REWARDS: RewardRow[] = [];
const NO_TITLES: { level: number; title: string }[] = [];
const NO_CURVE: number[] = [0];
const NO_CHOICES: Partial<Record<Slot, SlotChoice>> = {};

interface Step { level: number; rewards: RewardRow[]; title?: string }

/** The road's steps: each level with a reward or a new title, lowest first. */
export function roadSteps(road: readonly RewardRow[], titles: readonly { level: number; title: string }[], kind: "person" | "pet"): Step[] {
  const byLevel = new Map<number, Step>();
  for (const reward of road) {
    const at = reward.levels[kind] ?? 0;
    const step = byLevel.get(at) ?? { level: at, rewards: [] };
    step.rewards.push(reward);
    byLevel.set(at, step);
  }
  for (const band of titles) {
    if (band.level <= 1) continue;
    const step = byLevel.get(band.level) ?? { level: band.level, rewards: [] };
    step.title = band.title;
    byLevel.set(band.level, step);
  }
  return [...byLevel.values()].sort((a, b) => a.level - b.level);
}

export function RewardsTab({ who }: { who: HallWho }) {
  const t = useT();
  const rewards = useProgression((s) => s.snapshot?.rewards ?? NO_REWARDS);
  const titles = useProgression((s) => s.snapshot?.titles[who.kind] ?? NO_TITLES);
  const curve = useProgression((s) => s.snapshot?.levelXp ?? NO_CURVE);
  const subject = useProgression((s) => s.subjects[who.subjectId]);
  const choices = useProgression((s) => s.choices[who.who] ?? NO_CHOICES);
  const choose = useProgression((s) => s.choose);
  const focus = useProgression((s) => s.focusReward);
  const level = subject?.level ?? 1;
  const road = useMemo(() => rewardRoad(rewards, who.kind), [rewards, who.kind]);
  const steps = useMemo(() => roadSteps(road, titles, who.kind), [road, titles, who.kind]);
  const nextStep = steps.find((s) => s.level > level);
  const initial = (focus && road.find((r) => r.rewardId === focus)) || nextStep?.rewards[0] || road[road.length - 1];
  const [selected, setSelected] = useState<{ level: number; reward: RewardId | null }>(() => ({
    level: initial ? initial.levels[who.kind] ?? 1 : nextStep?.level ?? 1, reward: initial?.rewardId ?? null,
  }));
  const step = steps.find((s) => s.level === selected.level) ?? nextStep ?? steps[0];
  const reward = step?.rewards.find((r) => r.rewardId === selected.reward) ?? step?.rewards[0];
  const equipped = useMemo(() => equippedFor(rewards, who.kind, level, choices), [rewards, who.kind, level, choices]);
  const loadout = reward ? { ...equipped, [slotOf(reward.rewardId)]: reward.rewardId } : equipped;
  const track = useRef<HTMLOListElement>(null);
  // Open on the chosen step, not on the first one.
  useEffect(() => {
    track.current?.querySelector("[aria-pressed='true']")?.scrollIntoView({ block: "nearest", inline: "center" });
  }, []);
  if (!step) return <p className="hall-empty">{t("society.hall.all_unlocked")}</p>;
  const open = level >= step.level;
  const missing = xpToReach(curve, subject?.xp ?? 0, step.level);
  const worn = reward ? equipped[reward.slot] === reward.rewardId : false;
  return (
    <div className="hall-rewards">
      <p className="hall-lead">{t("society.hall.road_body")}</p>
      <ol ref={track} className="hall-road">
        {steps.map((s, i) => {
          const done = level >= s.level;
          const here = s === nextStep;
          return (
            <li key={s.level} data-done={done || undefined} data-here={here || undefined}>
              {here && <span className="hall-road-here">{t("society.hall.road_here").replace("{0}", String(level))}</span>}
              <button type="button" className="hall-road-step" aria-pressed={s.level === step.level}
                onClick={() => setSelected({ level: s.level, reward: s.rewards[0]?.rewardId ?? null })}>
                <span className="hall-road-level">{t("society.level.lv").replace("{0}", String(s.level))}</span>
                <span className="hall-road-icons">
                  {s.rewards.map((r) => <RewardIcon key={r.rewardId} reward={r.rewardId} size={28} />)}
                  {s.title && <span className="hall-road-title-mark" aria-hidden><TabIcon path="M7 3.5h10v17l-5-3.6-5 3.6z" size={16} /></span>}
                </span>
                {s.title && <span className="hall-road-title">{t(`society.level.title.${s.title}`)}</span>}
                {!done && <LockGlyph />}
              </button>
              {i < steps.length - 1 && <span className="hall-road-link" aria-hidden />}
            </li>
          );
        })}
      </ol>

      <div className="hall-detail">
        <div className="hall-stage hall-stage-small">
          <LoadoutPreview subject={who.preview} loadout={loadout} label={t("society.hall.preview_label").replace("{0}", who.name)} />
          {reward?.slot === "frame" && (
            <div className="hall-stage-bottom"><FramePlate name={who.name} level={step.level} frame={reward.rewardId} /></div>
          )}
        </div>
        <div className="hall-detail-info">
          <span className="hall-detail-kicker" data-open={open || undefined}>
            {open ? t("society.hall.reward_open").replace("{0}", String(step.level)) : t("society.hall.reward_locked").replace("{0}", String(step.level))}
          </span>
          {step.rewards.length > 1 && (
            <div className="hall-detail-pick" role="group" aria-label={t("society.level.lv").replace("{0}", String(step.level))}>
              {step.rewards.map((r) => (
                <button key={r.rewardId} type="button" aria-pressed={r.rewardId === reward?.rewardId}
                  onClick={() => setSelected({ level: step.level, reward: r.rewardId })}>
                  <RewardIcon reward={r.rewardId} size={22} />
                  {t(`society.level.reward.${r.rewardId}`)}
                </button>
              ))}
            </div>
          )}
          {reward && (
            <>
              <h3>{t(`society.level.reward.${reward.rewardId}`)}</h3>
              <span className="hall-card-meta">
                <span className="hall-rarity" data-rarity={rarityOf(step.level)}>{t(`society.hall.rarity.${rarityOf(step.level)}`)}</span>
                <span>{t(`society.level.slot.${reward.slot}`)}</span>
              </span>
              <p>{t(`society.hall.slot_hint.${reward.slot}`)}</p>
            </>
          )}
          {step.title && (
            <p className="hall-detail-title">
              {t("society.level.title_line").replace("{0}", t(`society.level.title.${step.title}`))}
            </p>
          )}
          {!open && <p className="hall-detail-missing">{t("society.hall.reward_missing").replace("{0}", missing.toLocaleString())}</p>}
          {reward && open && (
            worn
              ? <span className="hall-detail-worn">{t("society.hall.wearing_now")}</span>
              : <button type="button" className="hall-button hall-button-primary" onClick={() => choose(who.who, reward.slot, reward.rewardId)}>
                {t("society.hall.put_on")}
              </button>
          )}
        </div>
      </div>
    </div>
  );
}
