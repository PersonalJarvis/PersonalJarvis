import { AlertCircle, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import type { PaneActivity } from "@/lib/agenticIdeApi";

/**
 * The one badge on a pane: is anybody still owed something by this terminal?
 *
 * ## The question it replaced
 *
 * Every pane used to be labelled `live`, which answers a question nobody was
 * asking. `live` is a property of the PIPE — the socket is up and a process is
 * on the other end — and it stays true for a terminal that finished its work
 * twenty minutes ago, for one sitting on an unanswered permission prompt, and
 * for one grinding through a refactor. In a chat-mode list of a dozen agents,
 * that is a column of identical green dots next to twelve panes in completely
 * different states, and the user has to open each one to find out which.
 *
 * What a person actually wants to know is whether the agent is still WORKING or
 * has STOPPED, and this pill answers that instead. The pipe is still reported,
 * but only when it has something to say (connecting, exited, broken) — a
 * healthy connection is the boring case and is now spent on the useful claim.
 *
 * ## Why it holds for every coding CLI, including ones added later
 *
 * The distinction is not made here and not made from anything a product prints.
 * The backend derives it from the terminal SCREEN — a pane whose picture keeps
 * changing is working, a pane that stands still has stopped — which is a
 * property of the terminal rather than of whatever is running inside it. Two
 * earlier attempts read Claude Code's interrupt hint and Codex's bracketed
 * clock, and each broke on the next product and on the next release of its own.
 * See `jarvis/agentic_ide/activity.py`, which measured all four installed CLIs
 * before committing to the rule.
 *
 * So a CLI connected next year gets a working status pill with no code here,
 * and none there either.
 *
 * ## Why "done" and "idle" are not the same word
 *
 * A finished agent and a terminal nobody has ever spoken to show the SAME still
 * screen. Calling both "done" would invent a job for the second one, and be
 * read as "your work is ready" for a pane that has done none. `worked` — has
 * anything ever been asked of this pane, including the conversation it resumed
 * — is what separates them.
 *
 * Nothing here claims the work is CORRECT. "done" means the pane went quiet,
 * which is all a terminal can prove.
 *
 * ## Why motion means "busy" and colour means "ready"
 *
 * The two states a person scans this list for — still grinding vs. finished —
 * are told apart by SHAPE first, not by hue: a pane that is working shows a
 * turning spinner, and a pane that has stopped shows a still dot. That reads at
 * a glance, survives every colour-blindness, and it is why the working state is
 * no longer a pulsing dot: a slow throb and a steady dot are the same silhouette
 * at 8 pixels, so the difference lived entirely in a colour the eye had to
 * compare against its neighbours to judge.
 *
 * Colour then carries the second question — is this pane's stillness news? Amber
 * is the app's own accent and marks a pane holding something for you: filled for
 * a finished job, hollow for one that is merely ready and has done nothing yet.
 * Blue is spent on the one state that wants an action from you right now, a pane
 * stopped on a question. Grey is for a pane with nothing to report, red for a
 * broken one.
 */

/** The accessible meaning of each activity, and how its icon is drawn. */
type Look = {
  label: string;
  className: string;
  icon: "spinner" | "dot" | "ring" | "alert" | "beacon";
  /**
   * A soft halo of the dot's own colour. Spent on exactly one state — a
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
  working: {
    label: "working",
    className: "text-amber-400",
    icon: "spinner",
    hint: "Working — its screen is still changing.",
  },
  starting: {
    label: "starting",
    className: "text-muted-foreground",
    icon: "spinner",
    hint: "Starting up. Its agent has not taken the pane yet.",
  },
  asking: {
    label: "needs you",
    className: "text-sky-400",
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
    label: "exited",
    className: "text-muted-foreground",
    icon: "dot",
    hint: "Its process is gone.",
  },
  failed: {
    label: "failed",
    className: "text-destructive",
    icon: "alert",
    hint: "Its agent could not be started.",
  },
};

const DONE: Look = {
  label: "done",
  className: "text-amber-400",
  icon: "dot",
  glow: true,
  hint: "Finished and waiting at its prompt. That it stopped, not that the work is right.",
};

/**
 * Ready, but holding nothing — the same amber, drawn hollow.
 *
 * A ring rather than a second colour because this is the SAME piece of news as
 * `done` with one part missing: the pane is quiet and yours to talk to, it just
 * has no finished job behind it. Reading "empty" out of an unfilled shape is
 * what a fuel gauge does, and it keeps the accent colour meaning one thing.
 */
const IDLE: Look = {
  label: "idle",
  className: "text-amber-400/60",
  icon: "ring",
  hint: "Waiting at its prompt. Nothing has been sent to it yet.",
};

/** The pipe, for the three cases where the pipe is the news. */
const CONNECTING: Look = {
  label: "starting",
  className: "text-muted-foreground",
  icon: "spinner",
  hint: "Connecting to the pane.",
};

const EXITED: Look = {
  label: "exited",
  className: "text-muted-foreground",
  icon: "dot",
  hint: "Its process is gone.",
};

const BROKEN: Look = {
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
 * earned the accent colour that means "something here is yours".
 */
const CONNECTED: Look = {
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

/** The soft self-coloured halo a dot that holds something for you wears. */
const GLOW = "shadow-[0_0_5px_currentColor]";

function Icon({ look }: { look: Look }) {
  if (look.icon === "spinner")
    return <Loader2 className="h-3 w-3 animate-spin motion-reduce:animate-none" />;
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
