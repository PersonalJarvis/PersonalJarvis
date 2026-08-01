/**
 * "What should run in the new terminal?" — the one menu behind every action
 * that opens a pane.
 *
 * It lives on its own rather than inside the pane header because opening a
 * terminal is not something only a pane does: the grid's split buttons ask it,
 * the chat view's rail asks it, and an empty workspace asks it. Those surfaces
 * used to disagree — a split offered the choice while the rail silently started
 * whatever CLI happened to be first, so the chat view could only ever add more
 * of the same agent (maintainer report 2026-07-31). One component keeps them
 * telling the same story.
 *
 * Before any of them asked, a new pane inherited the anchor's agent silently,
 * which made running a Codex pane next to a Claude Code one impossible from the
 * workspace — you had to close it and start again from the wizard. The backend
 * always accepted an agent per terminal; this is the surface that asks.
 *
 * The list is whatever the backend registered, so it is not a fixed pair of
 * CLIs: a plain terminal (this machine's own shell, no agent around it) sits in
 * the same menu, and a CLI registered later appears here without a change on
 * this side. An entry that is not installed stays listed but disabled, so the
 * absence is visible and explains itself rather than silently not being there.
 */
import { useLayoutEffect, useRef } from "react";
import { cn } from "@/lib/utils";

/** A coding CLI an "open a terminal" action may start. */
export interface SplitAgentChoice {
  /** Backend id — "claude", "codex", "shell". */
  name: string;
  /** What the user reads — "Claude Code", "Plain Terminal". */
  displayName: string;
  installed: boolean;
  /**
   * `"cli"` for a coding agent, `"shell"` for a plain terminal on this
   * machine's own shell. Carried so the menu can say what a choice actually
   * opens without knowing any entry by name.
   */
  kind?: string;
  /** One line under the name — the shell that would open, for instance. */
  description?: string;
}

/**
 * Is there anything to pick?
 *
 * With one installed CLI the menu would hold a single entry, which is a click
 * tax rather than a choice — the caller opens that one straight away. Shared so
 * every surface draws the same line instead of each counting for itself.
 */
export function offersAgentChoice(agents?: SplitAgentChoice[]): boolean {
  if (agents === undefined) return false;
  return agents.filter((a) => a.installed).length !== 1;
}

/** The one installed choice that can be opened without asking. */
export function automaticAgentChoice(
  agents?: SplitAgentChoice[],
): string | undefined {
  const installed = (agents ?? []).filter((agent) => agent.installed);
  return installed.length === 1 ? installed[0].name : undefined;
}

export function AgentPickerMenu({
  title,
  ariaLabel,
  agents,
  onPick,
  onDismiss,
  testId,
  itemTestId,
  dialogId,
  className,
}: {
  /** The line above the entries — "Open beside — what?". */
  title: string;
  ariaLabel: string;
  agents: SplitAgentChoice[];
  onPick: (agent: string) => void;
  onDismiss: () => void;
  testId: string;
  /** Per-entry test id, so each surface keeps its own established names. */
  itemTestId: (agent: string) => string;
  /** DOM-safe id shared with the trigger's aria-controls when provided. */
  dialogId?: string;
  /** Where the menu hangs — the caller owns the anchoring. */
  className?: string;
}) {
  const first = agents.find((a) => a.installed);
  const dialogRef = useRef<HTMLDivElement | null>(null);
  // Capture during render, before `autoFocus` moves focus into the dialog.
  // The cleanup then returns a keyboard user to the exact button that opened
  // it, whichever of the three picker surfaces that happened to be.
  const returnFocus = useRef<HTMLElement | null>(
    typeof document !== "undefined" && document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null,
  );
  const dismiss = () => {
    onDismiss();
    returnFocus.current?.focus();
  };
  useLayoutEffect(() => {
    if (!first) dialogRef.current?.focus();
  }, [first]);
  return (
    <>
      {/* Click-anywhere-else to dismiss, without a global listener that would
          outlive the surface that opened this. */}
      <div className="fixed inset-0 z-40" onMouseDown={dismiss} />
      <div
        ref={dialogRef}
        id={dialogId ?? `${testId}-dialog`}
        role="dialog"
        aria-label={ariaLabel}
        tabIndex={-1}
        data-testid={testId}
        className={cn(
          // Scrolls rather than growing past the window: the list is every CLI
          // the backend registered, and that is six entries on a machine with
          // the usual set installed — more than fits under a button near the
          // top of a laptop screen.
          "absolute z-50 max-h-[70vh] w-60 overflow-y-auto rounded-lg border border-border bg-card p-1 shadow-xl scrollbar-jarvis",
          className,
        )}
        onMouseDown={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === "Escape") {
            e.preventDefault();
            e.stopPropagation();
            dismiss();
          }
        }}
      >
        <p className="px-2 py-1.5 text-[11px] uppercase tracking-wider text-muted-foreground">
          {title}
        </p>
        {!first && (
          <p role="status" className="px-2 py-2 text-xs text-muted-foreground">
            No terminal or coding CLI is available on this machine.
          </p>
        )}
        {agents.map((agent) => (
          <button
            key={agent.name}
            type="button"
            autoFocus={agent === first}
            aria-disabled={!agent.installed}
            data-testid={itemTestId(agent.name)}
            onClick={(e) => {
              e.stopPropagation();
              if (!agent.installed) return;
              onPick(agent.name);
              returnFocus.current?.focus();
            }}
            className="flex w-full items-start justify-between gap-2 rounded-md px-2 py-1.5 text-left text-sm transition-colors hover:bg-primary/10 aria-disabled:cursor-not-allowed aria-disabled:opacity-40 aria-disabled:hover:bg-transparent"
          >
            <span className="min-w-0">
              <span className="block truncate">{agent.displayName}</span>
              {/* What this choice actually opens — "no agent, just a prompt"
                  is the difference a user needs before clicking, and it is the
                  entry's own words rather than a name this menu recognises. */}
              {agent.description && (
                <span className="block truncate text-[11px] text-muted-foreground">
                  {agent.description}
                </span>
              )}
            </span>
            {!agent.installed && (
              <span className="shrink-0 text-[10px] uppercase tracking-wide text-muted-foreground">
                {agent.kind === "shell" ? "no shell here" : "not installed"}
              </span>
            )}
          </button>
        ))}
      </div>
    </>
  );
}
