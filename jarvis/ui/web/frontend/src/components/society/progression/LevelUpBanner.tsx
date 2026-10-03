/**
 * The level-up banner for the person and their pet, in four beats:
 * light rays and a flash behind (0 s), "LEVEL UP" stamps in with an
 * overshoot (0.1 s), the old number rolls out and the new one in (0.35 s),
 * then the new title and each unlocked reward card slide up one after the
 * other (0.8 s on). It stays about four seconds — longer when it has
 * rewards to show — and a click or Escape closes it early. Several level-ups
 * play one after another. Reduced motion shows the same card without motion.
 */
import { useEffect } from "react";
import { useT } from "@/i18n";
import { useProgression } from "./progressionStore";
import { RewardIcon } from "./RewardIcon";

const BASE_MS = 4000;

function titleAt(bands: { level: number; title: string }[] | undefined, level: number): string {
  let title = "";
  for (const band of bands ?? []) if (level >= band.level) title = band.title;
  return title;
}

const PER_REWARD_MS = 700;

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
  // The title line only when the climb crossed into a new title band.
  const titleChanged = !!banner.title && titleAt(bands, banner.previousLevel) !== banner.title;
  return (
    <div className="level-banner-layer" data-office-ui>
      <button type="button" key={banner.id} className="level-banner" data-kind={banner.kind} onClick={dismiss}
        aria-label={t("society.level.banner_aria").replace("{0}", who).replace("{1}", String(banner.level))}>
        <span className="level-banner-rays" aria-hidden />
        <span className="level-banner-flash" aria-hidden />
        {banner.away && <span className="level-banner-away">{t("society.level.while_away")}</span>}
        <span className="level-banner-kicker">{t("society.level.level_up")}</span>
        <span className="level-banner-number" aria-hidden>
          <span className="level-banner-old">{banner.previousLevel}</span>
          <span className="level-banner-new">{banner.level}</span>
        </span>
        <span className="level-banner-who">{who}</span>
        {titleChanged && (
          <span className="level-banner-title">
            {t("society.level.title_line").replace("{0}", t(`society.level.title.${banner.title}`))}
          </span>
        )}
        {banner.unlocked.length > 0 && (
          <span className="level-banner-rewards">
            <span className="level-banner-unlocked">{t("society.level.unlocked")}</span>
            {banner.unlocked.map((reward, i) => (
              <span key={reward} className="level-banner-reward" style={{ animationDelay: `${0.9 + i * 0.18}s` }}>
                <RewardIcon reward={reward} size={34} />
                <span>{t(`society.level.reward.${reward}`)}</span>
              </span>
            ))}
            <span className="level-banner-hint">{t(pet ? "society.level.equip_hint_pet" : "society.level.equip_hint")}</span>
          </span>
        )}
      </button>
    </div>
  );
}
