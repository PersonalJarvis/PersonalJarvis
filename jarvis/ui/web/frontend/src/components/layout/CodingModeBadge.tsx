import { Brain } from "lucide-react";
import { clsx } from "clsx";

import { useEventStore } from "@/store/events";
import { useT } from "@/i18n";

/**
 * App-wide indicator: is Jarvis an Agentic IDE right now?
 *
 * Focused coding mode is a mode of the ASSISTANT, not of one screen. While it
 * is on, Jarvis answers inside the open workspace — it reasons about that
 * repository and drives the coding agents in it — and that is true on the Chats
 * screen, in Settings, and for anything said out loud. Until now the only place
 * that said so was the toggle inside the workspace view: the one screen where
 * the user can already see the terminals. Everywhere else the assistant behaved
 * differently with nothing on screen to explain why.
 *
 * Rendered in the TopBar, which is the only element mounted above every view
 * (see the note in TopBar.tsx on why the shell and not the per-view header).
 *
 * Three states, and the distinction between the last two is the point:
 *   - no workspace open  → nothing rendered. Silence for everyone who never
 *     opens the IDE; a permanent "coding mode off" chip would be pure noise.
 *   - workspace open, mode OFF → a quiet chip. Worth showing, because "there
 *     are agents running but Jarvis is NOT in their context" is exactly the
 *     state a user misreads.
 *   - mode ON → the loud chip, matching the toggle it mirrors.
 *
 * Clicking opens the Agentic IDE rather than toggling. Turning the mode off is
 * a change to how the assistant answers everywhere, and doing that from a
 * one-click chip on an unrelated screen — where the workspace it applies to is
 * not even visible — is a footgun. The real switch stays next to the panes it
 * affects; this takes the user there.
 */
export function CodingModeBadge({ className }: { className?: string }) {
  const t = useT();
  const mode = useEventStore((s) => s.codingMode);
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const activeSection = useEventStore((s) => s.activeSection);

  if (!mode.hasWorkspace) return null;

  const label = mode.active
    ? t("topbar.coding_mode_on")
    : t("topbar.coding_mode_off");
  const title = mode.active
    ? (mode.workspace
        ? t("topbar.coding_mode_on_hint_named").replace("{0}", mode.workspace)
        : t("topbar.coding_mode_on_hint"))
    : t("topbar.coding_mode_off_hint");

  return (
    <button
      type="button"
      onClick={() => setActiveSection("agentic-ide")}
      title={title}
      aria-label={title}
      data-testid="coding-mode-badge"
      // Announce state changes to a screen reader: the mode can flip from
      // another surface entirely, which is silent for anyone not watching.
      aria-live="polite"
      // Not aria-pressed: this button navigates, it does not toggle the mode.
      // Claiming a pressed state for something it cannot change would be a lie
      // to exactly the users who depend on it most.
      disabled={activeSection === "agentic-ide" || activeSection === "agentic-ide-classic"}
      // `clsx`, not `cn`: tailwind-merge reads `text-body` as a colour and
      // would drop it in favour of the state colour below, leaving the chip at
      // the inherited 16 px.
      className={clsx(
        "inline-flex h-8 items-center gap-2 rounded-md px-3",
        "text-body font-medium transition-colors disabled:cursor-default",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-border-strong",
        // ON is a live state, so it says so in the one hue that means "this is
        // running" — a green dot beside plain ink, rather than a whole chip
        // recoloured in the accent, which put the loudest mark in the window
        // on a status. OFF keeps the quiet chrome shape.
        mode.active
          ? "bg-card text-foreground hover:bg-secondary"
          : "text-muted-foreground hover:bg-secondary hover:text-foreground",
        className,
      )}
    >
      {mode.active ? (
        <span aria-hidden className="h-2 w-2 shrink-0 rounded-full bg-success" />
      ) : (
        <Brain aria-hidden className="h-4 w-4 shrink-0" />
      )}
      {label}
    </button>
  );
}
