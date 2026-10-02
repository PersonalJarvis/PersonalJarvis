/**
 * The one tip about global shortcuts, offered after the first dictation the
 * person started from the UI (the mic button), when macOS has not yet allowed
 * the shortcuts' Input Monitoring.
 *
 * Why there: that is the moment a keyboard shortcut is obviously useful, and
 * the only moment it is not a nag. It is shown AT MOST ONCE: the flag below is
 * set the first time the tip is offered, so a person who ignores it is not
 * asked again after every dictation (the Shortcuts page keeps the permanent
 * explanation). Storage can fail (private window, blocked site data): every
 * access is guarded and a failure means "no tip", never an error.
 */
import { hasEmbeddedDesktopBridge } from "@/lib/embeddedDesktop";
import { isMacClient } from "./permissionPrompts";

export const SHORTCUTS_TIP_SEEN_KEY = "jarvis.shortcuts.tip.seen.v1";

export function shortcutsTipSeen(): boolean {
  try {
    return window.localStorage.getItem(SHORTCUTS_TIP_SEEN_KEY) === "1";
  } catch {
    // Unreadable storage: stay quiet rather than risk showing it every time.
    return true;
  }
}

export function markShortcutsTipSeen(): void {
  try {
    window.localStorage.setItem(SHORTCUTS_TIP_SEEN_KEY, "1");
  } catch {
    // Nothing to persist to; the worst case is one more tip next time.
  }
}

/**
 * Whether to show the tip now. Reads `GET /api/settings/keybinds` once (only on
 * a Mac client in the embedded desktop window, only if never offered before)
 * and claims the "seen" flag before answering true, so two composers that both
 * ask in the same tick still show one tip.
 */
export async function claimShortcutsTip(): Promise<boolean> {
  if (typeof navigator === "undefined" || !isMacClient(navigator.userAgent)) return false;
  if (!hasEmbeddedDesktopBridge() || shortcutsTipSeen()) return false;
  try {
    const res = await fetch("/api/settings/keybinds");
    if (!res.ok) return false;
    const body = (await res.json()) as { shortcuts_status?: { state?: string } };
    if (body.shortcuts_status?.state !== "needs_input_monitoring") return false;
  } catch {
    // Offline or warming: no tip this time, and the flag stays unset.
    return false;
  }
  if (shortcutsTipSeen()) return false;
  markShortcutsTipSeen();
  return true;
}
