import { useEffect, useId, useMemo, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { motion, useIsPresent, useReducedMotion } from "framer-motion";
import { useEventStore } from "@/store/events";
import { useDeckStore } from "@/store/deck";
import type { WakeWordConfig } from "@/hooks/useWakeWord";
import type { ThinkingStep } from "@/lib/thinkingSteps";
import {
  arcPath,
  gatesFor,
  polar,
  reticleSizeFor,
  ringSizeFor,
  ringTicks,
  GATE_ARC_CENTRE,
  GATE_ARC_SPAN,
  HANDOFF,
  SETTLE_MS,
  type Gate,
  type GateId,
  type GateState,
} from "@/lib/deckStandby";
import { DeckOrb, type OrbReadouts } from "@/components/deck/DeckOrb";
import { HudFrameOverlay, HudHaloDefs, useElementSize } from "@/components/deck/HudFrame";
import { fmtClock, fmtMs, fmtQuiet } from "@/components/deck/DeckLogCard";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";

/**
 * The deck before the first word — the boot sequence and the standby stage.
 *
 * What the maintainer asked for (2026-08-18): the board is good once things
 * happen, but a fresh start showed nine instruments all saying "nothing yet".
 * So until somebody speaks the stage is ONE instrument: the orb inside a big
 * ring, the four gates a voice turn needs drawn on that ring, and a small
 * console in the corner that reads like a boot log. Everything on it is a
 * fact from the stores the header lamps already show; the durations are
 * measured on this very screen (`okAt` against `mountedAt`) and appear only
 * for gates this screen watched turn true.
 *
 * Boot: the ring's ticks ignite clockwise, the bezel draws itself, and each
 * gate's arc draws in the moment that gate is really true — link, voice,
 * brain, wake — while its console line types in with the clock time. When
 * link and voice are both up, one ping leaves the orb and dies at the bezel:
 * the boot is done.
 *
 * Standby: the sweep turns on the ring while the wake word is actually being
 * listened for (and stays still when it is not — hotkey-only setups, a link
 * that dropped), the clock and the "quiet for" figure tick, the orb breathes.
 * The cue under the orb names the phrase to say — big, breathing slowly,
 * because it is the one thing a newcomer must read. Nothing else moves: the
 * standby has to be calm enough to sit next to for an hour.
 *
 * The board is one press away at all times (`onOpenBoard`), and takes over
 * on its own the moment a turn opens — that hand-off is MissionDeckView's.
 * This stage's part of it is the LAUNCH (`lib/deckStandby.ts::HANDOFF`):
 * the orb flares and two shockwaves leave it, the stage flashes, the ring
 * draws breath and bursts past the camera turning while its ticks flare
 * clockwise and the sweep spins up, the cue and the corners get out of the
 * way, and only then does the layer fade — over the board assembling
 * underneath. `useIsPresent` flips `data-leaving`, and the burst — flash,
 * flare, waves, the ring's breath-and-burst, the tick flare, the sweep
 * spin-up — is pure CSS keyed on it (index.css), so it runs on the
 * compositor whatever the main thread is doing while the board mounts; the
 * framer `exit` variants carry only the corners, the cue and the layer's own
 * fade. The maintainer's brief (2026-08-19): a hard switch is ridiculous;
 * this has to be cinematic, Stark-grade, fun to watch.
 */

/** How the orb travels between this ring and its place on the board. */
export const ORB_TRAVEL = {
  layout: { duration: HANDOFF.travelS, delay: HANDOFF.travelDelayS, ease: [0.22, 0.85, 0.2, 1] },
} as const;

/** The ring is labelled at the compass points only when there is room. */
const LABELS_MIN_RING = 520;
/** The corner blocks leave the ring's sides alone from this stage width. */
const WIDE_STAGE = 1040;

export type StandbyPhase = "boot" | "standby";

export function DeckStandby({
  phase,
  steps,
  busy,
  readouts,
  wakeConfig,
  onPressOrb,
  pressLabel,
  pressDisabled,
  onOpenBoard,
  className,
}: {
  phase: StandbyPhase;
  steps: ThinkingStep[];
  busy: boolean;
  readouts: OrbReadouts;
  wakeConfig: WakeWordConfig | null;
  onPressOrb: () => void;
  pressLabel: string;
  pressDisabled: boolean;
  onOpenBoard: () => void;
  className?: string;
}) {
  const t = useT();
  const reduced = useReducedMotion() ?? false;
  // The stage is on its way out — AnimatePresence keeps it mounted for the
  // exit, and this is how the ring's CSS learns to flare and spin up.
  const present = useIsPresent();
  const leaving = !present && !reduced;
  const connected = useEventStore((s) => s.connected);
  const voiceReady = useEventStore((s) => s.voiceReady);
  const voiceState = useEventStore((s) => s.voiceState);
  const brainProvider = useEventStore((s) => s.brainProvider);
  const brainModel = useEventStore((s) => s.brainModel);
  const assistantName = useEventStore((s) => s.assistantName);
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const journal = useDeckStore((s) => s.journal);

  // One clock for the stage: the corner clock, "quiet for", and the settle
  // timer all read it, so the stage ticks as one thing.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  // What this screen watched: gates that were pending when it mounted, and
  // when each turned true. Only THOSE get a duration — a gate that was
  // already up when the stage appeared has no honest time to show.
  const mountedAt = useRef(Date.now());
  const [okAt, setOkAt] = useState<Partial<Record<GateId, number>>>({});
  const [readyAt, setReadyAt] = useState<number | null>(null);
  // Whether the boot played on this screen — the ignition and the ping fire
  // only then; a stage that mounts already-ready is simply on.
  const ignite = useRef(phase === "boot").current;

  const settled = readyAt !== null && now - readyAt >= SETTLE_MS;
  const gates = useMemo(
    () =>
      gatesFor({
        connected,
        voiceReady,
        brainProvider,
        wakeEnabled: wakeConfig ? wakeConfig.enabled : null,
        settled,
      }),
    [connected, voiceReady, brainProvider, wakeConfig, settled],
  );
  // Fixed on the first render, so the very first paint already knows which
  // lines and arcs are the ones to watch draw in.
  const [pendingAtMount] = useState(
    () => new Set<GateId>(gates.filter((g) => g.state !== "ok").map((g) => g.id)),
  );

  useEffect(() => {
    const ts = Date.now();
    setOkAt((prev) => {
      let next = prev;
      for (const g of gates) {
        if (g.state === "ok" && prev[g.id] === undefined) {
          if (next === prev) next = { ...prev };
          next[g.id] = ts;
        }
      }
      return next;
    });
    if (connected && voiceReady) setReadyAt((r) => (r === null ? ts : r));
  }, [gates, connected, voiceReady]);

  const [stageRef, stage] = useElementSize<HTMLDivElement>();
  const wide = stage.w >= WIDE_STAGE;
  const ring = wide
    ? ringSizeFor(stage.w, stage.h)
    : ringSizeFor(stage.w, Math.max(0, stage.h - 200));
  const reticle = reticleSizeFor(ring);

  const wakePhrase = wakeConfig?.phrase.trim() || assistantName;
  const listening =
    phase === "standby" &&
    connected &&
    voiceReady &&
    voiceState === "idle" &&
    gates.find((g) => g.id === "wake")?.state === "ok";

  const lastJournalTs = journal.length > 0 ? journal[journal.length - 1].ts : null;
  const quietSince = lastJournalTs ?? readyAt ?? mountedAt.current;
  const quiet = now - quietSince;

  const gateText = (g: Gate): { text: string; onClick?: () => void } => {
    switch (g.id) {
      case "link":
        return { text: t(g.state === "ok" ? "deck.boot_link_ok" : "deck.boot_link_pending") };
      case "voice":
        return { text: t(g.state === "ok" ? "deck.boot_voice_ok" : "deck.boot_voice_pending") };
      case "brain":
        if (g.state === "ok") return { text: brainModel ? `${brainProvider} · ${brainModel}` : brainProvider };
        if (g.state === "off") return { text: t("deck.boot_brain_off"), onClick: () => setActiveSection("apikeys") };
        return { text: t("deck.boot_brain_pending") };
      case "wake":
        if (g.state === "ok") return { text: t("deck.boot_wake_ok").replace("{0}", wakePhrase) };
        if (g.state === "off") return { text: t("deck.boot_wake_off") };
        return { text: t("deck.boot_wake_pending") };
    }
  };

  // The console shows the gates in boot order, one after the other: a line
  // appears once every line before it has left "pending" — the way a boot
  // log grows. A later gate that is already true waits its turn (seconds at
  // most) rather than jumping the queue and scrambling the order.
  const shownGates = useMemo(() => {
    const out: Gate[] = [];
    for (const g of gates) {
      out.push(g);
      if (g.state === "pending") break;
    }
    return out;
  }, [gates]);

  // The cue under the orb: what to do so something happens. Big, because a
  // person who has never used the app must read it from across the room
  // (maintainer, 2026-08-19: "you have to see that you must say something").
  const wakeOn = gates.find((g) => g.id === "wake")?.state === "ok";

  const corner = (extra: string, children: ReactNode) => (
    <div className={cn("absolute z-10", extra)}>{children}</div>
  );

  // The shockwave has to clear the stage's far corner: scale the reticle-sized
  // ring up to the stage's diagonal, and a little past it.
  const waveScale =
    stage.w && stage.h ? (Math.hypot(stage.w, stage.h) / reticle) * 1.05 : 6;

  return (
    <motion.div
      ref={stageRef}
      data-testid="deck-standby"
      data-phase={phase}
      data-leaving={leaving ? "true" : "false"}
      className={cn("relative overflow-hidden", className)}
      variants={{
        hidden: {},
        show: { opacity: 1 },
        // The layer itself goes LAST: the burst plays out over the board
        // assembling underneath, then the remains fade.
        exit: { opacity: 0, transition: { duration: HANDOFF.stageFadeS, delay: HANDOFF.stageFadeDelayS } },
      }}
      initial={ignite && !reduced ? "hidden" : "show"}
      animate="show"
      exit="exit"
    >
      <HudFrameOverlay variant="bracket" w={stage.w} h={stage.h} live={phase === "standby"} />

      {/* The launch: the whole stage flashes gold for a blink (CSS, on leaving). */}
      <div aria-hidden className="deck-launch-flash pointer-events-none absolute inset-0 bg-primary" />

      {/* The instrument: the ring with the orb in it, centred on the stage. */}
      <div className={cn("absolute inset-0 grid", wide ? "place-items-center" : "place-items-start justify-items-center pt-3")}>
        <div className="relative" style={{ width: ring, height: ring }}>
          {/* On the hand-off the ring draws breath, then bursts past the
              camera, turning (CSS, on leaving) — while the corners slide out
              of the way. */}
          <div className="deck-launch-ring absolute inset-0">
            <StandbyRing
              size={ring}
              gates={gates}
              fresh={pendingAtMount}
              ignite={ignite}
              leaving={leaving}
              sweep={Boolean(listening) && !reduced}
              ping={ignite && readyAt !== null && !reduced ? readyAt : null}
              labels={ring >= LABELS_MIN_RING ? (id) => t(`deck.boot_gate_${id}`) : undefined}
              reticle={reticle}
            />
          </div>

          {/* The burst: the orb's flare and two shockwaves, from the orb's
              centre, over everything on the stage. Invisible at rest; on
              leaving they fire once (CSS), and the layer fades before they
              are done. `--wave-scale` takes a wave past the stage's corner. */}
          <div
            className="pointer-events-none absolute inset-0 grid place-items-center"
            aria-hidden
            style={{ "--wave-scale": waveScale.toFixed(2) } as CSSProperties}
          >
            <div
              className="deck-handoff-flare absolute rounded-full"
              style={{ width: reticle * 1.2, height: reticle * 1.2 }}
            />
            <div
              className="deck-handoff-wave absolute rounded-full"
              data-testid="deck-handoff-wave"
              style={{ width: reticle, height: reticle }}
            />
            <div
              className="deck-handoff-wave deck-handoff-wave-echo absolute rounded-full"
              style={{ width: reticle, height: reticle }}
            />
          </div>

          <div className="absolute inset-0 grid place-items-center">
            <motion.div
              layoutId="deck-orb"
              layoutDependency={reticle}
              transition={ORB_TRAVEL}
              variants={{
                hidden: { opacity: 0, scale: 0.9 },
                show: { opacity: 1, scale: 1, transition: { duration: 0.9, ease: [0.2, 0.7, 0.2, 1] } },
              }}
            >
              <DeckOrb
                steps={steps}
                busy={busy}
                size={reticle}
                readouts={readouts}
                onPress={onPressOrb}
                pressLabel={pressLabel}
                pressDisabled={pressDisabled}
              />
            </motion.div>
          </div>

          {/* Under the reticle, inside the ring: the boot title, then the cue. */}
          <div
            className="absolute inset-x-0 flex justify-center px-6 text-center"
            style={{ top: ring / 2 + reticle / 2 + 12 }}
          >
            {phase === "boot" ? (
              <motion.div
                key="boot-title"
                variants={{ exit: { opacity: 0, y: -10, scale: 0.9, transition: { duration: 0.22, ease: "easeIn" } } }}
              >
                <BootTitle text={t("deck.boot_title")} animate={!reduced} />
              </motion.div>
            ) : (
              <motion.div
                key="cue"
                variants={{
                  hidden: { opacity: 0, y: 6 },
                  show: { opacity: 1, y: 0, transition: { duration: 0.5, delay: 0.3 } },
                  // The cue is pulled into the orb as it launches.
                  exit: { opacity: 0, y: -10, scale: 0.9, transition: { duration: 0.22, ease: "easeIn" } },
                }}
                className="flex flex-col items-center gap-1"
              >
                <p
                  className={cn(
                    "font-display text-lg font-bold uppercase tracking-[0.26em] text-primary sm:text-xl",
                    !reduced && "deck-standby-cue",
                  )}
                  data-testid="deck-standby-cue"
                >
                  {wakeOn ? t("deck.standby_cue").replace("{0}", wakePhrase) : t("deck.standby_cue_off")}
                </p>
                <p className="max-w-[62ch] text-pretty text-xs leading-relaxed text-muted-foreground">
                  {t(wakeOn ? "deck.standby_cue_sub" : "deck.standby_cue_sub_off")}
                </p>
              </motion.div>
            )}
          </div>
        </div>
      </div>

      {/* Top left: which act this is, and the clock. */}
      {corner(
        wide ? "left-5 top-4" : "left-4 top-3",
        <motion.div
          variants={{ exit: { opacity: 0, x: -36, transition: { duration: HANDOFF.cornerS, ease: "easeIn" } } }}
          className="flex flex-col gap-0.5"
        >
          <span
            className={cn(
              "font-mono text-[10px] uppercase tracking-[0.22em]",
              phase === "boot" ? "text-muted-foreground" : "text-primary",
            )}
          >
            {t(phase === "boot" ? "deck.boot_phase" : "deck.standby_phase")}
            {phase === "boot" && <span className="deck-boot-caret ml-1 inline-block h-[1em] w-[0.5em] translate-y-[2px] bg-current" aria-hidden />}
          </span>
          <span className="font-mono text-3xl font-semibold tabular-nums leading-none text-foreground" data-testid="deck-standby-clock">
            {fmtClock(now)}
          </span>
          {readyAt !== null && (
            <span className="font-mono text-[10px] tabular-nums tracking-[0.12em] text-muted-foreground">
              {t("deck.standby_since").replace("{0}", fmtClock(readyAt))}
            </span>
          )}
        </motion.div>,
      )}

      {/* Bottom left: the console — the boot log, then the cursor. */}
      {corner(
        wide ? "bottom-4 left-5 max-w-[320px]" : "inset-x-4 bottom-14",
        <motion.div
          variants={{ exit: { opacity: 0, x: -36, transition: { duration: HANDOFF.cornerS, ease: "easeIn" } } }}
          className="font-mono text-[10.5px] leading-[1.6]"
          data-testid="deck-boot-console"
        >
          {shownGates.map((g) => {
            const { text, onClick } = gateText(g);
            const seen = pendingAtMount.has(g.id);
            const at = okAt[g.id];
            const ms = seen && at !== undefined ? at - mountedAt.current : null;
            return (
              <div
                key={g.id}
                className="deck-boot-line flex items-baseline gap-2"
                data-fresh={seen && !reduced ? "true" : "false"}
                data-gate={g.id}
                data-state={g.state}
              >
                <span className="shrink-0 tabular-nums text-muted-foreground/70">
                  {fmtClock(at ?? now)}
                </span>
                <span className={cn("w-[6ch] shrink-0 uppercase tracking-[0.08em]", TAG_TONE[g.state])}>
                  {t(`deck.boot_gate_${g.id}`)}
                </span>
                {onClick ? (
                  <button
                    type="button"
                    onClick={onClick}
                    className={cn("min-w-0 truncate text-left underline-offset-2 hover:underline", TEXT_TONE[g.state])}
                  >
                    {text}
                  </button>
                ) : (
                  <span className={cn("min-w-0 truncate", TEXT_TONE[g.state])}>{text}</span>
                )}
                {g.state === "pending" ? (
                  <span className="deck-boot-dots shrink-0 text-muted-foreground" aria-hidden>
                    <span>·</span>
                    <span>·</span>
                    <span>·</span>
                  </span>
                ) : ms !== null && ms >= 50 ? (
                  <span className="ml-auto shrink-0 pl-2 tabular-nums text-muted-foreground">{fmtMs(ms)}</span>
                ) : null}
              </div>
            );
          })}
          {phase === "standby" && (
            <div className="mt-1 flex items-baseline gap-2">
              <span className="shrink-0 tabular-nums text-muted-foreground/70">{fmtClock(now)}</span>
              <span className="inline-block h-[1.1em] w-[0.55em] shrink-0 self-center bg-foreground/60 motion-safe:animate-pulse" aria-hidden />
              <span className="min-w-0 text-foreground/80">
                {t(
                  listening
                    ? "deck.log_now_idle"
                    : !connected
                      ? "deck.log_now_offline"
                      : voiceState === "error"
                        ? "deck.standby_error"
                        : "deck.standby_still",
                )}
                {quiet > 5_000 && (
                  <span className="text-muted-foreground/70">
                    {" · "}
                    {t("deck.log_quiet_for").replace("{0}", fmtQuiet(quiet))}
                  </span>
                )}
              </span>
            </div>
          )}
        </motion.div>,
      )}

      {/* Bottom right: the board, one press away. */}
      {corner(
        wide ? "bottom-4 right-5 text-right" : "bottom-3 right-4 text-right",
        <motion.div
          variants={{ exit: { opacity: 0, x: 36, transition: { duration: HANDOFF.cornerS, ease: "easeIn" } } }}
          className="flex flex-col items-end gap-1.5"
        >
          <button
            type="button"
            onClick={onOpenBoard}
            className="border border-border px-2.5 py-1 font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground transition-colors hover:border-primary/50 hover:text-primary focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/60"
            style={{ clipPath: "polygon(6px 0, 100% 0, 100% calc(100% - 6px), calc(100% - 6px) 100%, 0 100%, 0 6px)" }}
          >
            {t("deck.open_board")}
          </button>
        </motion.div>,
      )}
    </motion.div>
  );
}

// Signal colours in PAIRS — a tint on black, its deep twin on paper.
const TAG_TONE: Record<GateState, string> = {
  pending: "text-muted-foreground",
  ok: "text-primary",
  off: "text-amber-700 dark:text-amber-300",
};
const TEXT_TONE: Record<GateState, string> = {
  pending: "text-foreground/70",
  ok: "text-foreground/90",
  off: "text-amber-700 dark:text-amber-300",
};

/** The boot title: the name, letter by letter, in the display face. */
function BootTitle({ text, animate }: { text: string; animate: boolean }) {
  const letters = Array.from(text.toUpperCase());
  return (
    <p
      className="font-display text-base font-bold uppercase tracking-[0.32em] text-foreground sm:text-lg"
      data-testid="deck-boot-title"
      aria-label={text}
    >
      {letters.map((ch, i) => (
        <span
          key={`${i}-${ch}`}
          aria-hidden
          className={animate ? "deck-boot-letter" : undefined}
          style={animate ? { animationDelay: `${180 + i * 38}ms` } : undefined}
        >
          {ch === " " ? " " : ch}
        </span>
      ))}
    </p>
  );
}

/**
 * The big ring — the standby's instrument.
 *
 * Bezel, tick scale, the four gate arcs at the compass points (dashed while
 * pending, drawn solid when true, dim and short when honestly off), the
 * sweep while the wake word is being listened for, and the one-shot ping.
 * All strokes are theme gold under the HUD halo so they read on any
 * wallpaper in either appearance.
 */
function StandbyRing({
  size,
  gates,
  fresh,
  ignite,
  leaving,
  sweep,
  ping,
  labels,
  reticle,
}: {
  size: number;
  gates: Gate[];
  /** Gates that were pending when the stage mounted — their arcs draw in. */
  fresh: Set<GateId>;
  ignite: boolean;
  /**
   * The stage is on its way out: the ticks flare clockwise on the CSS
   * cascade (`--tick-i`), so the ignition's own per-tick delay steps aside.
   */
  leaving: boolean;
  sweep: boolean;
  /** Timestamp of the boot's completion, or null — keys the one-shot ping. */
  ping: number | null;
  labels?: (id: GateId) => string;
  reticle: number;
}) {
  const haloId = useId();
  const C = size / 2;
  const R = C - 2;
  const ticks = useMemo(() => ringTicks(3), []);
  const stroke = "hsl(var(--primary))";
  const tickOpacity = [0.3, 0.55, 0.9] as const;

  return (
    <svg
      viewBox={`0 0 ${size} ${size}`}
      className="absolute inset-0 h-full w-full"
      aria-hidden
      data-testid="deck-standby-ring"
      data-sweep={sweep ? "true" : "false"}
    >
      <HudHaloDefs id={haloId} />
      <g filter={`url(#${haloId})`}>
        {/* bezel */}
        <circle
          className="deck-ring-bezel"
          data-ignite={ignite ? "true" : "false"}
          cx={C}
          cy={C}
          r={R * 0.985}
          pathLength={1}
          fill="none"
          stroke={stroke}
          strokeWidth={1}
          opacity={0.38}
          style={{ transform: "rotate(-90deg)", transformOrigin: "center", transformBox: "fill-box" }}
        />

        {/* tick scale — ignites clockwise on boot */}
        {ticks.map((tk, i) => {
          const [ax, ay] = polar(C, C, R * 0.985, tk.deg);
          const inner = tk.weight === 2 ? 0.925 : tk.weight === 1 ? 0.945 : 0.965;
          const [bx, by] = polar(C, C, R * inner, tk.deg);
          return (
            <line
              key={tk.deg}
              className="deck-ring-tick"
              data-ignite={ignite ? "true" : "false"}
              x1={ax}
              y1={ay}
              x2={bx}
              y2={by}
              stroke={stroke}
              strokeWidth={tk.weight === 2 ? 1.5 : 1}
              style={
                {
                  "--tick-opacity": tickOpacity[tk.weight],
                  "--tick-i": i,
                  opacity: "var(--tick-opacity)",
                  animationDelay: ignite && !leaving ? `${i * 9}ms` : undefined,
                } as CSSProperties
              }
            />
          );
        })}

        {/* gate arcs at the compass points */}
        {gates.map((g) => {
          const centre = GATE_ARC_CENTRE[g.id];
          const half = (g.state === "off" ? GATE_ARC_SPAN * 0.35 : GATE_ARC_SPAN) / 2;
          const d = arcPath(C, C, R * 0.905, centre - half, centre + half);
          return (
            <path
              key={g.id}
              className="deck-gate-arc"
              data-gate={g.id}
              data-state={g.state}
              data-fresh={fresh.has(g.id) ? "true" : "false"}
              d={d}
              pathLength={1}
              fill="none"
              stroke={stroke}
              strokeWidth={g.state === "ok" ? 2.5 : 1.5}
              strokeDasharray={g.state === "pending" ? "0.012 0.03" : undefined}
              opacity={g.state === "ok" ? 0.95 : g.state === "off" ? 0.45 : 0.3}
              strokeLinecap="butt"
            />
          );
        })}

        {/* labels inside the arcs */}
        {labels &&
          gates.map((g) => {
            const centre = GATE_ARC_CENTRE[g.id];
            const [lx, ly] = polar(C, C, R * 0.845, centre);
            return (
              <text
                key={g.id}
                x={lx}
                y={ly}
                textAnchor="middle"
                dominantBaseline="middle"
                className="font-mono uppercase"
                style={{ fontSize: 9, letterSpacing: "0.18em" }}
                fill={g.state === "ok" ? "hsl(var(--primary))" : "hsl(var(--muted-foreground))"}
                opacity={g.state === "pending" ? 0.7 : 0.95}
              >
                {labels(g.id)}
              </text>
            );
          })}

        {/* the sweep — a head and a fading trail, turning while listening */}
        {sweep && (
          <g className="deck-ring-sweep" data-testid="deck-ring-sweep">
            {Array.from({ length: 12 }, (_, i) => {
              const a1 = -i * 3;
              const a0 = a1 - 3.2;
              return (
                <path
                  key={i}
                  d={arcPath(C, C, R * 0.965, a0, a1)}
                  fill="none"
                  stroke={stroke}
                  strokeWidth={i === 0 ? 3 : 2}
                  opacity={(0.9 * (12 - i)) / 12}
                  strokeLinecap="butt"
                />
              );
            })}
          </g>
        )}

        {/* the ping — once, when the boot completes */}
        {ping !== null && (
          <circle
            key={ping}
            className="deck-ring-ping"
            cx={C}
            cy={C}
            r={R * 0.985}
            fill="none"
            stroke={stroke}
            strokeWidth={1.5}
            style={{ transformOrigin: "center", transformBox: "fill-box" }}
          />
        )}
      </g>

      {/* a faint scale between reticle and arcs — the ring's inner edge */}
      <circle
        cx={C}
        cy={C}
        r={Math.max(reticle / 2 + 18, R * 0.62)}
        fill="none"
        stroke={stroke}
        strokeWidth={1}
        strokeDasharray="2 8"
        opacity={0.22}
      />
    </svg>
  );
}
