/**
 * The walk: after the setup window, the user's pet walks the REAL app and
 * explains each section in a speech bubble — chat, voice, agents, the Agentic
 * IDE, plugins and where the wake word lives (on macOS also the permissions).
 *
 * Purely explanatory: it only navigates, never presses or writes anything.
 * The stop survives a remount (a reload, a late locale load) through session
 * storage. When it ends it leaves the app on the chat, on the home surface the
 * user had before.
 */
import { ArrowLeft } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { FOCUS_RING } from "@/components/agentic/controls";
import { fill, useLocaleChunk, useT } from "@/i18n";
import type { HomeSurface } from "@/lib/homeSurface";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";
import { useHomeStore } from "@/store/home";
import { PetSays, useGuidePet } from "../pet/GuidePet";
import { Spotlight } from "../tour/Spotlight";
import { QuietAction } from "../ui";
import { walkStopsFor } from "./setupSteps";
import { useAnchorRect } from "./useAnchorRect";
import { usePlatform } from "./usePlatform";

const BUBBLE_W = 500;
const STOP_KEY = "jarvis.setup.walkStop";

function readStop(count: number): number {
  try {
    const n = Number(window.sessionStorage.getItem(STOP_KEY));
    return Number.isInteger(n) && n > 0 && n < count ? n : 0;
  } catch {
    return 0;
  }
}

function saveStop(n: number | null): void {
  try {
    if (n === null) window.sessionStorage.removeItem(STOP_KEY);
    else window.sessionStorage.setItem(STOP_KEY, String(n));
  } catch {
    // Storage may be blocked; the walk then starts over after a reload.
  }
}

export function SectionWalk({ onDone }: { onDone: () => void }) {
  const t = useT();
  const ready = useLocaleChunk("onboarding");
  const pet = useGuidePet();
  const platform = usePlatform();
  const stops = useMemo(() => walkStopsFor(platform), [platform]);
  const [index, setIndexRaw] = useState(() => readStop(stops.length));
  const originalSurface = useRef<HomeSurface>(useHomeStore.getState().surface);
  const doneRef = useRef(false);
  const setIndex = useCallback((update: (i: number) => number) => {
    setIndexRaw((i) => {
      const n = update(i);
      saveStop(n);
      return n;
    });
  }, []);
  const stop = stops[Math.min(index, stops.length - 1)];
  const last = index >= stops.length - 1;

  // Bring the stop's place on screen first: the composer and the voice bar
  // are two surfaces of the chat home.
  useEffect(() => {
    if (!ready) return;
    const nav = useEventStore.getState();
    if (stop.section && nav.activeSection !== stop.section) nav.setActiveSection(stop.section);
    if (stop.surface && useHomeStore.getState().surface !== stop.surface) {
      useHomeStore.getState().setSurface(stop.surface);
    }
  }, [ready, stop]);

  const rect = useAnchorRect(ready ? stop.anchor : undefined, Boolean(stop.scrollTo), stop.id);

  const done = useCallback(() => {
    if (doneRef.current) return;
    doneRef.current = true;
    saveStop(null);
    // Leave the app where a new user expects it: home, on the surface they had.
    const nav = useEventStore.getState();
    if (nav.activeSection !== "chats") nav.setActiveSection("chats");
    if (useHomeStore.getState().surface !== originalSurface.current) {
      useHomeStore.getState().setSurface(originalSurface.current);
    }
    onDone();
  }, [onDone]);

  const next = useCallback(() => {
    if (last) done();
    else setIndex((i) => Math.min(stops.length - 1, i + 1));
  }, [last, done, setIndex, stops.length]);

  // Arrow keys move through the walk.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "ArrowRight") next();
      else if (event.key === "ArrowLeft") setIndex((i) => Math.max(0, i - 1));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [next, setIndex]);

  if (!ready) return null;

  const text = fill(t(`first_run.tour.stops.${stop.id}`), { pet: pet?.name ?? "Gigi" });
  const heading = t(`first_run.tour.labels.${stop.id}`);

  return (
    <Spotlight rect={rect} placement={stop.placement} blocking cardWidth={BUBBLE_W}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label={t("first_run.tour.title")}
        data-testid="setup-card"
        data-step="tour"
        data-stop={stop.id}
      >
        <PetSays
          key={stop.id}
          text={text}
          state={stop.pet}
          testId="walk-say"
          heading={
            <p className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">
              {heading}
            </p>
          }
        >
          <div className="mt-3 flex items-center gap-3">
            <button
              type="button"
              onClick={next}
              data-testid="walk-next"
              className={cn(
                "h-8 rounded-lg bg-accent px-3.5 text-sm font-medium text-accent-foreground transition-opacity hover:opacity-90",
                FOCUS_RING,
              )}
            >
              {last ? t("first_run.tour.finish") : t("first_run.tour.next")}
            </button>
            {index > 0 && (
              <QuietAction
                onClick={() => setIndex((i) => i - 1)}
                className="inline-flex items-center gap-1 text-xs"
                testId="walk-prev"
              >
                <ArrowLeft aria-hidden className="h-3 w-3" />
                {t("first_run.back")}
              </QuietAction>
            )}
            <span
              className="ml-auto flex items-center gap-1"
              role="img"
              aria-label={fill(t("first_run.step_of"), { current: index + 1, total: stops.length })}
              data-testid="walk-progress"
            >
              {stops.map((s, i) => (
                <span
                  key={s.id}
                  className={cn(
                    "h-1.5 rounded-full transition-all duration-200",
                    i === index ? "w-4 bg-accent" : i < index ? "w-1.5 bg-foreground/50" : "w-1.5 bg-border-strong",
                  )}
                />
              ))}
            </span>
            {!last && (
              <QuietAction onClick={done} className="text-xs" testId="walk-skip">
                {t("first_run.tour.skip")}
              </QuietAction>
            )}
          </div>
        </PetSays>
      </div>
    </Spotlight>
  );
}
