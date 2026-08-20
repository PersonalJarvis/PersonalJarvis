import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { AnimatePresence, MotionConfig, motion, useReducedMotion } from "framer-motion";
import { useEventStore, type VoiceState } from "@/store/events";
import { useDeckStore } from "@/store/deck";
import { VoiceWaveform, type WaveformPhase } from "@/components/overlay/VoiceWaveform";
import { voiceInputLevelRef } from "@/lib/voiceInputLevel";
import { DockRail } from "@/components/layout/DockRail";
import { TopBarActions } from "@/components/layout/TopBar";
import { CodingModeBadge } from "@/components/layout/CodingModeBadge";
import { MascotGigi } from "@/components/MascotGigi";
import { DeckOrb, type OrbReadouts } from "@/components/deck/DeckOrb";
import type { ThinkingStep } from "@/lib/thinkingSteps";
import { DeckStandby, ORB_TRAVEL } from "@/components/deck/DeckStandby";
import { DeckReveal } from "@/components/deck/DeckReveal";
import { HudLamp } from "@/components/deck/HudFrame";
import {
  IdeGridCard,
  OutputsCard,
  RunsCard,
  TerminalsCard,
} from "@/components/deck/DeckActivityCards";
import { ApiStatsCard, CaptureCard, LiveCounter } from "@/components/deck/DeckSignalCards";
import { LogCard } from "@/components/deck/DeckLogCard";
import { TurnCard } from "@/components/deck/DeckTurnCard";
import { WikiCard, warmWikiScene } from "@/components/deck/DeckWiki";
import { useVoiceEngineDisplay } from "@/hooks/useVoiceEngineDisplay";
import { useVoiceReadiness } from "@/hooks/useVoiceReadiness";
import { useWakeWord } from "@/hooks/useWakeWord";
import { useVoiceCall } from "@/components/agentic/useVoiceCall";
import { useElementSize } from "@/hooks/useElementSize";
import { orbSizeFor, stageVignette } from "@/lib/deckStage";
import { HANDOFF, autoLaunchAfterMs, resolvePhase } from "@/lib/deckStandby";
import { writeDeckMode } from "@/lib/deckMode";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";

/**
 * The mission deck — the front page.
 *
 * One stage that shows everything the assistant is and does, without a
 * single navigation step. You talk to it by voice; the typed composer lives
 * on the classic chat surface, not here (maintainer decision 2026-08-18).
 * The layout follows the maintainer's sketch of 2026-08-17 (photographed
 * rotated; read upright):
 *
 *   ┌ gigi · voice bars · lamps · counter ── name · brain · switch · chrome ┐
 *   │ dock │ [log — the     [response][api]        [ WIKI — 3D, tall    ] │
 *   │      │  terminal]     (      ORB      )       [                    ] │
 *   │      │ [outputs][run]   [capture]            [terminals] [ide grid] │
 *   └──────┴───────────────────────────────────────────────────────────────┘
 *
 * Two of the sketch's cards were re-thought on 2026-08-18 (maintainer): the
 * "right now" trace doubled the run inspector, which the small RUNS card
 * already opens, so the tall left slot is now the LOG — a terminal of what
 * the assistant heard, thought, did and said, with timings, that is never
 * blank; and the live Computer-Use screen mirror gave way to the RESPONSE
 * instrument — the turn's phases and how long each stage took. Computer Use
 * still shows: as the "control" lamp, as the response card's act phase, and
 * as lines in the log.
 *
 * Every card is a window onto a section and reads that section's own data;
 * its title jumps there. The dock on the left is the ONLY place the sections
 * are listed — the sidebar steps aside while the deck is up (App.tsx).
 *
 * The board is the deck's THIRD act (maintainer, 2026-08-18: a fresh start
 * showed nine instruments all saying "nothing yet"). Before the first word
 * the stage is `DeckStandby` — the boot sequence while the app comes up,
 * then the listening ring — and the board takes over the moment a turn
 * opens or the person asks for it (`lib/deckStandby.ts::resolvePhase`,
 * forward only). It also takes over ON ITS OWN, one beat after the ring
 * reports ready (maintainer, 2026-08-20: opening the app must land you on
 * the board, not on a screen that waits to be answered —
 * `lib/deckStandby.ts::AUTO_LAUNCH`). The start plays out in full either
 * way; only the trigger changed. The hand-off is a LAUNCH, on one clock
 * (`lib/deckStandby.ts::HANDOFF`): the orb flares and a shockwave leaves
 * it, the standby's ring bursts past the camera and its console gets out of
 * the way (`DeckStandby`), the orb travels from the ring's centre to its
 * place on the board (one `layoutId` on both stages) and lands with a ring,
 * the instruments assemble from the centre outward as the wave reaches them
 * (`DeckReveal`), and one scan runs down the whole board. The maintainer's
 * verdict on a fade-and-wipe (2026-08-19): a hard switch, ridiculous — this
 * has to be cinematic.
 *
 * The frames differ on purpose (HudFrame.tsx): brackets for pictures and the
 * map, chamfers for readouts, rails for streams — a deck of instruments, not
 * a grid of identical boxes. Nothing on this screen is invented: a number the
 * store cannot source is not shown.
 */
export function MissionDeckView({
  headerAccessory,
}: {
  /** The surface switch, handed down by the shell that owns the mode. */
  headerAccessory?: ReactNode;
}) {
  const t = useT();
  const assistantName = useEventStore((s) => s.assistantName);
  const connected = useEventStore((s) => s.connected);
  const voiceReady = useEventStore((s) => s.voiceReady);
  const voiceState = useEventStore((s) => s.voiceState);
  const chatThinking = useEventStore((s) => s.chatThinking);
  const thinkingSteps = useEventStore((s) => s.thinkingSteps);
  const messages = useEventStore((s) => s.messages);
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const cuActive = useDeckStore((s) => s.cu.active);
  const wordsSession = useDeckStore((s) => s.wordsSession);
  const turnPhase = useDeckStore((s) => s.turn.phase);
  const turnIndex = useDeckStore((s) => s.turn.index);
  const boardOpen = useDeckStore((s) => s.boardOpen);
  const openBoard = useDeckStore((s) => s.openBoard);
  const { warming } = useVoiceReadiness();
  // Header + orb name the engine that will answer the next spoken turn, not
  // the dormant sibling. Pipeline and realtime are independent picks; the
  // sidebar footer already followed this rule and the deck was still showing
  // the classic brain (OpenRouter) while Vertex AI Live was talking.
  const engine = useVoiceEngineDisplay();
  // The orb is the click-shaped wake word: the same start/stop path the
  // classic surface's voice bubble uses, so both do exactly one thing.
  const { active: callActive, busy: callBusy, connecting, toggleCall } = useVoiceCall();
  // The idle line names the real phrase ("Hey Nova"), never "your wake word":
  // a person who just installed the app should read what to say. Until the
  // phrase is known the name it derives from stands in.
  const { config: wakeConfig } = useWakeWord();
  const wakePhrase = wakeConfig?.phrase.trim() || assistantName;

  // Which act: boot, standby, or the board — forward only for the session.
  const phase = resolvePhase({
    connected,
    voiceReady,
    boardOpen,
    turnIndex,
    messageCount: messages.length,
    voiceEngaged: callActive || connecting,
  });
  // Forward only, for real: a transient reason for the board (a call being
  // set up, a voice state that came and went without a turn) must not let the
  // standby come back over a stage that already handed off — an interrupted
  // hand-off leaves the orb's shared-layout crossfade half-way, with the ring
  // back and nothing in it (seen 2026-08-19). So the first board render
  // latches the store's sticky flag.
  useEffect(() => {
    if (phase === "board") openBoard();
  }, [phase, openBoard]);
  // The start screen launches itself. Nobody who opens the app wants a lobby
  // they have to answer (maintainer, 2026-08-20) — so the boot lights its
  // gates, the ring reports ready, and one beat later the SAME hand-off a
  // spoken word triggers plays on its own (`lib/deckStandby.ts::AUTO_LAUNCH`).
  // Any real trigger arriving first simply wins: the phase is already board
  // and this timer never fires.
  useEffect(() => {
    const after = autoLaunchAfterMs(phase, connected);
    if (after === null) return;
    const id = window.setTimeout(() => openBoard(), after);
    return () => window.clearTimeout(id);
  }, [phase, connected, openBoard]);
  // The board powers on with its choreography ONLY when it takes over from
  // the standby on this very screen. A deck that mounts straight into a
  // running session (a section change and back) is simply there. Fixed on
  // the first render so the wrappers never remount because the flag moved.
  const mountedInto = useRef(phase);
  const revealBoard = mountedInto.current !== "board";
  // While the stage waits for the first word, the board's heaviest part gets
  // ready in the idle time: the WebGL probe and the 3D map's chunk. Measured
  // 2026-08-19: done in the click's own task they froze the launch for half
  // a second, and every JS-driven beat of it — the orb's travel — jumped.
  useEffect(() => {
    if (phase === "board") return;
    const w = window as Window & {
      requestIdleCallback?: (cb: () => void, opts?: { timeout: number }) => number;
      cancelIdleCallback?: (id: number) => void;
    };
    if (w.requestIdleCallback && w.cancelIdleCallback) {
      const id = w.requestIdleCallback(() => warmWikiScene(), { timeout: 4000 });
      return () => w.cancelIdleCallback?.(id);
    }
    const id = window.setTimeout(() => warmWikiScene(), 1500);
    return () => window.clearTimeout(id);
  }, [phase]);

  const running = useMemo(
    () => thinkingSteps.filter((s) => s.status === "active"),
    [thinkingSteps],
  );
  // The deck's own turn counts too: the text chat's thinking flag never arms
  // for a voice turn, and the orb must sweep for those as well.
  const busy =
    chatThinking ||
    running.length > 0 ||
    voiceState === "thinking" ||
    turnPhase === "think" ||
    turnPhase === "act";

  const mood: DeckMood = !connected
    ? "offline"
    : voiceState === "error"
      ? "fail"
      : voiceState === "listening"
        ? "listening"
        : voiceState === "speaking"
          ? "speaking"
          : busy
            ? "busy"
            : "ready";

  const lastAssistant = useMemo(
    () => [...messages].reverse().find((m) => m.role === "assistant"),
    [messages],
  );
  const headline = lastAssistant
    ? lastAssistant.content
    : warming
      ? t("deck.warming")
      : t("deck.idle_headline").replace("{0}", wakePhrase);
  const orbPressLabel = callActive
    ? t("deck.orb_hangup")
    : t("deck.orb_call").replace("{0}", wakePhrase);
  const readouts: OrbReadouts = {
    nw: t(`deck.mood_${mood}`),
    ne: `${running.length} ${t("deck.orb_steps")}`,
    sw: engine.providerLabel,
    se: `${wordsSession} ${t("deck.orb_words")}`,
  };
  // Pressing the orb reaches for the voice — the board opens on the press
  // itself, so the hand-off plays the moment the person acts, not a second
  // later when the transport reports back.
  const pressOrb = () => {
    openBoard();
    void toggleCall();
  };
  const pressDisabled = callBusy || connecting || !connected;

  return (
    <MotionConfig reducedMotion="user">
    <div className="flex h-full min-h-0 flex-col">
      {/* One header. The shell TopBar steps aside on this screen (same
          rule as the IDE): Gigi and the chrome actions live here so the
          front page is not two bars with a hole between them. */}
      <header className="relative flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-2">
        <span
          className="flex h-8 w-8 shrink-0 items-center justify-center"
          data-testid="deck-header-gigi"
        >
          <MascotGigi size={32} reactToVoice enableComments={false} />
        </span>
        <div className="flex items-center gap-3">
          <VoiceWaveform
            levelRef={voiceInputLevelRef}
            phase={waveformPhase(voiceState, connected)}
            count={14}
            className="h-6"
          />
          <span
            className={cn(
              "font-mono text-[10px] uppercase tracking-[0.18em]",
              mood === "fail" ? "text-destructive" : "text-primary",
            )}
          >
            {t(`deck.mood_${mood}`)}
          </span>
        </div>

        {/* Lamp row: the things that have to be true for a voice turn. */}
        <div className="flex items-center gap-3 font-mono text-[9px] uppercase tracking-[0.14em] text-muted-foreground">
          <Lamp on={connected} label={t("deck.lamp_link")} />
          <Lamp on={voiceReady} label={t("deck.lamp_voice")} />
          <Lamp
            on={engine.providerLabel !== "—"}
            label={t(engine.tier === "realtime" ? "deck.stat_realtime" : "deck.lamp_brain")}
          />
          <Lamp on={cuActive} label={t("deck.lamp_cu")} />
        </div>

        <LiveCounter />

        <div className="ml-auto flex flex-wrap items-center gap-x-4 gap-y-1">
          <span className="font-display text-sm font-bold uppercase tracking-[0.18em]">
            {assistantName}
          </span>
          <HeaderStat
            label={t(engine.tier === "realtime" ? "deck.stat_realtime" : "deck.stat_brain")}
            value={engine.providerLabel}
            hot
            testId="deck-stat-engine"
            onClick={() => setActiveSection("apikeys")}
          />
          <HeaderStat
            label={t("deck.stat_model")}
            value={engine.model || "—"}
            testId="deck-stat-model"
            onClick={() => setActiveSection("apikeys")}
          />
          {headerAccessory}
          <CodingModeBadge />
          <TopBarActions />
        </div>

        <svg
          className="pointer-events-none absolute inset-x-3 bottom-0 h-2 w-[calc(100%-1.5rem)]"
          preserveAspectRatio="none"
          viewBox="0 0 100 8"
          aria-hidden
        >
          <path
            d="M 0 0 V 8 M 0 7.5 H 100 M 100 0 V 8"
            fill="none"
            stroke="hsl(var(--primary))"
            strokeWidth={1}
            opacity={0.5}
            vectorEffect="non-scaling-stroke"
          />
        </svg>
      </header>

      {/* Stage */}
      <div className="flex min-h-0 flex-1">
        <DockRail />

        <div className="relative flex min-h-0 min-w-0 flex-1 flex-col">
          {phase === "board" && (
            <div
              data-testid="deck-board"
              className="relative grid min-h-0 flex-1 grid-cols-1 gap-3 overflow-y-auto p-3 lg:grid-cols-[minmax(200px,3fr)_minmax(0,6fr)_minmax(240px,4fr)] lg:grid-rows-[minmax(0,1fr)_minmax(0,0.6fr)] lg:overflow-hidden"
            >
              {/* LEFT top: the log — the terminal of the session */}
              <DeckReveal slot="left-top" reveal={revealBoard} bodyClassName="flex min-h-0 flex-col">
                <LogCard className="min-h-0 flex-1" />
              </DeckReveal>

              {/* CENTRE top: the response instrument + api on a strip, the orb underneath */}
              <div className="flex min-h-0 flex-col gap-3">
                <DeckReveal
                  slot="centre-top"
                  reveal={revealBoard}
                  className="shrink-0"
                  bodyClassName="grid grid-cols-2 gap-3"
                  style={{ height: "36%" }}
                >
                  <TurnCard className="min-h-0" />
                  <ApiStatsCard className="min-h-0" />
                </DeckReveal>
                <BoardCentre
                  steps={running}
                  busy={busy}
                  readouts={readouts}
                  onPress={pressOrb}
                  pressLabel={orbPressLabel}
                  pressDisabled={pressDisabled}
                  headline={headline}
                  headlineIsAnswer={Boolean(lastAssistant)}
                  reveal={revealBoard}
                />
              </div>

              {/* RIGHT top: the wiki, in space, tall */}
              <DeckReveal slot="right-top" reveal={revealBoard} bodyClassName="flex min-h-0 flex-col">
                <WikiCard className="min-h-0 flex-1" />
              </DeckReveal>

              {/* LEFT bottom: outputs and runs */}
              <DeckReveal
                slot="left-bottom"
                reveal={revealBoard}
                className="min-h-[8rem]"
                bodyClassName="grid grid-cols-2 gap-3"
              >
                <OutputsCard className="min-h-0" />
                <RunsCard className="min-h-0" />
              </DeckReveal>

              {/* CENTRE bottom: the last capture (briefly), then the ledger; centred and not too wide */}
              <DeckReveal
                slot="centre-bottom"
                reveal={revealBoard}
                className="min-h-[8rem]"
                bodyClassName="flex items-stretch justify-center"
              >
                <CaptureCard className="w-full max-w-[28rem]" />
              </DeckReveal>

              {/* RIGHT bottom: terminals and the coding workspace */}
              <DeckReveal
                slot="right-bottom"
                reveal={revealBoard}
                className="min-h-[8rem]"
                bodyClassName="grid grid-cols-2 gap-3"
              >
                <TerminalsCard className="min-h-0" />
                <IdeGridCard className="min-h-0" />
              </DeckReveal>

              {/* The launch's last beat: one scan down the whole board. */}
              {revealBoard && <BoardSweep />}
            </div>
          )}

          {/* Before the first word: the boot sequence, then the listening
              ring. Absolute over the stage so its exit plays over the board
              powering on underneath. */}
          <AnimatePresence>
            {phase !== "board" && (
              <DeckStandby
                key="standby"
                className="absolute inset-0 z-10"
                phase={phase}
                steps={running}
                busy={busy}
                readouts={readouts}
                wakeConfig={wakeConfig}
                onPressOrb={pressOrb}
                pressLabel={orbPressLabel}
                pressDisabled={pressDisabled}
                onOpenBoard={openBoard}
              />
            )}
          </AnimatePresence>
        </div>
      </div>

    </div>
    </MotionConfig>
  );
}

type DeckMood = "ready" | "busy" | "listening" | "speaking" | "fail" | "offline";


/**
 * The board's centre: the orb on its vignetted stage with the headline under
 * it. The centre grows with the room it has — as large as the stage allows,
 * never taller than the room left under the headline, and never past the
 * point where the reticle stops reading as an instrument. It measures ITSELF
 * because it mounts with the board, not with the view: a measurement taken
 * by the view while the standby is up would find no stage and never look
 * again.
 */
function BoardCentre({
  steps,
  busy,
  readouts,
  onPress,
  pressLabel,
  pressDisabled,
  headline,
  headlineIsAnswer,
  reveal,
}: {
  steps: ThinkingStep[];
  busy: boolean;
  readouts: OrbReadouts;
  onPress: () => void;
  pressLabel: string;
  pressDisabled: boolean;
  headline: string;
  /** The headline is the assistant's last answer, not the idle prompt. */
  headlineIsAnswer: boolean;
  reveal: boolean;
}) {
  const stageRef = useRef<HTMLDivElement>(null);
  const stage = useElementSize(stageRef);
  const orbSize = orbSizeFor(stage.width, stage.height);
  const reduced = useReducedMotion() ?? false;
  // The landing ring fires once and leaves the DOM.
  const [landed, setLanded] = useState(false);
  return (
    <div
      ref={stageRef}
      className="flex min-h-0 flex-1 flex-col items-center justify-center gap-2 px-2 text-center"
      style={{ backgroundImage: stageVignette(orbSize) }}
    >
      {/* The same layoutId as the standby's orb: when the board takes over,
          the orb travels here instead of blinking — and lands with a ring. */}
      <div className="relative">
        <motion.div layoutId="deck-orb" layoutDependency={orbSize} transition={ORB_TRAVEL}>
          <DeckOrb
            steps={steps}
            busy={busy}
            size={orbSize}
            readouts={readouts}
            onPress={onPress}
            pressLabel={pressLabel}
            pressDisabled={pressDisabled}
          />
        </motion.div>
        {reveal && !reduced && !landed && (
          <motion.div
            aria-hidden
            data-testid="deck-orb-landing"
            className="deck-handoff-wave pointer-events-none absolute inset-0 rounded-full"
            initial={{ opacity: 0, transform: "scale(0.7)" }}
            animate={{ opacity: [0, 0.85, 0], transform: ["scale(0.7)", "scale(1.05)", "scale(1.35)"] }}
            transition={{ delay: HANDOFF.landDelayS, duration: HANDOFF.landS, times: [0, 0.15, 1], ease: "easeOut" }}
            onAnimationComplete={() => setLanded(true)}
          />
        )}
      </div>
      <motion.p
        initial={reveal ? { opacity: 0, y: 6 } : false}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: HANDOFF.headlineDelayS, duration: 0.4 }}
        className={cn(
          "max-w-[44ch] text-pretty text-sm leading-relaxed",
          headlineIsAnswer ? "text-foreground" : "text-muted-foreground",
        )}
      >
        {headline}
      </motion.p>
    </div>
  );
}

/**
 * The launch's last beat: once the instruments are in, one scan line runs
 * down the whole board and is gone — the deck is live. Mounted only for a
 * board that took over from the standby on this screen; unmounts itself.
 */
function BoardSweep() {
  const reduced = useReducedMotion() ?? false;
  const [done, setDone] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  // The board's height, once: the bar travels it as a transform (off the
  // main thread), not as `top`.
  const [travel, setTravel] = useState(0);
  useLayoutEffect(() => {
    const parent = ref.current?.parentElement;
    if (parent) setTravel(parent.clientHeight);
  }, []);
  if (reduced || done) return null;
  return (
    <motion.div
      ref={ref}
      aria-hidden
      data-testid="deck-board-sweep"
      className="deck-scan-bar deck-scan-bar-h deck-board-sweep"
      initial={{ transform: "translateY(0px)", opacity: 0 }}
      animate={{ transform: `translateY(${travel}px)`, opacity: [0, 1, 1, 0] }}
      transition={{
        transform: { delay: HANDOFF.boardSweepDelayS, duration: HANDOFF.boardSweepS, ease: [0.3, 0, 0.2, 1] },
        opacity: { delay: HANDOFF.boardSweepDelayS, duration: HANDOFF.boardSweepS, times: [0, 0.1, 0.8, 1] },
      }}
      onAnimationComplete={() => setDone(true)}
    />
  );
}

function waveformPhase(state: VoiceState, connected: boolean): WaveformPhase {
  if (!connected) return "idle";
  switch (state) {
    case "connecting":
      return "connecting";
    case "listening":
      return "listening";
    case "thinking":
      return "working";
    case "speaking":
      return "speaking";
    case "error":
      return "error";
    default:
      return "idle";
  }
}

function Lamp({ on, label }: { on: boolean; label: string }) {
  return (
    <span className="flex items-center gap-1">
      <HudLamp on={on} />
      <span className={on ? "text-foreground/80" : undefined}>{label}</span>
    </span>
  );
}

function HeaderStat({
  label,
  value,
  hot,
  onClick,
  testId,
}: {
  label: string;
  value: string;
  hot?: boolean;
  onClick?: () => void;
  testId?: string;
}) {
  const body = (
    <>
      <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
        {label}
      </span>
      <span className={cn("font-mono text-xs tabular-nums", hot ? "text-primary" : "text-foreground")}>
        {value}
      </span>
    </>
  );
  if (!onClick) {
    return (
      <div data-testid={testId} className="flex items-baseline gap-1.5 whitespace-nowrap">
        {body}
      </div>
    );
  }
  return (
    <button
      type="button"
      data-testid={testId}
      onClick={onClick}
      className="flex items-baseline gap-1.5 whitespace-nowrap transition-colors hover:[&>span]:text-primary"
    >
      {body}
    </button>
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
  const label = t(next === "classic" ? "deck.switch_to_classic" : "deck.switch_to_deck");

  return (
    <button
      type="button"
      onClick={() => {
        writeDeckMode(next);
        onChange(next);
      }}
      title={label}
      className="flex items-center gap-1.5 border border-border px-2 py-1 font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground transition-colors hover:border-primary/50 hover:text-primary"
      style={{ clipPath: "polygon(6px 0, 100% 0, 100% calc(100% - 6px), calc(100% - 6px) 100%, 0 100%, 0 6px)" }}
    >
      {label}
    </button>
  );
}
