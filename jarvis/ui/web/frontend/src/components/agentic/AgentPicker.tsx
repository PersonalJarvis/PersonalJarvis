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
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { cn } from "@/lib/utils";
import { AgentMark } from "./AgentMark";

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
  /**
   * The mark for an entry this bundle has no asset for — a CLI the user added.
   * Absent for everything else, where `AgentMark`'s own table answers.
   */
  logoUrl?: string;
}

/**
 * Is there anything to pick?
 *
 * With one installed CLI the menu would hold a single entry, which is a click
 * tax rather than a choice — the caller opens that one straight away. Shared so
 * every surface draws the same line instead of each counting for itself.
 */
export function offersAgentChoice(agents?: SplitAgentChoice[]): boolean {
  return (agents ?? []).filter((a) => a.installed).length > 1;
}

/**
 * The menu's own footprint, used to decide whether it still fits below.
 *
 * Wide enough that the one-line description under each name survives instead of
 * being truncated mid-word: at 240px "Moonshot AI's terminal coding agent" was
 * cut to "…terminal coding ag…", which is a label that costs a line and answers
 * nothing. Keep in step with the `w-*` class on the menu below, which is what
 * governs while the menu still hangs inside its caller.
 */
const MENU_WIDTH_PX = 272;
const MENU_GAP_PX = 4;
/** Never hang closer than this to a window edge. */
const VIEWPORT_MARGIN_PX = 8;

type FixedPlacement = { left: number; top: number; maxHeight: number };

/**
 * Where a detached menu hangs, measured from the element it belongs to.
 *
 * Right-aligned with the anchor, because that is where the buttons that open
 * it sit, and flipped above it when the space below is the shorter of the two.
 * Both numbers are clamped into the window: a pane in the last column would
 * otherwise put half the menu past the right edge, where it is unreachable.
 */
function placeMenu(rect: DOMRect, viewport: { width: number; height: number }): FixedPlacement {
  const below = viewport.height - rect.bottom - MENU_GAP_PX - VIEWPORT_MARGIN_PX;
  const above = rect.top - MENU_GAP_PX - VIEWPORT_MARGIN_PX;
  const dropsDown = below >= above;
  const maxHeight = Math.max(96, Math.min(dropsDown ? below : above, viewport.height * 0.7));
  const left = Math.max(
    VIEWPORT_MARGIN_PX,
    Math.min(rect.right - MENU_WIDTH_PX, viewport.width - MENU_WIDTH_PX - VIEWPORT_MARGIN_PX),
  );
  return {
    left,
    top: dropsDown ? rect.bottom + MENU_GAP_PX : Math.max(VIEWPORT_MARGIN_PX, rect.top - MENU_GAP_PX - maxHeight),
    maxHeight,
  };
}

export function AgentPickerMenu({
  title,
  ariaLabel,
  agents,
  onPick,
  onDismiss,
  testId,
  itemTestId,
  className,
  anchorTo,
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
  /** Where the menu hangs — the caller owns the anchoring. */
  className?: string;
  /**
   * Hang the menu off this element, through a portal, instead of inside the
   * caller's own box.
   *
   * For surfaces that CLIP: a terminal pane is `overflow-hidden` (it has to be
   * — xterm's canvas must not paint past the frame), so a menu positioned
   * inside one is cut off at the pane's edge, and a pane six rows tall showed
   * a sliver of the first entry and nothing else. Rendering into the body puts
   * the list back in front of the window rather than inside a box the size of
   * the thing it was opened from. Absent, the menu stays where it always was.
   */
  anchorTo?: HTMLElement | null;
}) {
  const first = agents.find((a) => a.installed);
  const [placement, setPlacement] = useState<FixedPlacement | null>(null);

  /*
   * Re-measure while the menu is open, not once when it opened.
   *
   * The pane it hangs off is in a grid that moves: a seam being dragged, a
   * sibling closing, the window resized. A menu that kept its opening
   * coordinates would drift away from the button that spawned it. Passive
   * capture on scroll, so a scroller anywhere between here and the body counts.
   */
  useEffect(() => {
    if (!anchorTo) return;
    const measure = () =>
      setPlacement(
        placeMenu(anchorTo.getBoundingClientRect(), {
          width: window.innerWidth || 0,
          height: window.innerHeight || 0,
        }),
      );
    measure();
    window.addEventListener("resize", measure);
    window.addEventListener("scroll", measure, true);
    return () => {
      window.removeEventListener("resize", measure);
      window.removeEventListener("scroll", measure, true);
    };
  }, [anchorTo]);

  const detached = anchorTo != null && typeof document !== "undefined";
  /*
   * Undefined while the menu hangs inside its caller (the classes place it);
   * the measured rectangle once it has been detached; and, for the single
   * frame between mounting and measuring, hidden rather than absent — the
   * entries keep their tab order and the autofocus below still lands, and
   * nobody sees a menu in the top-left corner on its way to where it belongs.
   */
  const menuStyle: React.CSSProperties | undefined = !detached
    ? undefined
    : placement
      ? {
          position: "fixed",
          left: placement.left,
          top: placement.top,
          width: MENU_WIDTH_PX,
          maxHeight: placement.maxHeight,
        }
      : { visibility: "hidden" };
  const menu = (
    <>
      {/* Click-anywhere-else to dismiss, without a global listener that would
          outlive the surface that opened this. */}
      <div className="fixed inset-0 z-40" onMouseDown={onDismiss} />
      <div
        role="menu"
        aria-label={ariaLabel}
        data-testid={testId}
        data-detached={detached ? "true" : undefined}
        style={menuStyle}
        className={cn(
          // Scrolls rather than growing past the window: the list is every CLI
          // the backend registered, and that is six entries on a machine with
          // the usual set installed — more than fits under a button near the
          // top of a laptop screen.
          "z-50 max-h-[70vh] w-[17rem] overflow-y-auto rounded-lg border border-border bg-card p-1 shadow-xl scrollbar-jarvis",
          "animate-in fade-in-0 zoom-in-95 duration-150",
          // The caller's anchoring classes describe a box INSIDE its own
          // element ("right-2 top-full"), which is the very thing a detached
          // menu is escaping. Its coordinates come from the measurement above.
          detached ? "fixed" : cn("absolute", className),
        )}
        onMouseDown={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === "Escape") onDismiss();
        }}
      >
        {/* The question, at label weight rather than as quiet muted ink: it is
            11px in all-caps, which is the hardest text in the menu to read, and
            it is the one line that says what the click will do. The rule under
            it separates the question from the answers, so the first entry does
            not read as part of the heading. */}
        <p className="mb-1 border-b border-border/60 px-2 pb-1.5 pt-1 text-[11px] font-semibold uppercase tracking-wider text-foreground/70">
          {title}
        </p>
        {agents.map((agent) => (
          <button
            key={agent.name}
            type="button"
            role="menuitem"
            autoFocus={agent === first}
            disabled={!agent.installed}
            data-testid={itemTestId(agent.name)}
            onClick={(e) => {
              e.stopPropagation();
              onPick(agent.name);
            }}
            /* An entry that is not installed is OFF, not unreadable. It used to
               drop to 40 % opacity, which on paper put its name and its own
               "not installed" reason below the contrast floor — the absence
               stopped explaining itself, which is the whole reason the entry is
               listed. State now reads from the ink COLOUR (muted rather than
               foreground) with only a slight dim on top, so it is plainly
               unavailable and still says why. */
            className="flex w-full items-start justify-between gap-2 rounded-md px-2 py-1.5 text-left text-sm transition-colors hover:bg-primary/10 disabled:cursor-not-allowed disabled:text-muted-foreground disabled:opacity-80 disabled:hover:bg-transparent"
          >
            {/* The mark, at list weight rather than as a framed tile: this is a
                menu of choices, and forty boxes down a column read as a grid
                instead of a list. It earns its place because the entries are no
                longer a fixed set of names a user can learn — one of them may
                be a CLI they added and named themselves. */}
            <AgentMark
              agent={agent.name}
              label={agent.displayName}
              variant="plain"
              size="sm"
              logoUrl={agent.logoUrl}
              className="mt-0.5"
            />
            <span className="min-w-0 flex-1">
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
              <span className="shrink-0 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                {agent.kind === "shell" ? "no shell here" : "not installed"}
              </span>
            )}
          </button>
        ))}
      </div>
    </>
  );

  // Rendered where it was measured against — the window — so the pane that
  // opened it cannot clip it. Before the first measurement the menu is still
  // mounted (its entries keep their focus order and are reachable by keyboard);
  // it simply has no coordinates for one frame.
  return detached ? createPortal(menu, document.body) : menu;
}
