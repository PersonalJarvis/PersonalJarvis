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
 * absence is visible and explains itself rather than silently not being there —
 * and where an install command exists it carries an Install button, so the
 * absence is fixable from the menu instead of only from the CLIs page
 * (maintainer, 2026-08-23). The install runs in a real terminal the user can
 * read, and "installed" is a fresh probe rather than an exit code; see
 * `AgentInstallDialog` for why both of those matter.
 */
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

import type { CuratedModel, PermissionModeOption } from "@/lib/agentChatApi";
import { cn } from "@/lib/utils";
import { AgentInstallDialog } from "./AgentInstallDialog";
import { AgentMark } from "./AgentMark";

/**
 * What a NEW pane of one CLI may be opened on — its models, its effort ladder
 * and the permission stances it can be launched into.
 *
 * The backend's answer (`jarvis/workspace/launch_picks.py`), carried in the
 * agent chat's own catalog vocabulary so the IDE's chat composer can draw
 * these with the picker it already has. Absent for an entry that takes no
 * picks, and for a backend older than the field — both of which mean the same
 * thing to a picker: nothing to choose, so nothing is shown.
 */
export interface AgentLaunchPicks {
  models: CuratedModel[];
  /** "" = the CLI's own default. */
  defaultModel: string;
  /** Ascending; empty when this CLI takes no effort pick. */
  effortLevels: string[];
  defaultEffort: string;
  permissionModes: PermissionModeOption[];
  /** "" = whatever stance the CLI itself opens in. */
  defaultPermissionMode: string;
}

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
  /**
   * The command that would install this entry, when one exists.
   *
   * Present is what turns the row's dead "NOT INSTALLED" label into an Install
   * button, so the menu offers the fix where the user meets the problem. Absent
   * for the plain terminal (a host without a shell is not something an install
   * fixes) and for a user-added CLI, whose command is theirs to run.
   */
  installCommand?: string;
  /**
   * False for an entry that cannot be typed into (its interface is elsewhere).
   * A menu that only OPENS a pane ignores this; a surface that opens a CHAT
   * leaves such an entry out, because there would be nowhere to say anything.
   */
  acceptsPrompts?: boolean;
  /** What a new pane of this CLI may run on; absent = nothing to pick. */
  picks?: AgentLaunchPicks;
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
/**
 * The least room below the anchor still worth dropping into.
 *
 * Under the button is where a menu belongs and where the eye already is, so
 * flipping it above is a last resort rather than the winner of a contest
 * between two numbers. The old `below >= above` rule flipped a pane sitting
 * anywhere past the middle of a tall window — with 640px of clear space under
 * the header, more than the whole list needs, the menu still went up.
 */
const MENU_ROOM_PX = 220;

/** One of `top` and `bottom` is set: the menu hangs below the anchor, or above it. */
type FixedPlacement = { left: number; maxHeight: number; top?: number; bottom?: number };

/**
 * Where a detached menu hangs, measured from the element it belongs to.
 *
 * Right-aligned with the anchor, because that is where the buttons that open
 * it sit, and flipped above it only when the room below cannot hold a usable
 * list. Both numbers are clamped into the window: a pane in the last column
 * would otherwise put half the menu past the right edge, where it is
 * unreachable.
 *
 * The upward flip anchors the menu's BOTTOM edge, never its top. A top
 * computed as "anchor minus the available room" places a short menu at the
 * start of that room — which is the window's top edge, half a screen away
 * from the button that opened it (maintainer report 2026-08-24). The space
 * available and the space used are different numbers; only `bottom` keeps the
 * menu against the anchor whatever its height turns out to be.
 */
function placeMenu(rect: DOMRect, viewport: { width: number; height: number }): FixedPlacement {
  const below = viewport.height - rect.bottom - MENU_GAP_PX - VIEWPORT_MARGIN_PX;
  const above = rect.top - MENU_GAP_PX - VIEWPORT_MARGIN_PX;
  const dropsDown = below >= MENU_ROOM_PX || below >= above;
  const maxHeight = Math.max(96, Math.min(dropsDown ? below : above, viewport.height * 0.7));
  const left = Math.max(
    VIEWPORT_MARGIN_PX,
    Math.min(rect.right - MENU_WIDTH_PX, viewport.width - MENU_WIDTH_PX - VIEWPORT_MARGIN_PX),
  );
  return dropsDown
    ? { left, top: rect.bottom + MENU_GAP_PX, maxHeight }
    : {
        left,
        bottom: Math.max(VIEWPORT_MARGIN_PX, viewport.height - rect.top + MENU_GAP_PX),
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
  onInstalled,
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
  /**
   * Called once an install started from this menu ended, with whether the CLI
   * is now there. The host re-reads its agent list; without it the install
   * still works and the row catches up at the next natural read.
   */
  onInstalled?: (agent: string, installed: boolean) => void;
}) {
  const first = agents.find((a) => a.installed);
  const [placement, setPlacement] = useState<FixedPlacement | null>(null);
  /*
   * The entry whose installer is running, owned HERE rather than handed up to
   * every caller. Three surfaces open this menu (a pane's split buttons, the
   * chat rail, an empty workspace) and none of them has anything to add to an
   * install — threading dialog state through all three would be three copies
   * of the same wiring and three chances for one of them to drift.
   */
  const [installing, setInstalling] = useState<SplitAgentChoice | null>(null);

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
          // Exactly one of the two carries a number; the other stays `auto`, so
          // a menu flipped above the anchor grows upwards from its own bottom
          // edge instead of hanging from a top the list may never reach.
          top: placement.top,
          bottom: placement.bottom,
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
        {agents.map((agent) => {
          /*
           * A missing entry is only a dead end when nothing can be done
           * about it. A CLI that ships an install command gets a button
           * instead of the old label; the plain terminal on a host with no
           * shell keeps the label, because there is nothing to install.
           */
          const installable =
            !agent.installed && agent.kind !== "shell" && Boolean(agent.installCommand);
          return (
            <div
              key={agent.name}
              /* `none` and not `presentation`: this wrapper exists only so
                 the row can hold two menu items side by side, and it must
                 not take a place of its own in the menu's structure. */
              role="none"
              className="flex items-stretch gap-1"
            >
          <button
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
            className="flex min-w-0 flex-1 items-start justify-between gap-2 rounded-md px-2 py-1.5 text-left text-sm transition-colors hover:bg-primary/10 disabled:cursor-not-allowed disabled:text-muted-foreground disabled:opacity-80 disabled:hover:bg-transparent"
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
            {/* The label survives only where it is the whole answer. Where an
                Install button sits beside the row, repeating "not installed"
                in a 272px menu costs the entry's description its line and says
                nothing the button does not. */}
            {!agent.installed && !installable && (
              <span className="shrink-0 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                {agent.kind === "shell" ? "no shell here" : "not installed"}
              </span>
            )}
          </button>
              {installable && (
                <button
                  type="button"
                  role="menuitem"
                  data-testid={`${itemTestId(agent.name)}-install`}
                  title={agent.installCommand}
                  onClick={(e) => {
                    e.stopPropagation();
                    setInstalling(agent);
                  }}
                  /* Outlined rather than filled: it is the row's secondary
                     action, and a column of solid buttons would read as the
                     menu itself being about installing. Its colours are theme
                     tokens, so it follows light and dark. */
                  className="my-0.5 shrink-0 self-center rounded-md border border-border px-2 py-1 text-[11px] font-medium text-foreground transition-colors hover:border-primary/60 hover:bg-primary/10"
                >
                  Install
                </button>
              )}
            </div>
          );
        })}
      </div>
      {/* The installer, over the menu rather than instead of it: closing the
          dialog puts the user back in front of the list they were picking
          from, with the entry they just installed now pickable. */}
      {installing && (
        <AgentInstallDialog
          agent={installing.name}
          displayName={installing.displayName}
          command={installing.installCommand}
          logoUrl={installing.logoUrl}
          onClose={(installed) => {
            const name = installing.name;
            setInstalling(null);
            onInstalled?.(name, installed);
          }}
        />
      )}
    </>
  );

  // Rendered where it was measured against — the window — so the pane that
  // opened it cannot clip it. Before the first measurement the menu is still
  // mounted (its entries keep their focus order and are reachable by keyboard);
  // it simply has no coordinates for one frame.
  return detached ? createPortal(menu, document.body) : menu;
}
