/**
 * The rank ladder as one chart: every rank's insignia, name, pay grade and
 * the level it starts at, grouped by tier — enlisted, non-commissioned
 * officers, officers, general officers. Ranks already held are drawn in
 * full, the one held now is framed, the ones ahead are quiet silhouettes.
 * Choosing a rank ahead shows how much XP is still missing.
 */
import { useMemo } from "react";
import { useT } from "@/i18n";
import { useRunLocale } from "@/components/runs/format";
import type { RankTier } from "../levelCatalog";
import { rankAt } from "../levelCatalog";
import { RankInsignia } from "../insignia/RankInsignia";
import { useProgression } from "../progressionStore";
import { rankLadder, xpToReach } from "./hallModel";
import type { HallWho } from "./hallParts";

const NO_TITLES: { level: number; title: string }[] = [];
const NO_CURVE: number[] = [0];
const TIERS: readonly RankTier[] = ["enlisted", "nco", "officer", "general"];

export function RanksTab({ who }: { who: HallWho }) {
  const t = useT();
  const locale = useRunLocale();
  const titles = useProgression((s) => s.snapshot?.titles[who.kind] ?? NO_TITLES);
  const curve = useProgression((s) => s.snapshot?.levelXp ?? NO_CURVE);
  const subject = useProgression((s) => s.subjects[who.subjectId]);
  const level = subject?.level ?? 1;
  const held = rankAt(titles, level);
  const ladder = useMemo(() => rankLadder(titles), [titles]);
  const position = Math.max(0, ladder.findIndex((rung) => rung.rank === held));
  return (
    <div className="hall-ranks">
      <div className="hall-rank-summary">
        <RankInsignia rank={held} size={84} />
        <div>
          <span className="hall-hero-name">{t("society.hall.rank_held")}</span>
          <strong className="hall-hero-title">{t(`society.level.title.${held}`)}</strong>
          <span className="hall-rank-track" aria-hidden><i style={{ width: `${(position / Math.max(1, ladder.length - 1)) * 100}%` }} /></span>
          <span className="hall-hint">{t("society.hall.rank_position").replace("{0}", String(position + 1)).replace("{1}", String(ladder.length))}</span>
        </div>
      </div>
      <p className="hall-lead">{t("society.hall.ranks_body")}</p>
      {TIERS.map((tier) => {
        const rungs = ladder.filter((rung) => rung.tier === tier);
        if (rungs.length === 0) return null;
        return (
          <section key={tier} className="hall-rank-tier" data-tier={tier}>
            <h3>{t(`society.hall.tier.${tier}`)}</h3>
            <ol className="hall-rank-grid">
              {rungs.map((rung) => {
                const reached = level >= rung.level;
                const current = rung.rank === held;
                const missing = xpToReach(curve, subject?.xp ?? 0, rung.level);
                return (
                  <li key={rung.rank} className="hall-rank-cell" data-reached={reached || undefined} data-current={current || undefined}>
                    {current && <span className="hall-rank-now">{t("society.hall.rank_held")}</span>}
                    <span className="hall-rank-art">
                      <RankInsignia rank={rung.rank} size={78} dim={!reached} />
                      {rung.rank === "private" && <span className="hall-rank-none">{t("society.level.no_insignia")}</span>}
                    </span>
                    <strong>{t(`society.level.title.${rung.rank}`)}</strong>
                    <span className="hall-rank-grade">{rung.grade}</span>
                    <em>{reached ? t("society.hall.rank_from").replace("{0}", String(rung.level))
                      : `${t("society.hall.rank_from").replace("{0}", String(rung.level))} · ${t("society.hall.xp_needed").replace("{0}", missing.toLocaleString(locale))}`}</em>
                  </li>
                );
              })}
            </ol>
          </section>
        );
      })}
    </div>
  );
}
