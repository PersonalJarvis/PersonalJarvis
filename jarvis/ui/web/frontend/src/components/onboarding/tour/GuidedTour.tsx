import { useCallback, useEffect, useRef, useState } from "react";
import { MascotGigi } from "@/components/MascotGigi";
import { fill, useLocaleChunk, useT } from "@/i18n";
import { useEventStore } from "@/store/events";
import { useHomeStore } from "@/store/home";
import type { HomeSurface } from "@/lib/homeSurface";
import { cn } from "@/lib/utils";
import { FOCUS_RING } from "@/components/agentic/controls";
import { QuietAction } from "../ui";
import { Spotlight } from "./Spotlight";
import { nextStepIndex, TOUR_STEPS, type Rect, type TourEffect } from "./tourSteps";

/** How long a step waits for its element to appear (a section may still be loading). */
const FIND_TIMEOUT_MS = 2500;
const FIND_POLL_MS = 100;
/** Re-measure while shown — cheap, and it catches layout shifts no observer reports. */
const TRACK_MS = 400;
const STEP_KEY = "jarvis.tour.step";

function findAnchor(anchor: string): HTMLElement | null {
  const nodes = document.querySelectorAll<HTMLElement>(`[data-tour="${anchor}"]`);
  for (const el of nodes) {
    const r = el.getBoundingClientRect();
    if (r.width > 0 && r.height > 0) return el;
  }
  return null;
}

function rectOf(el: HTMLElement): Rect {
  const r = el.getBoundingClientRect();
  return { x: r.left, y: r.top, w: r.width, h: r.height };
}

function sameRect(a: Rect | null, b: Rect | null): boolean {
  if (!a || !b) return a === b;
  return (
    Math.abs(a.x - b.x) < 0.5 &&
    Math.abs(a.y - b.y) < 0.5 &&
    Math.abs(a.w - b.w) < 0.5 &&
    Math.abs(a.h - b.h) < 0.5
  );
}

function readSavedStep(): number {
  try {
    const raw = Number(window.sessionStorage.getItem(STEP_KEY));
    return Number.isInteger(raw) && raw > 0 && raw < TOUR_STEPS.length ? raw : 0;
  } catch {
    return 0;
  }
}

function saveStep(index: number | null): void {
  try {
    if (index === null) window.sessionStorage.removeItem(STEP_KEY);
    else window.sessionStorage.setItem(STEP_KEY, String(index));
  } catch {
    // Storage may be blocked; the tour then restarts from the top after a reload.
  }
}

/**
 * The guided tour of the real app, shown once after first-run setup.
 *
 * It walks the actual interface: dims the window, opens a hole over one
 * control at a time, and explains it in a small card with the mascot. Where
 * a step needs another place in the app, the tour navigates there itself —
 * into the agents' world and back — and when it ends it puts the app back on
 * the home screen. It never presses anything that could start work or cost
 * money; clicking the highlighted element yourself simply moves the tour on.
 *
 * Escape or "Skip tour" ends it at any point. Either way the backend records
 * it as seen, so it does not come back on its own.
 */
export function GuidedTour({ onDone }: { onDone: () => void }) {
  const t = useT();
  const ready = useLocaleChunk("onboarding");
  const [index, setIndex] = useState(readSavedStep);
  const [rect, setRect] = useState<Rect | null>(null);
  const [cue, setCue] = useState(0);
  const anchorRef = useRef<HTMLElement | null>(null);
  const originalSurface = useRef<HomeSurface>(useHomeStore.getState().surface);
  const doneRef = useRef(false);
  const nextButton = useRef<HTMLButtonElement>(null);
  const step = TOUR_STEPS[index];

  const run = useCallback((effect: TourEffect | undefined) => {
    if (!effect) return;
    const nav = useEventStore.getState();
    if (effect === "home-voice") {
      if (nav.activeSection !== "chats") nav.setActiveSection("chats");
      if (useHomeStore.getState().surface !== "voice") useHomeStore.getState().setSurface("voice");
    } else if (effect === "open-agents") {
      if (nav.activeSection !== "agents") nav.setActiveSection("agents");
    } else if (effect === "back-home") {
      if (nav.activeSection !== "chats") nav.setActiveSection("chats");
    }
  }, []);

  const finish = useCallback(() => {
    if (doneRef.current) return;
    doneRef.current = true;
    saveStep(null);
    // Leave the app where a new user expects it: home, on the surface they had.
    const nav = useEventStore.getState();
    if (nav.activeSection !== "chats") nav.setActiveSection("chats");
    if (useHomeStore.getState().surface !== originalSurface.current) {
      useHomeStore.getState().setSurface(originalSurface.current);
    }
    onDone();
  }, [onDone]);

  const advance = useCallback(() => {
    run(TOUR_STEPS[index]?.onExit);
    const target = nextStepIndex(index);
    if (target === null) {
      finish();
      return;
    }
    saveStep(target);
    setIndex(target);
    setCue((c) => c + 1);
  }, [index, run, finish]);

  // Enter the step: move the app if the step asks, then wait for its element.
  useEffect(() => {
    if (!ready || !step) return;
    run(step.onEnter);
    anchorRef.current = null;
    let cancelled = false;
    let timer = 0;
    const started = performance.now();
    const poll = () => {
      if (cancelled) return;
      const el = findAnchor(step.anchor);
      if (el) {
        anchorRef.current = el;
        setRect(rectOf(el));
        return;
      }
      if (performance.now() - started < FIND_TIMEOUT_MS) {
        timer = window.setTimeout(poll, FIND_POLL_MS);
        return;
      }
      // The element never showed: a step that is optional goes, any other
      // step explains itself from the middle of the window.
      if (step.skipIfMissing) advance();
      else setRect(null);
    };
    timer = window.setTimeout(poll, 0);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
    // `advance` changes with the index, which this effect already follows.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [index, ready]);

  // Follow the element while it is shown: its own size changes, the window,
  // any scrolling container, and a slow safety net for layout shifts.
  useEffect(() => {
    if (!ready) return;
    let frame = 0;
    const measure = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const el = anchorRef.current;
        if (!el || !el.isConnected) {
          const again = step ? findAnchor(step.anchor) : null;
          if (!again) return;
          anchorRef.current = again;
        }
        const next = rectOf(anchorRef.current!);
        setRect((prev) => (sameRect(prev, next) ? prev : next));
      });
    };
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(measure) : null;
    if (anchorRef.current && ro) ro.observe(anchorRef.current);
    window.addEventListener("resize", measure);
    window.addEventListener("scroll", measure, true);
    const interval = window.setInterval(measure, TRACK_MS);
    return () => {
      cancelAnimationFrame(frame);
      ro?.disconnect();
      window.removeEventListener("resize", measure);
      window.removeEventListener("scroll", measure, true);
      window.clearInterval(interval);
    };
  }, [ready, rect === null, step]);

  // Clicking the highlighted element counts as Next; Escape ends the tour.
  useEffect(() => {
    if (!step) return;
    const onClick = (event: MouseEvent) => {
      const target = event.target as Element | null;
      if (target?.closest?.(`[data-tour="${step.anchor}"]`)) advance();
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") finish();
    };
    document.addEventListener("click", onClick, true);
    window.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("click", onClick, true);
      window.removeEventListener("keydown", onKey);
    };
  }, [step, advance, finish]);

  useEffect(() => {
    if (!ready) return;
    const timer = window.setTimeout(() => nextButton.current?.focus({ preventScroll: true }), 360);
    return () => window.clearTimeout(timer);
  }, [index, ready]);

  if (!ready || !step) return null;
  const last = nextStepIndex(index) === null;

  return (
    <Spotlight rect={rect} placement={step.placement}>
      <div
        role="dialog"
        aria-live="polite"
        aria-label={t("app_tour.label")}
        className="rounded-2xl border border-border bg-popover p-4 text-popover-foreground shadow-float"
        data-testid="tour-card"
        data-step={step.id}
      >
        <div className="flex items-start gap-3">
          <MascotGigi
            size={36}
            reactToVoice={false}
            enableComments={false}
            cue={{ action: step.mascot, key: cue }}
          />
          <p className="min-w-0 flex-1 text-sm leading-relaxed text-foreground" data-testid="tour-text">
            {t(`app_tour.steps.${step.id}`)}
          </p>
        </div>
        <div className="mt-4 flex items-center gap-3">
          <button
            ref={nextButton}
            type="button"
            onClick={advance}
            data-testid="tour-next"
            className={cn(
              "h-8 rounded-lg bg-accent px-3.5 text-sm font-medium text-accent-foreground transition-opacity hover:opacity-90",
              FOCUS_RING,
            )}
          >
            {last ? t("app_tour.finish") : t("app_tour.next")}
          </button>
          <span className="text-xs text-muted-foreground">
            {fill(t("app_tour.step_of"), { current: index + 1, total: TOUR_STEPS.length })}
          </span>
          {!last && (
            <QuietAction onClick={finish} className="ml-auto text-xs" testId="tour-skip">
              {t("app_tour.skip")}
            </QuietAction>
          )}
        </div>
      </div>
    </Spotlight>
  );
}
