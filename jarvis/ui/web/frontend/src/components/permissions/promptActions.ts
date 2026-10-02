/**
 * Which buttons a permission card offers for an episode. Pure, so the reason
 * table of the design is one readable function with its own test.
 *
 * | reason                   | buttons                                                    |
 * |--------------------------|------------------------------------------------------------|
 * | not_determined           | Continue (macOS asks next) | Not now                       |
 * | denied / needs_settings  | Open System Settings, Check again, Not now (+ Reset)       |
 * | restart_hint             | Quit and reopen | Not now                                  |
 * | restricted / unavailable | explanation only (Not now closes it)                       |
 * | any, outside_app         | "Allow for the app that started Jarvis" replaces Continue  |
 *
 * "Reset and ask again" appears only once the person came back from System
 * Settings and the permission still reads off: that is the stranded-grant case
 * (an entry that belongs to an older build), and the backend says whether a
 * reset is possible (`can_reset`).
 */
import type { PromptEpisode } from "@/lib/permissionPrompts";

/**
 * Classes that let an action button wrap inside its card instead of running out
 * of it. The base button never wraps (`whitespace-nowrap`), which is right for a
 * toolbar and wrong for "Allow for the app that started Personal Jarvis" in a
 * 320 px card (or its German and Spanish sentences).
 */
export const WRAPPING_ACTION_BUTTON = "h-auto min-h-8 max-w-full whitespace-normal py-1.5 text-left";

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
      if (episode.outside_app && episode.can_prompt) actions.push("allow_outside");
      else if (episode.can_prompt) actions.push("continue");
      if (episode.can_open_settings) actions.push("open_settings", "check_again");
      if (context.returnedFromSettings && context.stillOff && context.canReset) {
        actions.push("reset");
      }
      break;
  }
  actions.push("not_now");
  return actions;
}
