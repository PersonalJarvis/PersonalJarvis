import { AnimatePresence, LayoutGroup, MotionConfig, motion } from "framer-motion";
import { ArrowLeft } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { MascotGigi, type MascotAction } from "@/components/MascotGigi";
import type { useOnboarding } from "@/hooks/useOnboarding";
import { fill, setUiLanguage, useLocaleChunk, useT, useUiLanguage, type UiLanguage } from "@/i18n";
import { cn } from "@/lib/utils";
import {
  beatsFor,
  CARD_WIDTH,
  MASCOT_CUE,
  nextBeat,
  previousBeat,
  resumeBeat,
  type BeatId,
} from "./beats";
import { AgentsBeat } from "./beats/AgentsBeat";
import { BrainBeat } from "./beats/BrainBeat";
import { HelloBeat } from "./beats/HelloBeat";
import { PermissionsBeat } from "./beats/PermissionsBeat";
import { ReadyBeat } from "./beats/ReadyBeat";
import { VoiceBeat } from "./beats/VoiceBeat";
import { ProgressDots } from "./ProgressDots";
import { EASE_OUT, QuietAction } from "./ui";

/** What a beat hands the ready beat: one line of what was set up, one of what was not. */
export interface BeatResult {
  summary: string | null;
  gap: string | null;
}

export interface BeatProps {
  onb: ReturnType<typeof useOnboarding>;
  /** Go on to the next beat. */
  next: () => void;
  /** Go on and record the beat as skipped. */
  skip: () => void;
  /** Report this beat's outcome for the ready beat's review. */
  report: (result: BeatResult | null) => void;
  /** Everything the beats before reported, keyed by beat. */
  results: Partial<Record<BeatId, BeatResult>>;
  /** Ask the mascot for a one-shot move — a success deserves a jump. */
  cheer: (action?: MascotAction) => void;
  /** The assistant's name as chosen on the voice beat, if one was. */
  chosenName: string | null;
  setChosenName: (name: string | null) => void;
}

const LANGS: UiLanguage[] = ["en", "de", "es"];

/** The guide once its strings are resident — they live in a lazy locale chunk. */
export function WelcomeStage({ onb }: { onb: ReturnType<typeof useOnboarding> }) {
  const ready = useLocaleChunk("onboarding");
  return ready ? <WelcomeFlow onb={onb} /> : null;
}

/**
 * The first-run guide: ONE card on the app's own ground that changes shape
 * from beat to beat instead of a stack of screens.
 *
 * The card keeps its frame — mascot, title, the dots, Back — and only its
 * width and body change. On the first beat the mascot sits large above a
 * centred title; from the second on it glides into a small badge beside the
 * title. The one way forward is always the full-width action at the foot of
 * the beat; everything else is quiet text.
 *
 * Progress is persisted on every beat (`POST /api/onboarding/step`) and a
 * reload resumes on the beat it left — but never past the consent.
 */
export function WelcomeFlow({ onb }: { onb: ReturnType<typeof useOnboarding> }) {
  const t = useT();
  const lang = useUiLanguage();
  const [platform, setPlatform] = useState<string | null>(null);
  const beats = useMemo(() => beatsFor(platform), [platform]);
  const [beat, setBeat] = useState<BeatId>(() =>
    resumeBeat(beatsFor(null), onb.state?.current_step ?? null, Boolean(onb.state?.terms.accepted)),
  );
  const [skipped, setSkipped] = useState<string[]>(() => onb.state?.skipped_steps ?? []);
  const [results, setResults] = useState<Partial<Record<BeatId, BeatResult>>>({});
  const [chosenName, setChosenName] = useState<string | null>(null);
  const [cue, setCue] = useState<{ action: MascotAction; key: number }>({
    action: MASCOT_CUE[beat],
    key: 0,
  });
  const cardRef = useRef<HTMLDivElement>(null);

  // Which beats exist depends on the platform (macOS asks for permissions).
  // A failed probe keeps the permissions beat out — Settings stays the
  // recovery path for it.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const res = await fetch("/api/permissions/status");
        if (!res.ok) return;
        const data = (await res.json()) as { platform?: string };
        if (!cancelled && typeof data.platform === "string") setPlatform(data.platform);
      } catch {
        // Probe is best-effort: without it the beat list simply has no permissions beat.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const cheer = useCallback((action: MascotAction = "jump") => {
    setCue((c) => ({ action, key: c.key + 1 }));
  }, []);

  const goTo = useCallback(
    (target: BeatId, nextSkipped: string[]) => {
      setBeat(target);
      setCue((c) => ({ action: MASCOT_CUE[target], key: c.key + 1 }));
      void onb.saveStep(target, nextSkipped);
    },
    [onb],
  );

  const next = useCallback(() => {
    const target = nextBeat(beats, beat);
    if (target) goTo(target, skipped);
  }, [beats, beat, goTo, skipped]);

  const skip = useCallback(() => {
    const nextSkipped = skipped.includes(beat) ? skipped : [...skipped, beat];
    setSkipped(nextSkipped);
    const target = nextBeat(beats, beat);
    if (target) goTo(target, nextSkipped);
  }, [beats, beat, goTo, skipped]);

  const back = useMemo(() => {
    const target = previousBeat(beats, beat);
    // The consent is not a place to go back to once it is given.
    if (!target || target === "welcome") return null;
    return () => goTo(target, skipped);
  }, [beats, beat, goTo, skipped]);

  const report = useCallback(
    (result: BeatResult | null) => {
      setResults((prev) => {
        const current = prev[beat];
        if (
          (current?.summary ?? null) === (result?.summary ?? null) &&
          (current?.gap ?? null) === (result?.gap ?? null)
        ) {
          return prev;
        }
        return { ...prev, [beat]: result ?? undefined };
      });
    },
    [beat],
  );

  // Land the keyboard in the beat: the first text field, else the primary
  // action — never the card itself, never a quiet link.
  useEffect(() => {
    const timer = window.setTimeout(() => {
      const root = cardRef.current;
      if (!root || root.contains(document.activeElement)) return;
      const target =
        root.querySelector<HTMLElement>("input[type='text']:not([disabled]), input[type='password']:not([disabled])") ??
        root.querySelector<HTMLElement>("[data-testid='onboarding-primary']:not([disabled])");
      target?.focus({ preventScroll: true });
    }, 380);
    return () => window.clearTimeout(timer);
  }, [beat]);

  const index = Math.max(0, beats.indexOf(beat));
  const first = beat === "welcome";
  const props: BeatProps = {
    onb,
    next,
    skip,
    report,
    results,
    cheer,
    chosenName,
    setChosenName,
  };

  const title = t(`first_run.${beat}.title`);
  const lede = t(`first_run.${beat}.lede`);

  return (
    <MotionConfig reducedMotion="user">
      <div
        className="relative flex min-h-full w-full items-start justify-center px-4 py-8 sm:items-center sm:px-8 sm:py-10"
        data-testid="onboarding-flow"
        data-beat={beat}
      >
        {/* The room behind the card: a faint signal-blue light from above, so
            the stage reads as a place and not as a blank page. Built from the
            accent channels, so it follows the theme. */}
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0"
          style={{
            background:
              "radial-gradient(90% 60% at 50% 0%, rgb(var(--accent-rgb) / 0.08), transparent 70%)",
          }}
        />
        <LayoutGroup>
          <motion.div
            ref={cardRef}
            layout
            transition={{ layout: { duration: 0.42, ease: EASE_OUT } }}
            className="relative w-full rounded-2xl border border-border bg-card p-6 shadow-float sm:p-8"
            style={{ maxWidth: CARD_WIDTH[beat] }}
            data-testid={`onboarding-step-${beat}`}
          >
            {first && (
              <div
                className="absolute right-4 top-4 flex items-center gap-0.5 rounded-full border border-border p-0.5"
                role="radiogroup"
                aria-label={t("first_run.welcome.language")}
              >
                {LANGS.map((code) => (
                  <button
                    key={code}
                    type="button"
                    role="radio"
                    aria-checked={lang === code}
                    onClick={() => setUiLanguage(code)}
                    data-testid={`onboarding-lang-${code}`}
                    className={cn(
                      "rounded-full px-2.5 py-1 text-xs transition-colors",
                      "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                      lang === code
                        ? "bg-secondary font-medium text-foreground"
                        : "text-muted-foreground hover:text-foreground",
                    )}
                  >
                    {t(`first_run.welcome.lang_${code}`)}
                  </button>
                ))}
              </div>
            )}

            {/* Every direct child of the resizing card carries `layout`, so
                framer corrects the card's scale for it and text never squashes
                while the width glides to the next beat. */}
            <motion.header
              layout
              transition={{ layout: { duration: 0.42, ease: EASE_OUT } }}
              className={cn(first ? "flex flex-col items-center pt-1 text-center" : "flex items-start gap-3 pr-2")}
            >
              <motion.div layoutId="onboarding-mascot" className="shrink-0" transition={{ duration: 0.42, ease: EASE_OUT }}>
                <MascotGigi
                  size={first ? 76 : 40}
                  reactToVoice={false}
                  enableComments={false}
                  cue={cue}
                />
              </motion.div>
              <motion.div layout="position" className={cn("min-w-0", first ? "mt-3" : "pt-0.5")}>
                <h2
                  className={cn(
                    "font-semibold tracking-tight text-foreground",
                    first ? "text-2xl" : "text-lg",
                  )}
                  id="onboarding-title"
                  data-testid="onboarding-title"
                >
                  {title}
                </h2>
                <p
                  className={cn(
                    "mt-1 text-sm leading-relaxed text-muted-foreground",
                    first && "mx-auto max-w-sm",
                  )}
                >
                  {lede}
                </p>
              </motion.div>
            </motion.header>

            <AnimatePresence mode="wait" initial={false}>
              <motion.div
                key={beat}
                layout="position"
                initial={{ opacity: 0, x: 12 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: -12 }}
                transition={{ duration: 0.24, ease: EASE_OUT }}
                className="mt-5"
              >
                {beat === "welcome" && <HelloBeat {...props} />}
                {beat === "brain" && <BrainBeat {...props} />}
                {beat === "agents" && <AgentsBeat {...props} />}
                {beat === "permissions" && <PermissionsBeat {...props} />}
                {beat === "voice" && <VoiceBeat {...props} />}
                {beat === "ready" && <ReadyBeat {...props} />}
              </motion.div>
            </AnimatePresence>

            <motion.footer
              layout
              transition={{ layout: { duration: 0.42, ease: EASE_OUT } }}
              className="mt-6 grid grid-cols-[1fr_auto_1fr] items-center gap-3"
            >
              <div>
                {back && (
                  <QuietAction onClick={back} testId="onboarding-back" className="inline-flex items-center gap-1.5">
                    <ArrowLeft aria-hidden className="h-3.5 w-3.5" />
                    {t("first_run.back")}
                  </QuietAction>
                )}
              </div>
              <ProgressDots count={beats.length} index={index} />
              <p className="text-right text-xs text-muted-foreground" aria-live="polite">
                {fill(t("first_run.step_of"), { current: index + 1, total: beats.length })}
              </p>
            </motion.footer>
          </motion.div>
        </LayoutGroup>
      </div>
    </MotionConfig>
  );
}
