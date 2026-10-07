import { AlertCircle, Check, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import type { PaneActivity } from "@/lib/agenticIdeApi";

/** Task lifecycle supplied by the backend. Silence alone never means done. */

/**
 * The badge's vocabulary, as a key rather than a word.
 *
 * What a surface with room for a WORD asks for: the chat stage's header spells
 * the state out in the user's language, and it wants the state, not the
 * English label the tooltip carries. One key per look, so "done" and "idle"
 * stay two states there exactly as they are two looks here.
 */
export type PaneActivityState =
  | "working"
  | "starting"
  | "asking"
  | "done"
  | "idle"
  | "live"
  | "exited"
  | "failed"
  | "error"
  | "stopped"
  | "unknown";

/** The accessible meaning of each activity, and how its icon is drawn. */
type Look = {
  state: PaneActivityState;
  label: string;
  className: string;
  icon: "spinner" | "dot" | "ring" | "alert" | "beacon" | "check";
  /**
   * A soft halo of the mark's own colour. Spent on exactly one state — a
   * finished job — so the one row holding something for you glows and the
   * grey ones (exited, a plain shell) stay matte.
   */
  glow?: boolean;
  /** The sentence behind the badge, minus the timing clause. */
  hint: string;
};

/**
 * Every state a pane can be in, in the user's words.
 *
 * A `Record` over the whole vocabulary rather than a chain of `if`s: a value
 * added to the Python `Activity` literal fails to compile here until it is
 * given a label, which is the drift this repo has been bitten by five times
 * (§5). `waiting` carries two looks because it is two different pieces of news
 * — see the module docstring.
 */
const LOOK: Record<Exclude<PaneActivity, "" | "waiting">, Look> = {
  stopped: {
    state: "stopped", label: "stopped", className: "text-muted-foreground",
    icon: "ring", hint: "The task was interrupted.",
  },
  unknown: {
    state: "unknown", label: "status unknown", className: "text-muted-foreground",
    icon: "ring", hint: "No verified task status is available.",
  },
  working: {
    state: "working",
    label: "working",
    // Life. A status is never --foreground: ink is the colour of everything
    // that is NOT a signal, so a state painted in it reads as a label.
    className: "text-accent",
    icon: "spinner",
    hint: "Working — the task is still in progress.",
  },
  starting: {
    state: "starting",
    label: "starting",
    className: "text-muted-foreground",
    icon: "spinner",
    hint: "Starting up. Its agent has not taken the pane yet.",
  },
  asking: {
    state: "asking",
    label: "needs you",
    // Degraded — the pane is stalled until somebody answers it. It used to be
    // a Tailwind sky blue, which is a literal colour and a fourth hue in a
    // palette that has exactly three.
    className: "text-warning",
    /*
     * The one deliberate exception to "motion means busy": a slow radiating
     * ring around a STILL dot. It does not share the spinner's silhouette —
     * rotation reads as grinding, a ping reads as a notification — and this is
     * the single state in the list that wants an action from the user right
     * now, so it is the single one allowed to wave.
     */
    icon: "beacon",
    hint: "Stopped with a question on screen. It is waiting for your answer.",
  },
  exited: {
    state: "exited",
    label: "exited",
    className: "text-muted-foreground",
    icon: "dot",
    hint: "Its process is gone.",
  },
  failed: {
    state: "failed",
    label: "failed",
    className: "text-destructive",
    icon: "alert",
    hint: "Its agent could not be started.",
  },
};

const DONE: Look = {
  state: "done",
  label: "done",
  className: "text-accent",
  icon: "check",
  glow: true,
  hint: "Finished and waiting at its prompt. That it stopped, not that the work is right.",
};

/**
 * Ready, but holding nothing — the same green, drawn as an empty ring.
 *
 * A ring rather than a second colour because this is the SAME piece of news as
 * `done` with one part missing: the pane is quiet and yours to talk to, it just
 * has no finished job behind it. Reading "nothing here yet" out of an unfilled
 * shape is what a fuel gauge does, and it keeps green meaning one thing.
 */
const IDLE: Look = {
  state: "idle",
  label: "idle",
  className: "text-accent",
  icon: "ring",
  hint: "Waiting at its prompt. Nothing has been sent to it yet.",
};

/** The pipe, for the three cases where the pipe is the news. */
const CONNECTING: Look = {
  state: "starting",
  label: "starting",
  className: "text-muted-foreground",
  icon: "spinner",
  hint: "Connecting to the pane.",
};

const EXITED: Look = {
  state: "exited",
  label: "exited",
  className: "text-muted-foreground",
  icon: "dot",
  hint: "Its process is gone.",
};

const BROKEN: Look = {
  state: "error",
  label: "error",
  className: "text-destructive",
  icon: "alert",
  hint: "This pane could not be reached.",
};

/**
 * A live pipe with nothing else known yet.
 *
 * The old badge, kept for exactly two panes: a plain terminal, which is a shell
 * prompt and has no job to be in the middle of, and an agent pane in the second
 * before its first status poll answers.
 *
 * Grey, and hollow: neither pane has a finished job behind it, so neither has
 * earned the green that means "something here is yours".
 */
const CONNECTED: Look = {
  state: "live",
  label: "live",
  className: "text-muted-foreground",
  icon: "ring",
  hint: "Connected.",
};

function lookFor(
  status: string,
  activity: PaneActivity,
  worked: boolean,
): Look {
  if (status === "error") return BROKEN;
  if (status === "exited") return EXITED;
  if (status !== "live") return CONNECTING;
  if (activity === "waiting") return worked ? DONE : IDLE;
  if (activity === "") return CONNECTED;
  return LOOK[activity];
}

/**
 * The word for this pane's state — "working", "done", "needs you".
 *
 * The pill itself is eight pixels of icon and says this only in its tooltip and
 * its accessible name, which is the right size for a header. A pane that has
 * given up its terminal to a card (see `PaneTooNarrowCard` in
 * ./AgenticTerminal) has room to spell it out, and has to: the card IS the
 * pane's state, and there is nothing else on it to read.
 *
 * Exported from here rather than restated there, so the vocabulary keeps one
 * home. A value added to the Python `Activity` literal still fails to compile
 * until `LOOK` is given a label for it, and now both readers inherit that.
 */
export function paneActivityLabel(
  status: string,
  activity: PaneActivity = "",
  worked = false,
): string {
  return lookFor(status, activity, worked).label;
}

/**
 * The same answer as a key — for a surface that translates the word itself.
 *
 * `paneActivityLabel` hands back English, which is right for a tooltip and an
 * accessible name and wrong for a header the user reads in their own
 * language. This is the state behind that label, and the i18n table keys off
 * it (`agentic_grid.pane_chat.state.*`).
 */
export function paneActivityState(
  status: string,
  activity: PaneActivity = "",
  worked = false,
): PaneActivityState {
  return lookFor(status, activity, worked).state;
}

/**
 * How long it has been in this state — "45s", "3 min", "2 hours".
 *
 * A DURATION, not a moment: "waiting since 14:02" makes the reader do the
 * subtraction, and the number they actually want is how long they have been
 * waiting. Rounded on purpose; a pill is a glance, not a stopwatch.
 */
export function durationLabel(since: number, now: number): string {
  const seconds = Math.max(0, Math.round(now / 1000 - since));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.round(minutes / 60);
  return hours === 1 ? "1 hour" : `${hours} hours`;
}

/**
 * The soft self-coloured halo a mark that holds something for you wears.
 *
 * Kept through the no-shadows pass (Design.md) on purpose. These are not
 * elevation: they take `currentColor`, sit on a glyph rather than a surface,
 * and are the only thing separating "finished, waiting for you" from
 * "finished". Removing them would delete a state, not a decoration — the same
 * reason the animated glows in index.css stayed.
 */
const GLOW = "ring-2 ring-current/30";
/** The same halo for a stroked icon, where a box shadow would draw a square. */
const GLOW_STROKE = "[filter:drop-shadow(0_0_3px_currentColor)]";

function Icon({ look }: { look: Look }) {
  if (look.icon === "spinner")
    return <Loader2 className="h-3 w-3 animate-spin motion-reduce:animate-none" />;
  if (look.icon === "check")
    return (
      <Check
        className={cn("h-3.5 w-3.5", look.glow && GLOW_STROKE)}
        strokeWidth={3}
        aria-hidden="true"
      />
    );
  if (look.icon === "dot")
    return (
      <span
        className={cn("h-2 w-2 rounded-full bg-current", look.glow && GLOW)}
        aria-hidden="true"
      />
    );
  if (look.icon === "beacon")
    return (
      <span className="relative flex h-2 w-2" aria-hidden="true">
        {/* The halo — a slow ping, hidden for anyone who asked their OS for
            less motion; the still dot underneath carries the state alone. */}
        <span className="absolute inset-0 animate-ping rounded-full bg-current opacity-60 [animation-duration:1.8s] motion-reduce:hidden" />
        <span className={cn("relative h-2 w-2 rounded-full bg-current", GLOW)} />
      </span>
    );
  if (look.icon === "ring")
    return (
      <span
        className="h-2 w-2 rounded-full border-[1.5px] border-current"
        aria-hidden="true"
      />
    );
  if (look.icon === "alert") return <AlertCircle className="h-3 w-3" />;
  return null;
}

export function PaneActivityPill({
  status,
  detail,
  activity = "",
  since = 0,
  worked = false,
  now,
}: {
  /** The socket's own view: `connecting`, `live`, `exited`, `error`. */
  status: string;
  /** Whatever the socket said about that, shown in the tooltip. */
  detail?: string;
  activity?: PaneActivity;
  /** When the pane entered this state (epoch seconds); 0 when unknown. */
  since?: number;
  /** Has anything ever been asked of this pane? */
  worked?: boolean;
  /** Injectable clock, in milliseconds — the tests do not race the wall. */
  now?: number;
}) {
  const look = lookFor(status, activity, worked);
  // How long it has been in this state, when the backend knows. Only in the
  // tooltip: the badge itself sits in a 64-pixel column beside a call-sign, and
  // a number that changes every second there is movement without information.
  const elapsed = since > 0 ? durationLabel(since, now ?? Date.now()) : "";
  const title = [look.hint, elapsed && `For ${elapsed}.`, detail]
    .filter(Boolean)
    .join(" ");
  return (
    <span
      data-testid="pane-activity"
      data-activity={activity || status}
      data-icon={look.icon}
      className={cn(
        "flex h-4 w-4 shrink-0 items-center justify-center transition-colors duration-300",
        look.className,
      )}
      title={title}
      aria-label={`${look.label}. ${title}`}
    >
      {/* Keyed by the shape it is changing TO, so a state change replaces the
          icon and plays one short zoom-in — the flip from spinner to dot is
          the news the whole badge exists for, and a 200 ms pop is what makes
          it visible in the corner of the eye without adding standing motion. */}
      <span
        key={look.icon}
        className="flex items-center justify-center animate-in fade-in zoom-in-50 duration-200 motion-reduce:animate-none"
      >
        <Icon look={look} />
      </span>
    </span>
  );
}
