import { Keyboard, Loader2, Mic, Square } from "lucide-react";

import type { DictationStatus } from "@/hooks/useDictation";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { formatCombo } from "@/views/settings/KeybindRow";
import heroImage from "@/assets/voice/dictation-hero.webp";

/**
 * The opening banner of the Dictation tab — the one thing a person needs to
 * know ("hold this key and speak") set large enough to be read from across the
 * desk, with the one action on the screen beside it.
 *
 * The banner is a photographic plate, and a photograph does not change with
 * the theme: it is the same dark scene in light and in dark mode. That is why
 * this component — and only this one — writes its inks as fixed white-on-dark
 * literals instead of theme tokens. A token-driven title would turn dark on a
 * light theme and vanish into the photo. The plate's own edge is a
 * `--border` ring, so on a dark page it still separates from the ground.
 *
 * The left of the plate is a solid fall-off into near-black (the image itself
 * is composed that way, and a gradient guarantees it on any aspect ratio), so
 * the text always sits on a calm surface rather than on the picture.
 */
export interface DictationHeroProps {
  status: DictationStatus | null;
  loading: boolean;
  busy: boolean;
  onToggle: () => void;
  onOpenShortcuts: () => void;
}

/** The plate's ground; also the gradient's start so the seam is invisible. */
const PLATE = "#050507";

export function DictationHero({
  status,
  loading,
  busy,
  onToggle,
  onOpenShortcuts,
}: DictationHeroProps) {
  const t = useT();
  const active = Boolean(status?.active);
  const available = Boolean(status?.available);
  const hotkey = status?.hotkey ?? "";
  const handsFree = status?.hotkey_toggle ?? "";

  // Life / neutral / fault — the same three states the status dot has always
  // had, now as a small pill on the plate instead of a separate card.
  const stateLabel = active
    ? t("dictation.state_active")
    : available
      ? t("dictation.state_ready")
      : t("dictation.state_unavailable");
  const dotFill = active
    ? "bg-success motion-safe:animate-pulse"
    : available
      ? "bg-white/70"
      : "bg-destructive";

  return (
    <section
      className="relative isolate overflow-hidden rounded-2xl ring-1 ring-border"
      style={{ backgroundColor: PLATE }}
      data-testid="dictation-hero"
    >
      <img
        src={heroImage}
        alt=""
        aria-hidden="true"
        draggable={false}
        className="pointer-events-none absolute inset-y-0 right-0 h-full w-full select-none object-cover object-right sm:w-[78%]"
      />
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0"
        style={{
          background: `linear-gradient(90deg, ${PLATE} 0%, ${PLATE} 30%, rgb(5 5 7 / 0.82) 48%, rgb(5 5 7 / 0.25) 72%, rgb(5 5 7 / 0) 100%)`,
        }}
      />

      <div className="relative flex min-h-[232px] flex-col justify-center gap-5 px-6 py-7 sm:px-9">
        <span
          className="inline-flex w-fit items-center gap-2 rounded-full bg-white/10 px-2.5 py-1 text-xs font-medium text-white/85 ring-1 ring-white/10 backdrop-blur-sm"
          data-testid="dictation-state"
        >
          <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", dotFill)} />
          {loading ? t("dictation.loading") : stateLabel}
        </span>

        <div className="max-w-[520px]">
          <h2 className="text-[30px] font-semibold leading-[36px] tracking-[-0.02em] text-white [text-wrap:balance]">
            {loading ? (
              <span className="inline-block h-8 w-72 max-w-full animate-pulse rounded-md bg-white/10" />
            ) : active ? (
              t("dictation.hero_listening")
            ) : available && hotkey ? (
              <HoldTitle template={t("dictation.hero_hold")} combo={hotkey} />
            ) : (
              t("dictation.hero_no_key")
            )}
          </h2>
          <p className="mt-2 text-base text-white/70">
            {!loading && !available
              ? status?.reason || t("dictation.state_unavailable")
              : active
                ? t("dictation.hero_body_active")
                : t("dictation.hero_body")}
          </p>
          {available && !active && handsFree && (
            <p className="mt-1 text-sm text-white/50">
              {t("dictation.hero_hands_free").replace("{0}", formatCombo(handsFree))}
            </p>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            disabled={loading || busy || !available}
            data-testid="dictation-toggle"
            onClick={onToggle}
            className="inline-flex h-9 items-center gap-2 rounded-md bg-white px-4 text-base font-medium text-neutral-900 transition-colors hover:bg-white/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/60 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy ? (
              <Loader2
                aria-hidden="true"
                className="h-4 w-4 animate-spin motion-reduce:animate-none"
              />
            ) : active ? (
              <Square aria-hidden="true" className="h-4 w-4" />
            ) : (
              <Mic aria-hidden="true" className="h-4 w-4" />
            )}
            {active ? t("dictation.stop") : t("dictation.start")}
          </button>
          {!loading && available && (
            <button
              type="button"
              onClick={onOpenShortcuts}
              data-testid="dictation-open-shortcuts"
              className="inline-flex h-9 items-center gap-2 rounded-md px-3 text-base font-medium text-white/80 transition-colors hover:bg-white/10 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/60"
            >
              <Keyboard aria-hidden="true" className="h-4 w-4" />
              {hotkey ? t("dictation.hero_shortcuts") : t("dictation.hero_assign")}
            </button>
          )}
        </div>
      </div>
    </section>
  );
}

/**
 * "Hold [Ctrl + Alt] to dictate" with the key set apart from the sentence.
 *
 * The locale owns the word order (some languages put the key in the middle
 * of the sentence, others at its end), so the template is split at its placeholder instead of
 * being glued together from fragments here.
 */
function HoldTitle({ template, combo }: { template: string; combo: string }) {
  const [before, after = ""] = template.split("{0}");
  return (
    <>
      {before}
      <span className="whitespace-nowrap rounded-lg bg-white/10 px-2 py-0.5 font-semibold text-white ring-1 ring-white/15">
        {formatCombo(combo)}
      </span>
      {after}
    </>
  );
}
