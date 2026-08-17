import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { LayoutList, Radio } from "lucide-react";
import { useEventStore, type SectionId } from "@/store/events";
import { NAV_GROUPS, resolveNavLabel } from "@/components/layout/navGroups";
import { ChatInput } from "@/components/ChatInput";
import { MascotGigi } from "@/components/MascotGigi";
import { useVoiceReadiness } from "@/hooks/useVoiceReadiness";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";
import { writeDeckMode } from "@/lib/deckMode";
import type { ThinkingStep } from "@/lib/thinkingSteps";

/**
 * The mission deck — the front page.
 *
 * One surface that answers "what is going on" without a single navigation
 * step: every section on the left with its live state, what the assistant is
 * doing right now on the right, and the one place you talk to it in the middle.
 *
 * Everything on screen is REAL. There is no filler telemetry: a number that
 * cannot be sourced from the store is simply not shown, because a deck whose
 * figures are decorative teaches the user to stop reading it. That is why there
 * is no cost meter and no context gauge here yet — the events exist on the bus
 * (`BrainTTFT`, `BudgetWarning`) but nothing feeds them into this store, and
 * inventing a plausible value would be worse than the gap.
 *
 * Colours come from theme tokens only, so the deck holds in both appearances.
 */
export function MissionDeckView({
  headerAccessory,
}: {
  /** The surface switch, handed down by the shell that owns the mode. */
  headerAccessory?: ReactNode;
}) {
  const t = useT();
  const assistantName = useEventStore((s) => s.assistantName);
  const brainProvider = useEventStore((s) => s.brainProvider);
  const brainModel = useEventStore((s) => s.brainModel);
  const connected = useEventStore((s) => s.connected);
  const voiceState = useEventStore((s) => s.voiceState);
  const messages = useEventStore((s) => s.messages);
  const chatThinking = useEventStore((s) => s.chatThinking);
  const thinkingSteps = useEventStore((s) => s.thinkingSteps);
  const conversations = useEventStore((s) => s.conversations);
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const { warming } = useVoiceReadiness();

  // The live steps drive both the "running now" column and the ring around the
  // mascot, so an active step is the deck's definition of "busy".
  const running = useMemo(
    () => thinkingSteps.filter((s) => s.status === "active"),
    [thinkingSteps],
  );

  const mood: DeckMood = !connected
    ? "offline"
    : voiceState === "error"
      ? "fail"
      : voiceState === "listening"
        ? "listening"
        : chatThinking || running.length > 0
          ? "busy"
          : "ready";

  // The headline: the assistant's last word, or the honest state when it has
  // not said anything yet in this session.
  const lastAssistant = useMemo(
    () => [...messages].reverse().find((m) => m.role === "assistant"),
    [messages],
  );

  const headline = lastAssistant
    ? lastAssistant.content
    : warming
      ? t("deck.warming")
      : t("deck.idle_headline");

  return (
    <div className="flex h-full min-h-0 flex-col">
      <DeckHeader
        assistantName={assistantName}
        brainProvider={brainProvider}
        brainModel={brainModel}
        mood={mood}
        runningCount={running.length}
        accessory={headerAccessory}
      />

      <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[minmax(160px,220px)_minmax(0,1fr)_minmax(200px,300px)]">
        <SectionRail onJump={setActiveSection} chatCount={conversations.length} />

        <main className="flex min-h-0 flex-col border-border lg:border-x">
          <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-4 px-6 py-6 text-center">
            <CoreOrb mood={mood} steps={running} />

            <div className="font-mono text-[10px] uppercase tracking-[0.24em] text-primary">
              {t(`deck.mood_${mood}`)}
            </div>

            <p className="max-w-[46ch] text-pretty text-base leading-relaxed text-foreground">
              {headline}
            </p>

            <ThoughtStream steps={thinkingSteps} active={chatThinking} />
          </div>

          {/* The real composer — same component the classic view uses, so
              dictation, file drop and Enter-to-send behave identically. */}
          <div className="border-t border-border px-6 py-4">
            <ChatInput />
          </div>
        </main>

        <RunningColumn steps={thinkingSteps} />
      </div>
    </div>
  );
}

type DeckMood = "ready" | "busy" | "listening" | "fail" | "offline";

const MOOD_DOT: Record<DeckMood, string> = {
  ready: "bg-emerald-400",
  busy: "bg-primary",
  listening: "bg-primary",
  fail: "bg-destructive",
  offline: "bg-muted-foreground",
};

// ----------------------------------------------------------------------
// Header — identity left, the values that steer a decision right.
// ----------------------------------------------------------------------

function DeckHeader({
  assistantName,
  brainProvider,
  brainModel,
  mood,
  runningCount,
  accessory,
}: {
  assistantName: string;
  brainProvider: string;
  brainModel: string;
  mood: DeckMood;
  runningCount: number;
  accessory?: ReactNode;
}) {
  const t = useT();
  const setActiveSection = useEventStore((s) => s.setActiveSection);

  return (
    <header className="flex flex-wrap items-center gap-x-6 gap-y-2 border-b border-border px-4 py-2.5">
      <div className="flex items-center gap-2">
        <span
          className={cn(
            "h-2 w-2 shrink-0 rounded-full",
            MOOD_DOT[mood],
            (mood === "busy" || mood === "listening") && "animate-pulse",
          )}
          aria-hidden
        />
        <span className="font-display text-sm font-bold uppercase tracking-[0.18em]">
          {assistantName}
        </span>
        <span className="font-mono text-[10px] uppercase tracking-[0.16em] text-primary">
          {t(`deck.mood_${mood}`)}
        </span>
      </div>

      <div className="ml-auto flex flex-wrap items-center gap-x-5 gap-y-1">
        <HeaderStat
          label={t("deck.stat_brain")}
          value={brainProvider || "—"}
          hot
          onClick={() => setActiveSection("apikeys")}
        />
        <HeaderStat
          label={t("deck.stat_model")}
          value={brainModel || "—"}
          onClick={() => setActiveSection("apikeys")}
        />
        <HeaderStat label={t("deck.stat_running")} value={String(runningCount)} hot={runningCount > 0} />
        {accessory}
      </div>
    </header>
  );
}

function HeaderStat({
  label,
  value,
  hot,
  onClick,
}: {
  label: string;
  value: string;
  hot?: boolean;
  onClick?: () => void;
}) {
  const body = (
    <>
      <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
        {label}
      </span>
      <span
        className={cn(
          "font-mono text-xs tabular-nums",
          hot ? "text-primary" : "text-foreground",
        )}
      >
        {value}
      </span>
    </>
  );

  if (!onClick) {
    return <div className="flex items-baseline gap-1.5 whitespace-nowrap">{body}</div>;
  }
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex items-baseline gap-1.5 whitespace-nowrap transition-colors hover:[&>span]:text-primary"
    >
      {body}
    </button>
  );
}

// ----------------------------------------------------------------------
// Left — every section at once, straight from the sidebar's own list.
// ----------------------------------------------------------------------

function SectionRail({
  onJump,
  chatCount,
}: {
  onJump: (id: SectionId) => void;
  chatCount: number;
}) {
  const t = useT();
  const activeSection = useEventStore((s) => s.activeSection);

  return (
    <nav className="hidden min-h-0 flex-col overflow-hidden lg:flex">
      <div className="flex items-center gap-2 border-b border-border px-3 py-2 font-mono text-[9px] uppercase tracking-[0.22em] text-primary">
        <LayoutList className="h-3 w-3" />
        {t("deck.sections")}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-1.5 py-2">
        {NAV_GROUPS.map((group, gi) => (
          <div key={gi} className="mb-2">
            {group.map((item) => {
              // Only counts that are genuinely known are shown. "chats" is the
              // one the store already holds; the rest stay blank rather than
              // display a number nobody computed.
              const count = item.id === "chats" ? chatCount : 0;
              const isActive =
                activeSection === item.id || item.matchIds?.includes(activeSection);
              return (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => onJump(item.id)}
                  className={cn(
                    "flex w-full items-center gap-2 rounded-sm px-2 py-1 text-left transition-colors",
                    isActive ? "bg-primary/10" : "hover:bg-card/40",
                  )}
                >
                  <item.icon
                    className={cn(
                      "h-3 w-3 shrink-0",
                      count > 0 ? "text-primary" : "text-muted-foreground",
                    )}
                  />
                  <span
                    className={cn(
                      "flex-1 truncate text-xs",
                      count > 0 ? "text-foreground" : "text-muted-foreground",
                    )}
                  >
                    {resolveNavLabel(t, item)}
                  </span>
                  {count > 0 && (
                    <span className="font-mono text-[10px] tabular-nums text-primary">
                      {count}
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        ))}
      </div>
    </nav>
  );
}

// ----------------------------------------------------------------------
// Centre — the mascot, ringed by one arc per running step.
// ----------------------------------------------------------------------

const STEP_STROKE: Record<string, string> = {
  brain: "hsl(var(--primary))",
  tool: "hsl(var(--primary))",
  computer: "hsl(var(--primary))",
  worker: "hsl(var(--primary))",
  note: "hsl(var(--muted-foreground))",
};

function CoreOrb({ mood, steps }: { mood: DeckMood; steps: ThinkingStep[] }) {
  // A slow sweep that only turns while something is actually running — motion
  // without a cause reads as a screensaver and stops meaning anything.
  const [sweep, setSweep] = useState(0);
  const raf = useRef<number | null>(null);
  const busy = mood === "busy" || mood === "listening";

  useEffect(() => {
    if (!busy || window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      return;
    }
    let start: number | null = null;
    const tick = (ts: number) => {
      if (start === null) start = ts;
      setSweep(((ts - start) / 28) % 360);
      raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
    return () => {
      if (raf.current !== null) cancelAnimationFrame(raf.current);
    };
  }, [busy]);

  const point = useCallback((deg: number, r: number): [number, number] => {
    const rad = ((deg - 90) * Math.PI) / 180;
    return [66 + r * Math.cos(rad), 66 + r * Math.sin(rad)];
  }, []);

  const arcs = steps.slice(0, 6).map((step, i, all) => {
    const span = 300 / Math.max(1, all.length);
    const a0 = -150 + i * span + 3;
    const a1 = a0 + (span - 6);
    const [x0, y0] = point(a0, 52);
    const [x1, y1] = point(a1, 52);
    return {
      id: step.id,
      d: `M ${x0} ${y0} A 52 52 0 0 1 ${x1} ${y1}`,
      stroke: STEP_STROKE[step.kind] ?? "hsl(var(--primary))",
    };
  });

  const [sx, sy] = point(sweep, 60);
  const [ex, ey] = point(sweep + 40, 60);

  return (
    <div className="relative h-[132px] w-[132px] shrink-0">
      <svg viewBox="0 0 132 132" className="absolute inset-0 h-full w-full" aria-hidden>
        {/* Scale ring — built once, static. */}
        {Array.from({ length: 48 }, (_, i) => {
          const deg = i * 7.5;
          const [ax, ay] = point(deg, 38);
          const [bx, by] = point(deg, 42);
          return (
            <line
              key={deg}
              x1={ax}
              y1={ay}
              x2={bx}
              y2={by}
              stroke="hsl(var(--primary))"
              strokeWidth={1}
              opacity={i % 6 === 0 ? 0.4 : 0.13}
            />
          );
        })}

        {arcs.map((a) => (
          <path
            key={a.id}
            d={a.d}
            fill="none"
            stroke={a.stroke}
            strokeWidth={2.5}
            strokeLinecap="round"
            opacity={0.9}
          />
        ))}

        {busy && (
          <path
            d={`M ${sx} ${sy} A 60 60 0 0 1 ${ex} ${ey}`}
            fill="none"
            stroke="hsl(var(--primary))"
            strokeWidth={1.2}
            strokeLinecap="round"
            opacity={0.5}
          />
        )}
      </svg>

      <div className="absolute inset-0 grid place-items-center">
        <MascotGigi size={54} reactToVoice enableComments={false} />
      </div>
    </div>
  );
}

/**
 * What the assistant is reasoning about, in its own words.
 *
 * Shows the newest step's translated label plus its raw detail. Deliberately
 * one line: this is the pulse, not the transcript — the full trace lives in the
 * running column beside it.
 */
function ThoughtStream({ steps, active }: { steps: ThinkingStep[]; active: boolean }) {
  const t = useT();
  const latest = steps[steps.length - 1];

  if (!active && !latest) {
    return <div className="min-h-[3.2em]" aria-hidden />;
  }

  return (
    <div className="w-full max-w-[46ch] border-l border-border pl-3 text-left">
      <span className="block font-mono text-[9px] uppercase tracking-[0.2em] text-primary">
        {t("deck.thinking_now")}
      </span>
      <span className="text-xs leading-relaxed text-muted-foreground">
        {latest ? (
          <>
            {t(latest.labelKey)}
            {latest.detail ? ` · ${latest.detail}` : ""}
          </>
        ) : (
          t("deck.thinking_starting")
        )}
      </span>
    </div>
  );
}

// ----------------------------------------------------------------------
// Right — every action the assistant is taking, live.
// ----------------------------------------------------------------------

const STEP_BADGE: Record<string, string> = {
  brain: "bg-primary text-primary-foreground",
  tool: "bg-sky-400 text-black",
  computer: "bg-violet-400 text-black",
  worker: "bg-emerald-400 text-black",
  note: "bg-muted text-muted-foreground",
};

function RunningColumn({ steps }: { steps: ThinkingStep[] }) {
  const t = useT();
  const shown = [...steps].reverse();

  return (
    <aside className="flex min-h-0 flex-col overflow-hidden">
      <div className="flex items-center gap-2 border-b border-border px-3 py-2 font-mono text-[9px] uppercase tracking-[0.22em] text-primary">
        <Radio className="h-3 w-3" />
        {t("deck.running")}
        <span className="ml-auto tabular-nums text-muted-foreground">{steps.length}</span>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-2 py-2">
        {shown.length === 0 ? (
          <p className="px-1 py-1 text-[11px] text-muted-foreground">
            {t("deck.running_empty")}
          </p>
        ) : (
          shown.map((step) => (
            <div
              key={step.id}
              className={cn(
                "mb-1 border-l px-2 py-1",
                step.status === "active"
                  ? "border-primary"
                  : step.status === "error"
                    ? "border-destructive"
                    : "border-border",
              )}
            >
              <div className="flex items-center gap-1.5">
                <span
                  className={cn(
                    "shrink-0 px-1 py-px font-mono text-[9px] uppercase tracking-wider",
                    STEP_BADGE[step.kind] ?? STEP_BADGE.note,
                  )}
                >
                  {step.kind}
                </span>
                <span
                  className={cn(
                    "flex-1 truncate text-[11px]",
                    step.status === "active" ? "text-foreground" : "text-muted-foreground",
                  )}
                >
                  {t(step.labelKey)}
                </span>
                {step.durationMs !== undefined && (
                  <span className="shrink-0 font-mono text-[9px] tabular-nums text-muted-foreground">
                    {(step.durationMs / 1000).toFixed(1)}s
                  </span>
                )}
              </div>
              {step.detail && (
                <div className="truncate pl-1 pt-0.5 font-mono text-[10px] text-muted-foreground">
                  {step.detail}
                </div>
              )}
            </div>
          ))
        )}
      </div>
    </aside>
  );
}

/**
 * The escape hatch, rendered by the shell above both surfaces.
 *
 * Kept in this module so the deck and the switch that leaves it stay together.
 */
export function SurfaceSwitch({
  mode,
  onChange,
}: {
  mode: "deck" | "classic";
  onChange: (mode: "deck" | "classic") => void;
}) {
  const t = useT();
  const next = mode === "deck" ? "classic" : "deck";

  return (
    <button
      type="button"
      onClick={() => {
        writeDeckMode(next);
        onChange(next);
      }}
      title={t(next === "classic" ? "deck.switch_to_classic" : "deck.switch_to_deck")}
      className="flex items-center gap-1.5 rounded-lg border border-border px-2 py-1 font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground transition-colors hover:border-primary/50 hover:text-primary"
    >
      {t(next === "classic" ? "deck.switch_to_classic" : "deck.switch_to_deck")}
    </button>
  );
}
