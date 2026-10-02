/**
 * Which buttons a permission card offers for an episode. Pure, so the reason
 * table of the design is one readable function with its own test.
 *
 * | reason                   | buttons                                                    |
 * |--------------------------|------------------------------------------------------------|
 * | not_determined           | Continue (macOS asks next) | Not now                       |
 * | denied / needs_settings  | Open System Settings | Not now                             |
 * | restart_hint             | Quit and reopen | Not now                                  |
 * | restricted / unavailable | explanation only (Not now closes it)                       |
 * | any, outside_app         | "Allow for the app that started Jarvis" replaces Continue  |
 *
 * One primary action and one quiet "Not now": the backend watcher and the focus
 * refetch notice a grant by themselves, so there is nothing to press to "check".
 * "Check again" appears only once the person came back from System Settings and
 * the permission still reads off. Then (and only then) "Reset and ask again" can
 * follow: that is the stranded-grant case (an entry that belongs to an older
 * build), and the backend says whether a reset is possible (`can_reset`). For
 * Screen Recording and Input Monitoring, which macOS may apply only after a
 * restart, "Quit and reopen" comes before the reset.
 */
import type { PromptEpisode } from "@/lib/permissionPrompts";

/**
 * The reasons whose way forward is a switch in System Settings (denied, or not
 * allowed yet). Only these cards carry the quiet path line and the "See all
 * permissions" link: a card that asks, restarts or merely explains has no use for them.
 */
export function isSettingsReason(reason: string): boolean {
  return reason === "denied" || reason === "needs_settings";
}

/**
 * Classes that let an action button wrap inside its card instead of running out
 * of it. The base button never wraps (`whitespace-nowrap`), which is right for a
 * toolbar and wrong for "Allow for the app that started Personal Jarvis" in a
 * 320 px card (or its German and Spanish sentences).
 */
export const WRAPPING_ACTION_BUTTON = "h-auto min-h-8 max-w-full whitespace-normal py-1.5 text-left";

/**
 * The quiet "Not now": a text link in muted ink with no left padding, so when a long
 * primary button (German, Spanish) pushes it onto its own line it still lines up with
 * the text above instead of floating indented.
 */
export const QUIET_ACTION_BUTTON = "px-0 text-muted-foreground hover:text-foreground";

export type PromptAction =
  | "continue"
  | "allow_outside"
  | "open_settings"
  | "check_again"
  | "reset"
  | "restart"
  | "not_now";

export interface PromptActionContext {
  /** The person opened System Settings from this card and then came back. */
  returnedFromSettings: boolean;
  /** The last read of the missing permission(s) still says off. */
  stillOff: boolean;
  /** The backend allows "Ask again" (a tccutil reset) for the missing permission. */
  canReset: boolean;
  /**
   * The missing permission is one macOS may apply only to a NEW process (Screen
   * Recording, Input Monitoring; community-observed, UNVERIFIED). A reset would
   * throw a correct grant away, so "Quit and reopen" comes before it.
   */
  restartMayHelp?: boolean;
}

export function promptActions(
  episode: Pick<PromptEpisode, "reason" | "can_prompt" | "can_open_settings" | "outside_app">,
  context: PromptActionContext,
): PromptAction[] {
  const actions: PromptAction[] = [];
  switch (episode.reason) {
    case "restricted":
    case "unavailable":
      break;
    case "restart_hint":
      actions.push("restart");
      break;
    case "not_determined":
      if (episode.outside_app && episode.can_prompt) actions.push("allow_outside");
      else if (episode.can_prompt) actions.push("continue");
      else if (episode.can_open_settings) actions.push("open_settings");
      break;
    case "denied":
    case "needs_settings":
      // ONE primary: a confirmation or a request when one is still possible,
      // otherwise the way to the Settings pane.
      if (episode.outside_app && episode.can_prompt) actions.push("allow_outside");
      else if (episode.can_prompt) actions.push("continue");
      else if (episode.can_open_settings) actions.push("open_settings");
      if (context.returnedFromSettings && context.stillOff) {
        // Back from Settings and it still reads off: now a re-read, a restart or a reset can help.
        if (episode.can_open_settings && !actions.includes("open_settings")) actions.push("open_settings");
        actions.push("check_again");
        if (context.restartMayHelp) actions.push("restart");
        if (context.canReset) actions.push("reset");
      }
      break;
  }
  actions.push("not_now");
  return actions;
}
