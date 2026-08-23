/**
 * "Install this CLI" — the whole flow, from the picker, without leaving it.
 *
 * The absence of a coding CLI used to be a dead end wearing a label: the entry
 * sat in the "open a terminal — what?" menu greyed out under the words NOT
 * INSTALLED, and the only way forward was to know that a CLIs page exists,
 * find it, and come back. Every registered entry that CAN be installed now
 * offers it where the user meets it (maintainer, 2026-08-23).
 *
 * Three things make this honest rather than a progress bar over a hope:
 *
 * 1. **The install runs in a real terminal the user can read.** `npm install
 *    -g` fails for reasons only its own output explains — a permissions error,
 *    a proxy, a Node that is too old. A spinner would hide every one of them.
 *    It is also the same PTY the CLI itself would run in, so a machine where
 *    the pane cannot start says so here rather than after the install.
 * 2. **"Installed" is a fresh probe, never an exit code.** A package manager
 *    that exits 0 has not proved that a binary is on this app's PATH — the
 *    recurring macOS/Linux symptom is exactly that gap. The dialog polls the
 *    single-entry recheck route, which re-augments PATH and looks, so the
 *    green line means the app can actually find and start the thing.
 * 3. **It reports failure as failure.** The dialog never closes itself on a
 *    guess; the user closes it, and the row behind it tells the truth either
 *    way at the next read.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Check, Loader2, X } from "lucide-react";

import { WorkspaceTerminal } from "../workspace/WorkspaceTerminal";
import { recheckAgent } from "@/lib/agenticIdeApi";
import { AgentMark } from "./AgentMark";

/**
 * How often the dialog asks whether the binary has appeared.
 *
 * One subprocess per ask, against ONE entry — cheap next to the full sweep,
 * but not free, and nobody watching an npm install needs sub-second news. Four
 * seconds is under the time it takes to notice a finished install and reach
 * for the mouse, which is the only latency that matters here.
 */
const POLL_MS = 4000;

/** Long enough for a slow registry over a slow line; short enough to end. */
const MAX_POLL_MS = 15 * 60 * 1000;

interface AgentInstallDialogProps {
  /** Registry id — "codex", "deepseek-harness". */
  agent: string;
  /** What the user reads — "DeepSeek Harness". */
  displayName: string;
  /** The command that will run, shown before it does. */
  command?: string;
  /** The mark for a CLI the user added; shipped entries need none. */
  logoUrl?: string;
  /**
   * Closing hands back whether the entry ended up installed, so the surface
   * that opened this can re-read its list once instead of polling too.
   */
  onClose: (installed: boolean) => void;
}

export function AgentInstallDialog({
  agent,
  displayName,
  command,
  logoUrl,
  onClose,
}: AgentInstallDialogProps) {
  const [installed, setInstalled] = useState(false);
  const [version, setVersion] = useState<string | null>(null);
  /*
   * Read by the poll, never a dependency of it. The poll must keep its own
   * identity for the whole dialog — restarting the interval on every state
   * change would reset the elapsed budget below and poll forever.
   */
  const installedRef = useRef(false);
  installedRef.current = installed;

  const close = useCallback(() => onClose(installedRef.current), [onClose]);

  useEffect(() => {
    let cancelled = false;
    const startedAt = Date.now();
    const ask = () => {
      if (cancelled || installedRef.current) return;
      void recheckAgent(agent).then(
        (row) => {
          if (cancelled || !row.installed) return;
          setInstalled(true);
          setVersion(row.version);
        },
        () => {
          /* A failed probe is not an answer. The next tick asks again, and the
             dialog keeps showing the terminal — which is where the reason for
             a genuinely broken install is written anyway. */
        },
      );
    };
    const timer = window.setInterval(() => {
      if (Date.now() - startedAt > MAX_POLL_MS) {
        window.clearInterval(timer);
        return;
      }
      ask();
    }, POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [agent]);

  // Escape closes, like every other overlay in the app. Bound on the document
  // because focus starts inside the xterm canvas, which swallows keystrokes.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [close]);

  if (typeof document === "undefined") return null;

  return createPortal(
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-scrim/70 p-4 backdrop-blur-sm"
      onMouseDown={close}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={`Install ${displayName}`}
        data-testid={`agent-install-dialog-${agent}`}
        className="flex h-[32rem] max-h-full w-full max-w-3xl flex-col overflow-hidden rounded-xl border border-border bg-card shadow-2xl"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <header className="flex items-center gap-3 border-b border-border px-4 py-3">
          <AgentMark
            agent={agent}
            label={displayName}
            size="sm"
            logoUrl={logoUrl}
          />
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-semibold text-foreground">
              Install {displayName}
            </p>
            {/* The command, before and while it runs. A user who would rather
                run it in their own shell can read it here and close this. */}
            {command && (
              <p className="truncate font-mono text-[11px] text-muted-foreground">
                {command}
              </p>
            )}
          </div>
          <button
            type="button"
            aria-label="Close"
            data-testid={`agent-install-close-${agent}`}
            onClick={close}
            className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-primary/10 hover:text-foreground"
          >
            <X className="h-4 w-4" />
          </button>
        </header>

        {/* The installer's own output. Keyed by nothing: this component mounts
            once per install, so the PTY is opened once and never restarted
            under a running package manager. */}
        <div className="min-h-0 flex-1">
          <WorkspaceTerminal
            paneKey={`install-${agent}`}
            installName={agent}
            title={`Installing ${displayName}`}
            /* The pane must never be an empty black rectangle. A package
               manager can take several seconds to say its first word, and in
               that gap the only honest thing on screen is what is about to
               run. Dim, because it is this app talking, not the installer. */
            banner={
              command
                ? `\x1b[2m$ ${command}\x1b[0m`
                : `\x1b[2mStarting the installer for ${displayName}…\x1b[0m`
            }
          />
        </div>

        <footer className="flex items-center justify-between gap-3 border-t border-border px-4 py-3">
          {installed ? (
            <p
              data-testid={`agent-install-done-${agent}`}
              /* Both appearances explicitly: emerald-400 is legible on the
                 dark app and washes out to nearly nothing on paper. */
              className="flex items-center gap-2 text-sm font-medium text-emerald-600 dark:text-emerald-400"
            >
              <Check className="h-4 w-4" />
              <span>
                {displayName} is installed
                {version ? ` — ${version}` : ""}
              </span>
            </p>
          ) : (
            <p className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              {/* Not "installing…": what is true is that this app is watching.
                  The installer's own progress is the terminal above, and
                  claiming to know its state would be inventing one. */}
              <span>Watching for {displayName} to appear…</span>
            </p>
          )}
          <button
            type="button"
            onClick={close}
            data-testid={`agent-install-dismiss-${agent}`}
            className={
              installed
                ? "shrink-0 rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90"
                : "shrink-0 rounded-md border border-border px-3 py-1.5 text-sm text-foreground transition-colors hover:bg-primary/10"
            }
          >
            {installed ? "Done" : "Close"}
          </button>
        </footer>
      </div>
    </div>,
    document.body,
  );
}
