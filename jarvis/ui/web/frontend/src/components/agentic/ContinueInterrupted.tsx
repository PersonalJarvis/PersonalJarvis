/**
 * "Continue interrupted" — the toolbar button that restarts work a restart stopped.
 *
 * ## The gap it closes
 *
 * Resuming a workspace brings the panes back and reconnects each one to the
 * conversation it was having. It cannot make the agents carry on: a coding CLI
 * launched on an old transcript reads it and then waits at its prompt. So an
 * agent that was halfway through a job comes back knowing every detail of it and
 * doing nothing — and on screen that is indistinguishable from a pane that
 * finished. The user's report was exactly that: the sessions were interrupted,
 * twelve terminals sat there, and nothing said which of them still had work.
 *
 * So: a button that checks, a list that says which panes are waiting, and one
 * click that types `continue` into them.
 *
 * ## Why a button and not an automatic nudge
 *
 * Sending "continue" to every resumed pane the moment a workspace comes back
 * would restart twelve agents on twelve unattended tasks because somebody
 * reopened a folder to look at one file. And an agent that was interrupted ON
 * PURPOSE ("stop, that is the wrong approach") would cheerfully resume the thing
 * it was told to stop. The offer is a click, never a side effect of opening a
 * workspace.
 *
 * ## Why it lists panes rather than just counting them
 *
 * Because "continue" is not one answer. A pane whose agent is not running cannot
 * be typed into at all, and each pane carries the job it was last given — which
 * is how somebody decides that two of the five are worth restarting and the
 * others are not. Both are shown BEFORE the click, not discovered after it.
 */
import { useCallback, useEffect, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { AlertCircle, FolderGit2, Loader2, PlayCircle, Terminal, X } from "lucide-react";

import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { useDocumentVisible } from "@/hooks/useDocumentVisible";
import {
  continueInterrupted,
  fetchInterrupted,
  type ContinueResult,
  type InterruptedOffer,
  type InterruptedPane,
} from "@/lib/agenticIdeApi";
import { useEventStore } from "@/store/events";

/**
 * How often the count behind the button is refreshed.
 *
 * Slower than the recap poll on purpose: this answer only changes when a pane is
 * resumed or driven again, which is a human-paced event. The dialog re-reads on
 * open regardless, so nothing on screen is ever the stale copy.
 */
const POLL_MS = 15_000;

const EMPTY: InterruptedOffer = {
  count: 0,
  continuable_count: 0,
  prompt: "continue",
  panes: [],
};

interface ContinueInterruptedProps {
  /** Disable the control while the workspace is busy with something else. */
  busy?: boolean;
  /**
   * Is the section holding this control the one on screen? The workspace stays
   * mounted behind other sections (see MainView), and this poll runs for as
   * long as it is open. Defaults to true — the behaviour before it could hide.
   */
  onScreen?: boolean;
}

/** One line of plain language for what the button will actually do. */
export function continueSummary(offer: InterruptedOffer, t: (key: string) => string): string {
  if (offer.count === 0) return t("agentic_grid.continue.none");
  const blocked = offer.count - offer.continuable_count;
  const base = t(
    offer.continuable_count === 1
      ? "agentic_grid.continue.summary_one"
      : "agentic_grid.continue.summary_many",
  ).replace("{0}", String(offer.continuable_count));
  if (blocked === 0) return base;
  return `${base} ${t("agentic_grid.continue.summary_blocked").replace("{0}", String(blocked))}`;
}

/** What to tell the user after the instruction went out. */
export function continueOutcome(
  result: ContinueResult,
  t: (key: string) => string,
): { tone: "success" | "warning" | "error"; message: string } {
  const parts: string[] = [];
  if (result.continued.length > 0) {
    parts.push(
      t("agentic_grid.continue.result_continued")
        .replace("{0}", String(result.continued.length))
        .replace("{1}", result.continued.join(", ")),
    );
  }
  // Held, not sent: their agents are still starting. Said separately because
  // "carrying on" would be a claim about panes that have not been typed into
  // yet — they are a promise the backend keeps a few seconds later.
  if (result.queued.length > 0) {
    parts.push(
      t("agentic_grid.continue.result_queued")
        .replace("{0}", String(result.queued.length))
        .replace("{1}", result.queued.join(", ")),
    );
  }
  // Its own sentence, never folded into the success count: those panes were
  // typed into without a confirmed submit, so the text may be sitting in the
  // input box waiting for a keypress the user has to make.
  if (result.unconfirmed.length > 0) {
    parts.push(
      t("agentic_grid.continue.result_unconfirmed").replace("{0}", result.unconfirmed.join(", ")),
    );
  }
  if (result.failed.length > 0) {
    parts.push(
      t("agentic_grid.continue.result_failed").replace(
        "{0}",
        result.failed.map((f) => `${f.name} (${f.detail})`).join("; "),
      ),
    );
  }
  const tone =
    result.failed.length > 0 && result.continued.length === 0
      ? "error"
      : result.failed.length > 0 || result.unconfirmed.length > 0
        ? "warning"
        : "success";
  return { tone, message: parts.join(" ") || t("agentic_grid.continue.none") };
}

function PaneRow({
  pane,
  busy,
  sending,
  onContinue,
}: {
  pane: InterruptedPane;
  busy: boolean;
  /** This pane's own instruction is in flight — its button is spent. */
  sending: boolean;
  onContinue: () => void;
}) {
  const t = useT();
  // Already spoken for: in flight from this window, or held by the backend
  // until its agent comes up. Either way a second press would be a second
  // "continue" into the same conversation.
  const spent = sending || Boolean(pane.queued);
  return (
    <li
      data-testid={`interrupted-pane-${pane.name}`}
      className={cn(
        "rounded-lg border p-3",
        pane.continuable ? "border-border bg-card/60" : "border-amber-500/40 bg-amber-500/10",
      )}
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
            <Terminal className="h-3.5 w-3.5 shrink-0 translate-y-0.5 text-primary" />
            <span className="font-medium text-foreground">{pane.name}</span>
            <span className="text-xs text-muted-foreground">{pane.display_name}</span>
            <span className="flex items-center gap-1 text-xs text-muted-foreground">
              <FolderGit2 className="h-3 w-3 shrink-0" />
              {pane.workspace}
            </span>
          </div>
          {pane.last_task ? (
            <p className="mt-1 text-xs text-muted-foreground">
              {t("agentic_grid.continue.last_task").replace("{0}", pane.last_task)}
            </p>
          ) : (
            <p className="mt-1 text-xs text-muted-foreground">
              {t("agentic_grid.continue.no_task")}
            </p>
          )}
          {!pane.continuable && (
            <p className="mt-1.5 flex items-start gap-1.5 text-xs text-amber-200">
              <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              {pane.blocked_reason}
            </p>
          )}
          {pane.continuable && pane.starting && (
            <p
              data-testid={`interrupted-starting-${pane.name}`}
              className="mt-1.5 text-xs text-muted-foreground"
            >
              {t("agentic_grid.continue.starting")}
            </p>
          )}
        </div>
        <button
          type="button"
          data-testid={`continue-pane-${pane.name}`}
          className="btn-ghost shrink-0 disabled:cursor-not-allowed disabled:opacity-40"
          disabled={busy || spent || !pane.continuable}
          onClick={onContinue}
        >
          {sending ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <PlayCircle className="h-4 w-4" />
          )}
          {spent ? t("agentic_grid.continue.sending") : t("agentic_grid.continue.one")}
        </button>
      </div>
    </li>
  );
}

export function ContinueInterrupted({
  busy = false,
  onScreen = true,
}: ContinueInterruptedProps) {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const [offer, setOffer] = useState<InterruptedOffer>(EMPTY);
  const [open, setOpen] = useState(false);
  const [working, setWorking] = useState(false);
  // Call-signs whose instruction is in flight from THIS window. Per pane rather
  // than one global flag, so continuing one pane does not freeze the buttons of
  // the others while it waits for its screen to change.
  const [sending, setSending] = useState<Set<string>>(() => new Set());

  const refresh = useCallback(async (): Promise<InterruptedOffer> => {
    try {
      const next = await fetchInterrupted();
      setOffer(next);
      return next;
    } catch {
      // A failed check must not put an error in front of somebody who is
      // working — the button simply keeps its last honest count.
      return EMPTY;
    }
  }, []);

  /*
   * Poll only while there is somebody to show a count to.
   *
   * Two ways for that to be false and one answer for both: the window is
   * minimized or behind another (`documentVisible`), or the user is in another
   * section while this workspace stays mounted behind it (`onScreen`).
   *
   * Binding the effect to that answer rather than checking it inside the tick
   * is also the catch-up: coming back rebuilds the effect, and the first thing
   * a rebuilt effect does is read. That matters here more than most — the
   * restart that interrupts the panes is exactly the event that happens while
   * nobody is looking.
   */
  const documentVisible = useDocumentVisible();
  const polling = onScreen && documentVisible;
  useEffect(() => {
    if (!polling) return;
    void refresh();
    const timer = window.setInterval(() => void refresh(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [refresh, polling]);

  const run = async (names?: string[]) => {
    // Which panes this press owns, marked BEFORE the request goes out.
    //
    // Delivering a "continue" takes seconds — the submit is verified against
    // the pane's own screen — and a button that stays live for that whole time
    // is a button somebody presses again. The backend refuses the duplicate
    // too (it claims each pane synchronously), but a control that looks
    // clickable and silently does nothing is its own bug: this makes the wait
    // visible where the click happened.
    const claimed = names ?? offer.panes.filter((p) => p.continuable).map((p) => p.name);
    setSending((current) => new Set([...current, ...claimed]));
    setWorking(true);
    try {
      const result = await continueInterrupted(names);
      const { tone, message } = continueOutcome(result, t);
      pushToast(tone, message);
      const next = await refresh();
      // Nothing left to offer: close rather than leave an empty dialog in front
      // of a workspace the user now wants to watch.
      if (next.count === 0) setOpen(false);
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setSending((current) => {
        const rest = new Set(current);
        for (const name of claimed) rest.delete(name);
        return rest;
      });
      setWorking(false);
    }
  };

  const waiting = offer.count > 0;
  return (
    <Dialog.Root
      open={open}
      onOpenChange={(next) => {
        if (working) return;
        setOpen(next);
        // Checked on every open, so the list is never the poll's stale copy —
        // and so the button answers "check what was interrupted" honestly even
        // when the count behind it happens to be a few seconds old.
        if (next) void refresh();
      }}
    >
      <Dialog.Trigger asChild>
        <button
          type="button"
          data-testid="continue-interrupted-open"
          disabled={busy}
          title={t("agentic_grid.continue.hint")}
          className={cn(
            // Quiet at rest, amber only while panes actually wait — the same
            // rule the rest of the toolbar follows (TOOLBAR_BTN in AgenticGrid).
            "flex h-7 shrink-0 items-center gap-1.5 rounded-md px-2 text-xs font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-40",
            waiting
              ? "bg-amber-500/15 text-amber-200 hover:bg-amber-500/25"
              : "text-muted-foreground hover:bg-secondary hover:text-foreground",
          )}
        >
          <PlayCircle className="h-4 w-4" />
          {t("agentic_grid.continue.button")}
          {waiting && (
            <span
              data-testid="continue-interrupted-count"
              className="rounded bg-amber-500/25 px-1.5 py-0.5 font-mono text-[10px]"
            >
              {offer.count}
            </span>
          )}
        </button>
      </Dialog.Trigger>

      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-background/70 backdrop-blur-sm" />
        <Dialog.Content
          data-testid="continue-interrupted"
          className="fixed left-1/2 top-1/2 z-50 flex max-h-[min(40rem,calc(100vh-4rem))] w-[min(40rem,calc(100vw-3rem))] -translate-x-1/2 -translate-y-1/2 flex-col rounded-2xl border border-border bg-card shadow-2xl"
          onEscapeKeyDown={(event) => {
            if (working) event.preventDefault();
          }}
          onPointerDownOutside={(event) => {
            if (working) event.preventDefault();
          }}
        >
          <div className="flex items-start justify-between gap-4 border-b border-border px-5 py-4">
            <div className="min-w-0">
              <Dialog.Title className="font-display text-base font-semibold">
                {t("agentic_grid.continue.title")}
              </Dialog.Title>
              <Dialog.Description className="mt-1 text-xs text-muted-foreground">
                {t("agentic_grid.continue.description")}
              </Dialog.Description>
            </div>
            <Dialog.Close asChild>
              <button
                type="button"
                aria-label={t("agentic_grid.continue.close")}
                disabled={working}
                className="shrink-0 rounded-lg p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-40"
              >
                <X className="h-4 w-4" />
              </button>
            </Dialog.Close>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto scrollbar-jarvis px-5 py-4">
            {offer.count === 0 ? (
              <p data-testid="continue-interrupted-empty" className="text-sm text-muted-foreground">
                {t("agentic_grid.continue.none")}
              </p>
            ) : (
              <ul className="space-y-2">
                {offer.panes.map((pane) => (
                  <PaneRow
                    key={`${pane.workspace_id}:${pane.key}`}
                    pane={pane}
                    busy={busy}
                    sending={sending.has(pane.name)}
                    onContinue={() => void run([pane.name])}
                  />
                ))}
              </ul>
            )}
          </div>

          <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border px-5 py-3">
            <p className="min-w-0 text-xs text-muted-foreground">{continueSummary(offer, t)}</p>
            <div className="flex shrink-0 items-center gap-2">
              <Dialog.Close asChild>
                <button type="button" className="btn-ghost" disabled={working}>
                  {t("agentic_grid.continue.close")}
                </button>
              </Dialog.Close>
              <button
                type="button"
                data-testid="continue-interrupted-all"
                className="btn-primary disabled:cursor-not-allowed disabled:opacity-40"
                disabled={busy || working || offer.continuable_count === 0}
                onClick={() => void run()}
              >
                {working ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <PlayCircle className="h-4 w-4" />
                )}
                {working
                  ? t("agentic_grid.continue.sending")
                  : t("agentic_grid.continue.all").replace(
                      "{0}",
                      String(offer.continuable_count),
                    )}
              </button>
            </div>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
