/**
 * The Level Hall screen: the game menu behind the hall's checkpoints, the
 * level card and `L`. Six pages for the person or their pet (the pet IS
 * Jarvis in the Verse):
 *
 * - Overview: the rank, level and XP bar, the next promotion, the next
 *   uniform pieces and the quickest ways to earn.
 * - Ranks: the whole rank ladder as one chart, from private to five stars.
 * - Studio: the dressing room. Every slot's pieces as cards; an unlocked
 *   piece is put on with one click, a locked one is tried on in the live
 *   preview with the level it needs.
 * - Rewards: the promotion road from level 1 to the cap, every step's rank
 *   and pieces, and the chosen step in the preview.
 * - Guide: how levelling works — three steps, what the next levels cost, and
 *   every rule that pays XP for you, your pet and your agents.
 * - Team: every agent's level, best first.
 *
 * The chrome uses theme tokens (light and dark); the stage and the insignia
 * are the game's own and read in both modes.
 */
import { useEffect, useMemo, useRef } from "react";
import { useT } from "@/i18n";
import { useCompanionPet } from "../../companion/companionPetStore";
import type { SocietyAgent } from "../../data";
import type { ToyLook } from "../../office/toyFigureModel";
import { levelFraction, rewardRoad } from "../cosmetics";
import { RANK_INFO, rankAt, rankOf } from "../levelCatalog";
import { RankInsignia } from "../insignia/RankInsignia";
import { LevelChip, XpBar } from "../LevelHud";
import { RewardTileTone } from "../RewardIcon";
import { useLevelSound } from "../levelSounds";
import { agentSubject, PERSON_SUBJECT, petSubject, type RewardRow, type XpRuleRow } from "../progressionApi";
import { HALL_TABS, useProgression, type HallTab } from "../progressionStore";
import { groupRules, nextTitle, quickWins, upcomingLevelCosts, xpToReach } from "./hallModel";
import { LevelHero, RewardCard, RuleRow, Section, TabIcon, type HallWho } from "./hallParts";
import { RanksTab } from "./RanksTab";
import { RewardsTab } from "./RewardsTab";
import { StudioTab } from "./StudioTab";
import "./hall.css";

const LEAD_AGENT_ID = "jarvis";
// Stable fallbacks: a selector that returns a fresh [] or {} re-renders forever.
const NO_RULES: XpRuleRow[] = [];
const NO_REWARDS: RewardRow[] = [];
const NO_CURVE: number[] = [0];
const NO_TITLES: { level: number; title: string }[] = [];

/** Stroke icons in a 24 × 24 box for the page tabs. */
const TAB_ICON: Record<HallTab, string> = {
  overview: "M12 2.8l8 4.6v9.2l-8 4.6-8-4.6V7.4zM12 8v4l3 2",
  ranks: "M5 10l7-4.5 7 4.5M5 15l7-4.5 7 4.5M5 20l7-4.5 7 4.5",
  studio: "M10 3l1.6 5.4L17 10l-5.4 1.6L10 17l-1.6-5.4L3 10l5.4-1.6zM18 14l.8 2.2L21 17l-2.2.8L18 20l-.8-2.2L15 17l2.2-.8z",
  rewards: "M4 10h16v10H4zM2.5 6.5h19V10h-19zM12 6.5V20M12 6.5C10 3 6.5 3 6.5 5s3 1.5 5.5 1.5M12 6.5C14 3 17.5 3 17.5 5s-3 1.5-5.5 1.5",
  guide: "M4 4.5h6a2 2 0 0 1 2 2V20a2 2 0 0 0-2-2H4zM20 4.5h-6a2 2 0 0 0-2 2V20a2 2 0 0 1 2-2h6z",
  team: "M9 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM3 20c0-3.3 2.7-5.5 6-5.5s6 2.2 6 5.5M16.5 11a2.5 2.5 0 1 0 0-5M18 14.5c2 .6 3.5 2.4 3.5 5",
};

function OverviewTab({ who }: { who: HallWho }) {
  const t = useT();
  const open = useProgression((s) => s.openPanel);
  const rewards = useProgression((s) => s.snapshot?.rewards ?? NO_REWARDS);
  const rules = useProgression((s) => s.snapshot?.rules ?? NO_RULES);
  const curve = useProgression((s) => s.snapshot?.levelXp ?? NO_CURVE);
  const titles = useProgression((s) => s.snapshot?.titles[who.kind] ?? NO_TITLES);
  const subject = useProgression((s) => s.subjects[who.subjectId]);
  const level = subject?.level ?? 1;
  const promotion = nextTitle(titles, level);
  const promoRank = promotion ? rankAt(titles, promotion.level) : null;
  const road = useMemo(() => rewardRoad(rewards, who.kind), [rewards, who.kind]);
  const upcoming = road.filter((r) => (r.levels[who.kind] ?? 0) > level).slice(0, 3);
  // With everything open, the page shows the three best pieces instead.
  const best = upcoming.length === 0 ? road.slice(-3).reverse() : [];
  const wins = useMemo(() => quickWins(rules, who.kind, 4), [rules, who.kind]);
  return (
    <div className="hall-overview">
      <LevelHero who={who} />
      {who.kind === "pet" && <p className="hall-hint hall-wide">{t("society.level.pet_intro")}</p>}
      <Section title={t("society.hall.next_promotion_title")} aside={
        <button type="button" className="hall-link" onClick={() => open("ranks")}>{t("society.hall.ranks_all")}</button>}>
        {promotion && promoRank ? (
          <button type="button" className="hall-promotion" data-tier={RANK_INFO[promoRank].tier} onClick={() => open("ranks")}>
            <RankInsignia rank={promoRank} size={64} />
            <span>
              <strong>{t(`society.level.title.${promoRank}`)}</strong>
              <em>{RANK_INFO[promoRank].grade} · {t("society.level.locked_at").replace("{0}", String(promotion.level))}</em>
              <span className="hall-promotion-xp">{t("society.hall.xp_needed").replace("{0}", xpToReach(curve, subject?.xp ?? 0, promotion.level).toLocaleString())}</span>
            </span>
          </button>
        ) : <p className="hall-hint">{t("society.hall.top_title")}</p>}
      </Section>
      {road.length > 0 && <Section title={t(upcoming.length > 0 ? "society.hall.next_rewards" : "society.hall.best_pieces")} aside={
        <button type="button" className="hall-link" onClick={() => open("rewards")}>{t("society.hall.road_all")}</button>}>
        {upcoming.length === 0 && <p className="hall-hint">{t("society.hall.all_unlocked")}</p>}
        {upcoming.length + best.length > 0 && (
          <div className="hall-cards">
            {[...upcoming, ...best].map((reward) => (
              <RewardCard key={reward.rewardId} reward={reward} kind={who.kind} level={level}
                xpMissing={xpToReach(curve, subject?.xp ?? 0, reward.levels[who.kind] ?? 1)}
                onClick={() => open("studio", { reward: reward.rewardId })} />
            ))}
          </div>
        )}
        {upcoming.length > 0 && <p className="hall-hint">{t("society.hall.try_hint")}</p>}
      </Section>}
      <Section title={t("society.hall.quick_title")} aside={
        <button type="button" className="hall-link" onClick={() => open("guide")}>{t("society.hall.quick_all")}</button>}>
        <ul className="hall-rules">{wins.map((rule) => <RuleRow key={rule.source} rule={rule} />)}</ul>
      </Section>
    </div>
  );
}

function GuideTab({ who, petName }: { who: HallWho; petName: string }) {
  const t = useT();
  const rules = useProgression((s) => s.snapshot?.rules ?? NO_RULES);
  const curve = useProgression((s) => s.snapshot?.levelXp ?? NO_CURVE);
  const level = useProgression((s) => s.subjects[who.subjectId]?.level ?? 1);
  const costs = upcomingLevelCosts(curve, level, 3);
  const steps = [1, 2, 3] as const;
  const sections = [
    { kind: "person" as const, title: t("society.hall.section_you") },
    { kind: "pet" as const, title: t("society.hall.section_pet").replace("{0}", petName) },
    { kind: "agent" as const, title: t("society.hall.section_agents") },
  ];
  return (
    <div className="hall-guide">
      <ol className="hall-steps">
        {steps.map((n) => (
          <li key={n}>
            <span className="hall-step-n">{n}</span>
            <strong>{t(`society.hall.step${n}_title`)}</strong>
            <p>{t(`society.hall.step${n}_body`)}</p>
            {n === 2 && costs.length > 0 && (
              <ul className="hall-costs">
                {costs.map((c) => <li key={c.level}>{t("society.hall.cost_line").replace("{0}", String(c.level)).replace("{1}", String(c.xp))}</li>)}
              </ul>
            )}
          </li>
        ))}
      </ol>
      {sections.map((section) => (
        <Section key={section.kind} title={section.title} className="hall-guide-section">
          {groupRules(rules, section.kind).map((group) => (
            <div key={group.id} className="hall-rule-group">
              <h4>{t(`society.hall.group.${group.id}`).replace("{0}", petName)}</h4>
              <ul className="hall-rules">{group.rules.map((rule) => <RuleRow key={rule.source} rule={rule} />)}</ul>
            </div>
          ))}
        </Section>
      ))}
      <p className="hall-hint">{t("society.hall.limits_note")}</p>
    </div>
  );
}

function TeamTab({ agents }: { agents: readonly SocietyAgent[] }) {
  const t = useT();
  const subjects = useProgression((s) => s.subjects);
  const rows = useMemo(() => agents
    .filter((a) => a.agentId !== LEAD_AGENT_ID && a.tier !== "lead")
    .map((a) => ({ agent: a, subject: subjects[agentSubject(a.agentId)] }))
    .sort((a, b) => (b.subject?.xp ?? 0) - (a.subject?.xp ?? 0) || a.agent.name.localeCompare(b.agent.name)), [agents, subjects]);
  return (
    <div className="hall-team">
      <Section title={t("society.level.team")}>
        {rows.length === 0 ? <p className="hall-empty">{t("society.level.no_agents")}</p> : (
          <ol className="hall-ranking">
            {rows.map(({ agent, subject }, i) => (
              <li key={agent.agentId} data-podium={i < 3 ? i + 1 : undefined}>
                <span className="hall-rank">{i + 1}</span>
                <LevelChip kind="agent" level={subject?.level ?? 1} />
                <span className="hall-rank-body">
                  <span><strong>{agent.name}</strong> <em>{t(`society.level.title.${rankOf(subject?.title)}`)}</em></span>
                  <XpBar fraction={levelFraction(subject)} kind="agent" label={t("society.level.agent_xp_label").replace("{0}", agent.name)} />
                </span>
                <span className="hall-rank-xp">{t("society.level.total").replace("{0}", String(subject?.xp ?? 0))}</span>
              </li>
            ))}
          </ol>
        )}
      </Section>
      <p className="hall-hint">{t("society.hall.team_note")}</p>
    </div>
  );
}

export function LevelHallScreen({ agents, playerName, petName, playerLook }: {
  agents: readonly SocietyAgent[]; playerName: string; petName: string; playerLook: ToyLook;
}) {
  const t = useT();
  const tab = useProgression((s) => s.panel);
  const subjectChoice = useProgression((s) => s.hallSubject);
  const openPanel = useProgression((s) => s.openPanel);
  const setSubject = useProgression((s) => s.setHallSubject);
  const petId = useProgression((s) => s.petId);
  const loaded = useProgression((s) => !!s.snapshot);
  const companion = useCompanionPet((s) => s.pet);
  const sound = useLevelSound();
  const panel = useRef<HTMLDivElement>(null);
  const open = !!tab;
  // Focus moves into the screen when it opens, not on every page change (that would pull it off the clicked tab).
  useEffect(() => {
    if (open) panel.current?.focus({ preventScroll: true });
  }, [open]);
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.stopPropagation();
      openPanel(null);
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [open, openPanel]);
  // The team page has no subject; the subject switch steps aside there.
  const who: HallWho = useMemo(() => (subjectChoice === "pet"
    ? { who: "pet", kind: "pet", subjectId: petSubject(petId), name: petName, preview: { kind: "pet", pet: companion } }
    : { who: "person", kind: "person", subjectId: PERSON_SUBJECT, name: playerName, preview: { kind: "person", look: playerLook } }),
  [subjectChoice, petId, petName, companion, playerName, playerLook]);
  if (!tab) return null;
  return (
    <div className="hall-layer" data-office-ui onPointerDown={(e) => { if (e.target === e.currentTarget) openPanel(null); }}>
      {/* data-state="open" marks a modal for the office's keyboard guard: no walking or E behind the screen. */}
      <div ref={panel} className="hall" role="dialog" data-state="open" aria-modal="true" aria-labelledby="hall-title" tabIndex={-1}>
        <header className="hall-head">
          <div className="hall-brand">
            <span className="hall-brand-mark" aria-hidden><TabIcon path={TAB_ICON.ranks} size={20} /></span>
            <span>
              <h2 id="hall-title">{t("society.hall.title")}</h2>
              <small>{t("society.hall.subtitle")}</small>
            </span>
          </div>
          {tab !== "team" && (
            <div className="hall-subject" role="radiogroup" aria-label={t("society.hall.subject_label")}>
              {(["person", "pet"] as const).map((id) => (
                <button key={id} type="button" role="radio" aria-checked={subjectChoice === id} onClick={() => setSubject(id)}>
                  {id === "person" ? t("society.hall.subject_you") : petName}
                </button>
              ))}
            </div>
          )}
          <button type="button" className="hall-close" onClick={() => openPanel(null)} aria-label={t("society.office.close")}>
            <TabIcon path="M6 6l12 12M18 6L6 18" size={18} />
          </button>
        </header>
        <nav className="hall-tabs" role="tablist" aria-label={t("society.hall.title")}>
          {HALL_TABS.map((id) => (
            <button key={id} type="button" role="tab" aria-selected={tab === id} className="hall-tab" onClick={() => openPanel(id)}>
              <TabIcon path={TAB_ICON[id]} />
              <span>{t(`society.hall.tab_${id}`)}</span>
            </button>
          ))}
        </nav>
        <RewardTileTone.Provider value="light">
        <div className="hall-body" role="tabpanel" data-tab={tab}>
          {!loaded ? <p className="hall-empty">{t("society.hall.loading")}</p> : <>
            {tab === "overview" && <OverviewTab who={who} />}
            {tab === "ranks" && <RanksTab who={who} />}
            {tab === "studio" && <StudioTab key={who.who} who={who} />}
            {tab === "rewards" && <RewardsTab key={who.who} who={who} />}
            {tab === "guide" && <GuideTab who={who} petName={petName} />}
            {tab === "team" && <TeamTab agents={agents} />}
          </>}
        </div>
        </RewardTileTone.Provider>
        <footer className="hall-foot">
          <label className="hall-sound">
            <input type="checkbox" checked={sound.on} onChange={(e) => sound.set(e.target.checked)} />
            {t("society.level.sound")}
          </label>
          <span>{t("society.level.free_note")}</span>
        </footer>
      </div>
    </div>
  );
}
