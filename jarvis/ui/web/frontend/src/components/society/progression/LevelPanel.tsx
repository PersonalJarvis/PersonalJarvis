/**
 * The progress panel (L, or a click on the level card): three tabs.
 *
 * - You / Pet: level, title and bar; what to wear in each slot (automatic,
 *   none, or any unlocked piece); the reward track from level 1 to 50 with
 *   the next unlock marked; and the exact rules that pay XP.
 * - Agents: every agent's level, title and bar, best first, and their rules.
 *
 * Theme tokens throughout, so it reads in light and dark mode.
 */
import { useEffect, useMemo, useRef } from "react";
import { useT } from "@/i18n";
import type { SocietyAgent } from "../data";
import { levelFraction, unlockedFor, type SlotChoice } from "./cosmetics";
import { LevelChip, LevelRing, XpBar } from "./LevelHud";
import { SLOTS, type RewardId, type Slot, type SubjectKind } from "./levelCatalog";
import { agentSubject, PERSON_SUBJECT, petSubject, type RewardRow, type XpRuleRow } from "./progressionApi";
import { useProgression, type PanelTab } from "./progressionStore";
import { RewardIcon } from "./RewardIcon";
import { useLevelSound } from "./levelSounds";

const LEAD_AGENT_ID = "jarvis";

// Stable fallbacks: a selector that returns a fresh [] or {} re-renders forever.
const NO_RULES: XpRuleRow[] = [];
const NO_REWARDS: RewardRow[] = [];
const NO_TITLES: { level: number; title: string }[] = [];
const NO_CHOICES: Partial<Record<Slot, SlotChoice>> = {};

function duration(seconds: number, t: (key: string) => string): string {
  return seconds >= 60
    ? t("society.level.minutes").replace("{0}", String(Math.round(seconds / 60)))
    : t("society.level.seconds").replace("{0}", String(seconds));
}

/** The limit line under a rule: once a day, once per floor, a cooldown, a daily cap. */
export function ruleLimit(rule: XpRuleRow, t: (key: string) => string): string {
  if (rule.source === "daily_visit") return t("society.level.limit_daily");
  if (rule.source === "floor_discovered") return t("society.level.limit_floor");
  if (rule.source === "agent_hired") return t("society.level.limit_agent");
  const parts: string[] = [];
  if (rule.cooldownS > 0) parts.push(t("society.level.limit_cooldown").replace("{0}", duration(rule.cooldownS, t)));
  if (rule.dailyCap > 0) parts.push(t("society.level.limit_cap").replace("{0}", String(rule.dailyCap)));
  return parts.join(" · ");
}

function Rules({ kind }: { kind: SubjectKind }) {
  const t = useT();
  const rules = useProgression((s) => s.snapshot?.rules ?? NO_RULES);
  const mine = rules.filter((r) => r.kind === kind);
  return (
    <section className="level-section">
      <h3>{t(`society.level.rules_${kind}`)}</h3>
      <ul className="level-rules">
        {mine.map((rule) => {
          const limit = ruleLimit(rule, t);
          return (
            <li key={rule.source}>
              <span className="level-rule-xp">+{rule.xp}</span>
              <span className="level-rule-text">
                <span>{t(`society.level.source.${rule.source}`)}</span>
                {limit && <em>{limit}</em>}
              </span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function Wardrobe({ who, kind, level }: { who: "person" | "pet"; kind: SubjectKind; level: number }) {
  const t = useT();
  const rewards = useProgression((s) => s.snapshot?.rewards ?? NO_REWARDS);
  const choices = useProgression((s) => s.choices[who] ?? NO_CHOICES);
  const choose = useProgression((s) => s.choose);
  const open = useMemo(() => unlockedFor(rewards, kind, level), [rewards, kind, level]);
  return (
    <section className="level-section">
      <h3>{t("society.level.wear")}</h3>
      <div className="level-slots">
        {SLOTS.map((slot) => {
          const inSlot = rewards.filter((r) => r.slot === slot && r.levels[kind] !== null);
          if (inSlot.length === 0) return null;
          const current: SlotChoice = choices[slot] ?? "auto";
          const pick = (choice: SlotChoice) => choose(who, slot, choice);
          return (
            <div key={slot} className="level-slot" role="group" aria-label={t(`society.level.slot.${slot}`)}>
              <span className="level-slot-name">{t(`society.level.slot.${slot}`)}</span>
              <div className="office-chips">
                <button type="button" className="office-chip" aria-pressed={current === "auto"} onClick={() => pick("auto")}>{t("society.level.auto")}</button>
                <button type="button" className="office-chip" aria-pressed={current === "none"} onClick={() => pick("none")}>{t("society.level.none")}</button>
                {inSlot.map((reward) => {
                  const unlocked = open.some((r) => r.rewardId === reward.rewardId);
                  return (
                    <button key={reward.rewardId} type="button" className="office-chip level-wear-chip" aria-pressed={current === reward.rewardId}
                      disabled={!unlocked} onClick={() => pick(reward.rewardId)}
                      title={unlocked ? undefined : t("society.level.locked_at").replace("{0}", String(reward.levels[kind]))}>
                      <RewardIcon reward={reward.rewardId} locked={!unlocked} size={16} />
                      {t(`society.level.reward.${reward.rewardId}`)}
                      {!unlocked && <small>{t("society.level.lv").replace("{0}", String(reward.levels[kind]))}</small>}
                    </button>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function Track({ kind, level }: { kind: SubjectKind; level: number }) {
  const t = useT();
  const rewards = useProgression((s) => s.snapshot?.rewards ?? NO_REWARDS);
  const titles = useProgression((s) => s.snapshot?.titles[kind] ?? NO_TITLES);
  const steps = useMemo(() => {
    const byLevel = new Map<number, { rewards: RewardRow[]; title?: string }>();
    for (const r of rewards) {
      const at = r.levels[kind];
      if (at === null) continue;
      const entry = byLevel.get(at) ?? { rewards: [] };
      entry.rewards.push(r);
      byLevel.set(at, entry);
    }
    for (const band of titles) {
      if (band.level <= 1) continue;
      const entry = byLevel.get(band.level) ?? { rewards: [] };
      entry.title = band.title;
      byLevel.set(band.level, entry);
    }
    return [...byLevel.entries()].sort((a, b) => a[0] - b[0]);
  }, [rewards, titles, kind]);
  const nextAt = steps.find(([at]) => at > level)?.[0];
  const track = useRef<HTMLOListElement>(null);
  // Open on the next unlock, not on level 2.
  useEffect(() => {
    track.current?.querySelector("[data-next]")?.scrollIntoView({ block: "nearest", inline: "center" });
  }, [kind]);
  return (
    <section className="level-section">
      <h3>{t("society.level.track")}</h3>
      <ol ref={track} className="level-track">
        {steps.map(([at, step]) => (
          <li key={at} data-done={level >= at || undefined} data-next={at === nextAt || undefined}>
            <span className="level-track-at">{t("society.level.lv").replace("{0}", String(at))}</span>
            <span className="level-track-items">
              {step.rewards.map((r) => (
                <span key={r.rewardId} className="level-track-item">
                  <RewardIcon reward={r.rewardId as RewardId} locked={level < at} size={26} />
                  <span>{t(`society.level.reward.${r.rewardId}`)}</span>
                </span>
              ))}
              {step.title && <span className="level-track-title">{t(`society.level.title.${step.title}`)}</span>}
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}

function SubjectHeader({ kind, subjectId, name }: { kind: SubjectKind; subjectId: string; name: string }) {
  const t = useT();
  const subject = useProgression((s) => s.subjects[subjectId]);
  const level = subject?.level ?? 1;
  const fraction = levelFraction(subject);
  const left = subject && subject.xpForNext > 0 ? subject.xpForNext - subject.xpIntoLevel : null;
  return (
    <div className="level-subject">
      <LevelRing kind={kind} level={level} fraction={fraction} size={58} />
      <div>
        <strong>{name}</strong>
        <em>{t(`society.level.title.${subject?.title || (kind === "pet" ? "hatchling" : "newcomer")}`)}</em>
        <XpBar fraction={fraction} kind={kind} label={t("society.level.xp_label")} />
        <span className="level-subject-xp">
          {left === null && subject ? t("society.level.max")
            : t("society.level.to_next").replace("{0}", String(left ?? 40)).replace("{1}", String(level + 1))}
          {subject ? ` · ${t("society.level.total").replace("{0}", String(subject.xp))}` : ""}
        </span>
      </div>
    </div>
  );
}

function AgentsTab({ agents }: { agents: readonly SocietyAgent[] }) {
  const t = useT();
  const subjects = useProgression((s) => s.subjects);
  const rows = useMemo(() => agents
    .filter((a) => a.agentId !== LEAD_AGENT_ID && a.tier !== "lead")
    .map((a) => ({ agent: a, subject: subjects[agentSubject(a.agentId)] }))
    .sort((a, b) => (b.subject?.xp ?? 0) - (a.subject?.xp ?? 0) || a.agent.name.localeCompare(b.agent.name)), [agents, subjects]);
  return (
    <>
      <section className="level-section">
        <h3>{t("society.level.team")}</h3>
        {rows.length === 0 ? <p className="office-hint">{t("society.level.no_agents")}</p> : (
          <ol className="level-agents">
            {rows.map(({ agent, subject }, i) => (
              <li key={agent.agentId}>
                <span className="level-agent-rank">{i + 1}</span>
                <LevelChip kind="agent" level={subject?.level ?? 1} />
                <span className="level-agent-body">
                  <span><strong>{agent.name}</strong> <em>{t(`society.level.title.${subject?.title || "rookie"}`)}</em></span>
                  <XpBar fraction={levelFraction(subject)} kind="agent" label={t("society.level.agent_xp_label").replace("{0}", agent.name)} />
                </span>
              </li>
            ))}
          </ol>
        )}
      </section>
      <Rules kind="agent" />
    </>
  );
}

export function LevelPanel({ agents, playerName, petName }: { agents: readonly SocietyAgent[]; playerName: string; petName: string }) {
  const t = useT();
  const tab = useProgression((s) => s.panel);
  const openPanel = useProgression((s) => s.openPanel);
  const petId = useProgression((s) => s.petId);
  const person = useProgression((s) => s.subjects[PERSON_SUBJECT]);
  const pet = useProgression((s) => s.subjects[petSubject(petId)]);
  const sound = useLevelSound();
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!tab) return;
    panel.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.stopPropagation();
      openPanel(null);
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [tab, openPanel]);
  if (!tab) return null;
  const tabs: PanelTab[] = ["you", "pet", "agents"];
  return (
    <div className="level-panel-layer" data-office-ui onPointerDown={(e) => { if (e.target === e.currentTarget) openPanel(null); }}>
      <div ref={panel} className="office-card level-panel" role="dialog" aria-modal="false" aria-labelledby="level-panel-title" tabIndex={-1}>
        <header className="level-panel-head">
          <h2 id="level-panel-title">{t("society.level.panel_title")}</h2>
          <div className="level-tabs" role="tablist">
            {tabs.map((id) => (
              <button key={id} type="button" role="tab" aria-selected={tab === id} className="level-tab" onClick={() => openPanel(id)}>
                {id === "pet" ? petName : t(`society.level.tab_${id}`)}
              </button>
            ))}
          </div>
          <button type="button" className="office-icon-button" onClick={() => openPanel(null)} aria-label={t("society.office.close")}>×</button>
        </header>
        <div className="level-panel-body">
          {tab === "you" && <>
            <SubjectHeader kind="person" subjectId={PERSON_SUBJECT} name={playerName} />
            <Wardrobe who="person" kind="person" level={person?.level ?? 1} />
            <Track kind="person" level={person?.level ?? 1} />
            <Rules kind="person" />
          </>}
          {tab === "pet" && <>
            <SubjectHeader kind="pet" subjectId={petSubject(petId)} name={petName} />
            <p className="office-hint">{t("society.level.pet_intro")}</p>
            <Wardrobe who="pet" kind="pet" level={pet?.level ?? 1} />
            <Track kind="pet" level={pet?.level ?? 1} />
            <Rules kind="pet" />
          </>}
          {tab === "agents" && <AgentsTab agents={agents} />}
        </div>
        <footer className="level-panel-foot">
          <label className="level-sound">
            <input type="checkbox" checked={sound.on} onChange={(e) => sound.set(e.target.checked)} />
            {t("society.level.sound")}
          </label>
          <span className="office-hint">{t("society.level.free_note")}</span>
        </footer>
      </div>
    </div>
  );
}
