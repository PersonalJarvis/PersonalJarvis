/**
 * Pieces the Level Hall screen's pages share: the tab icons, the level hero,
 * reward cards, the rank tag, the padlock and the XP rule rows.
 */
import type { ReactNode } from "react";
import { useT } from "@/i18n";
import { levelFraction } from "../cosmetics";
import { LevelRing, XpBar } from "../LevelHud";
import { RANK_INFO, rankAt, rankOf, type RankId } from "../levelCatalog";
import { RankInsignia } from "../insignia/RankInsignia";
import type { RewardRow, XpRuleRow } from "../progressionApi";
import { useProgression } from "../progressionStore";
import { RewardIcon } from "../RewardIcon";
import { nextTitle } from "./hallModel";
import type { PreviewSubject } from "./LoadoutPreview";
import { ruleLimit } from "./ruleLimit";

const NO_TITLES: { level: number; title: string }[] = [];

export function TabIcon({ path, size = 16 }: { path: string; size?: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} aria-hidden fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
      <path d={path} />
    </svg>
  );
}

/** Whose page this is, everything the pages need to know about them. */
export interface HallWho {
  who: "person" | "pet";
  kind: "person" | "pet";
  subjectId: string;
  name: string;
  preview: PreviewSubject;
}

export function Section({ title, aside, children, className }: { title: string; aside?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`hall-section${className ? ` ${className}` : ""}`}>
      <header><h3>{title}</h3>{aside}</header>
      {children}
    </section>
  );
}

/** The level hero: ring, name, title, bar and what is missing. */
export function LevelHero({ who, compact = false }: { who: HallWho; compact?: boolean }) {
  const t = useT();
  const subject = useProgression((s) => s.subjects[who.subjectId]);
  const titles = useProgression((s) => s.snapshot?.titles[who.kind] ?? NO_TITLES);
  const level = subject?.level ?? 1;
  const fraction = levelFraction(subject);
  const atCap = !!subject && subject.xpForNext <= 0;
  const upcoming = nextTitle(titles, level);
  return (
    <div className="hall-hero" data-kind={who.kind} data-compact={compact || undefined}>
      <LevelRing kind={who.kind} level={level} fraction={fraction} size={compact ? 76 : 120} />
      <div className="hall-hero-body">
        <span className="hall-hero-name">{who.name}</span>
        <span className="hall-hero-title">
          <span>{t(`society.level.title.${rankAt(titles, level)}`)}</span>
          <small>{RANK_INFO[rankAt(titles, level)].grade}</small>
        </span>
        <XpBar fraction={fraction} kind={who.kind} label={t("society.level.xp_label")} />
        <span className="hall-hero-xp">
          <b>{atCap ? t("society.level.max") : t("society.level.xp_of").replace("{0}", String(subject?.xpIntoLevel ?? 0)).replace("{1}", String(subject?.xpForNext ?? 40))}</b>
          {!atCap && <span>{t("society.level.to_next").replace("{0}", String(subject ? subject.xpForNext - subject.xpIntoLevel : 40)).replace("{1}", String(level + 1))}</span>}
        </span>
        {!compact && (
          <span className="hall-hero-meta">
            <span>{t("society.level.total").replace("{0}", String(subject?.xp ?? 0))}</span>
            <span>{upcoming
              ? t("society.hall.next_title").replace("{0}", t(`society.level.title.${rankOf(upcoming.title)}`)).replace("{1}", String(upcoming.level))
              : t("society.hall.top_title")}</span>
          </span>
        )}
      </div>
    </div>
  );
}

/** The rank a level brings, as a small tag: its insignia and name. */
export function RankTag({ rank }: { rank: RankId }) {
  const t = useT();
  return (
    <span className="hall-rank-tag" data-tier={RANK_INFO[rank].tier}>
      <RankInsignia rank={rank} size={13} />
      {t(`society.level.title.${rank}`)}
    </span>
  );
}

/** A reward as a card: its picture, name, slot, the rank it comes with, and whether it is open. */
export function RewardCard({ reward, kind, level, onClick, pressed, badge, xpMissing }: {
  reward: RewardRow; kind: "person" | "pet"; level: number; onClick?: () => void; pressed?: boolean; badge?: string; xpMissing?: number;
}) {
  const t = useT();
  const titles = useProgression((s) => s.snapshot?.titles[kind] ?? NO_TITLES);
  const at = reward.levels[kind] ?? 0;
  const open = level >= at;
  const body = (
    <>
      <RewardIcon reward={reward.rewardId} size={48} locked={!open} />
      <span className="hall-card-text">
        <strong>{t(`society.level.reward.${reward.rewardId}`)}</strong>
        <span className="hall-card-meta">
          <span>{t(`society.level.slot.${reward.slot}`)}</span>
          <RankTag rank={rankAt(titles, at)} />
        </span>
        <span className="hall-card-state" data-open={open || undefined}>
          {badge ?? (open ? t("society.hall.unlocked") : xpMissing !== undefined
            ? `${t("society.level.locked_at").replace("{0}", String(at))} · ${t("society.hall.xp_needed").replace("{0}", xpMissing.toLocaleString())}`
            : t("society.level.locked_at").replace("{0}", String(at)))}
        </span>
      </span>
      {!open && <LockGlyph />}
    </>
  );
  return onClick
    ? <button type="button" className="hall-card" data-open={open || undefined} aria-pressed={pressed} onClick={onClick}>{body}</button>
    : <div className="hall-card" data-open={open || undefined}>{body}</div>;
}

export function LockGlyph() {
  return (
    <svg className="hall-lock" viewBox="0 0 24 24" width={14} height={14} aria-hidden fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round">
      <rect x="5" y="11" width="14" height="9" rx="2" /><path d="M8 11V8a4 4 0 0 1 8 0v3" />
    </svg>
  );
}

export function RuleRow({ rule }: { rule: XpRuleRow }) {
  const t = useT();
  const limit = ruleLimit(rule, t);
  return (
    <li className="hall-rule">
      <span className="hall-rule-xp">+{rule.xp}</span>
      <span className="hall-rule-text">
        <span>{t(`society.level.source.${rule.source}`)}</span>
        {limit && <em>{limit}</em>}
      </span>
    </li>
  );
}
