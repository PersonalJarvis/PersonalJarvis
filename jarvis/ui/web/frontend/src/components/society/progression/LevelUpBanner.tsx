/**
 * The promotion card for the person and their pet, styled like a citation:
 * midnight blue, a fine double gold rule, no flashing. In four beats:
 *
 * 1. the card settles in and "Promoted" (or "Level up") is set (0–0.4 s);
 * 2. on a promotion the old insignia stands for a moment, then steps aside
 *    and dims while the new one is laid on piece by piece — each chevron,
 *    rocker, bar or star in turn — and catches the light once (0.5–1.6 s);
 *    between two promotions the insignia stays and the level number rolls;
 * 3. the rank name, its grade and the level are set below (from 1.2 s);
 * 4. each uniform piece the promotion unlocked slides up after it.
 *
 * It stays about five seconds — longer with pieces to show — and a click or
 * Escape closes it early. Several level-ups play one after another. Reduced
 * motion shows the same card without motion.
 */
import { useEffect } from "react";
import { useT } from "@/i18n";
import { RANK_INFO, rankAt, rankOf } from "./levelCatalog";
import { RankInsignia } from "./insignia/RankInsignia";
import { useProgression } from "./progressionStore";
import { RewardIcon } from "./RewardIcon";

const BASE_MS = 5200;
const PER_REWARD_MS = 800;

export function LevelUpBanner({ playerName, petName }: { playerName: string; petName: string }) {
  const t = useT();
  const banner = useProgression((s) => s.banners[0]);
  const dismiss = useProgression((s) => s.dismissBanner);
  const bands = useProgression((s) => (banner ? s.snapshot?.titles[banner.kind] : undefined));

  useEffect(() => {
    if (!banner) return;
    const timer = setTimeout(dismiss, BASE_MS + banner.unlocked.length * PER_REWARD_MS);
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") { event.stopPropagation(); dismiss(); } };
    window.addEventListener("keydown", onKey, true);
    return () => { clearTimeout(timer); window.removeEventListener("keydown", onKey, true); };
  }, [banner, dismiss]);

  if (!banner) return null;
  const pet = banner.kind === "pet";
  const who = pet ? t("society.level.pet_up").replace("{0}", petName) : playerName;
  const rank = rankAt(bands, banner.level);
  const before = rankAt(bands, banner.previousLevel);
  const promoted = before !== rank;
  const info = RANK_INFO[rank];
  const next = bands?.find((band) => band.level > banner.level);
  return (
    <div className="level-banner-layer" data-office-ui>
      <button type="button" key={banner.id} className="promo" data-tier={info.tier} data-promoted={promoted || undefined} onClick={dismiss}
        aria-label={(promoted ? t("society.level.promoted_aria") : t("society.level.banner_aria"))
          .replace("{0}", who).replace("{1}", String(banner.level)).replace("{2}", t(`society.level.title.${rank}`))}>
        <span className="promo-rule" aria-hidden />
        {banner.away && <span className="promo-away">{t("society.level.while_away")}</span>}
        <span className="promo-kicker">{promoted ? t("society.level.promoted") : t("society.level.level_up")}</span>
        <span className="promo-who">{who}</span>
        <span className="promo-stage" aria-hidden>
          {promoted && <span className="promo-old"><RankInsignia rank={before} size={64} /></span>}
          <span className="promo-new"><RankInsignia rank={rank} size={118} reveal={promoted} /></span>
        </span>
        <span className="promo-rank">{t(`society.level.title.${rank}`)}</span>
        <span className="promo-grade">
          <span>{info.grade}</span>
          <span className="promo-dot" aria-hidden />
          <span className="promo-level">
            {t("society.level.lv").replace("{0}", "")}
            <span className="promo-number">
              <span className="promo-number-old">{banner.previousLevel}</span>
              <span className="promo-number-new">{banner.level}</span>
            </span>
          </span>
        </span>
        {!promoted && next && (
          <span className="promo-next">
            {t("society.level.next_promotion").replace("{0}", t(`society.level.title.${rankOf(next.title)}`)).replace("{1}", String(next.level))}
          </span>
        )}
        {banner.unlocked.length > 0 && (
          <span className="promo-rewards">
            <span className="promo-unlocked">{t("society.level.unlocked")}</span>
            {banner.unlocked.map((reward, i) => (
              <span key={reward} className="promo-reward" style={{ animationDelay: `${1.7 + i * 0.22}s` }}>
                <RewardIcon reward={reward} size={40} />
                <span>{t(`society.level.reward.${reward}`)}</span>
              </span>
            ))}
            <span className="promo-hint">{t("society.level.equip_hint")}</span>
          </span>
        )}
      </button>
    </div>
  );
}
