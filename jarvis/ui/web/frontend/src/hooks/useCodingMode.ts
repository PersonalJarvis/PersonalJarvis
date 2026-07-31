import { useEffect } from "react";

import { useEventStore } from "@/store/events";
import { fetchWorkspaces } from "@/lib/agenticIdeApi";

/**
 * Keeps the app-wide "coding mode" indicator in sync with the backend.
 *
 * Why this is shell state and not workspace state: focused coding mode changes
 * how Jarvis answers EVERY turn — on the Chats screen, in Settings, by voice
 * from across the room. The one place it was visible until now was the toggle
 * inside the workspace view, i.e. the single screen where the user can already
 * see the terminals and does not need telling. Everywhere else the assistant
 * silently behaved differently with nothing on screen saying so.
 *
 * Four update paths, the same defense-in-depth shape as useBrainStatus:
 *   1. Mount fetch — the initial value after a reload or an app restart.
 *   2. WS event AgenticIdeCodingModeChanged, relayed by useWebSocket.ts as
 *      `jarvis:agentic-ide-mode`. Covers a switch made by voice or the CLI, and
 *      the workspace transitions that change the mode as a side effect.
 *   3. `jarvis:agentic-ide-changed` (panes added/closed) — a workspace that
 *      gains or loses panes can also have appeared or gone entirely.
 *   4. Section navigation — the cheap catch-all. Anything a local view did to
 *      the mode is reconciled the moment the user leaves it, so a missed event
 *      can never outlive the screen it happened on.
 *
 * Deliberately NOT polled. The endpoint is in-memory but a global badge is
 * exactly the kind of surface that quietly turns into a per-second request for
 * the entire session; every path above is edge-triggered instead.
 *
 * `GET /workspaces` is the light endpoint on purpose: it returns tab cards, so
 * it costs nothing per pane. `GET /state` would recompute every terminal's recap
 * to render one chip.
 */
export function useCodingMode(): void {
  const setCodingMode = useEventStore((s) => s.setCodingMode);
  const activeSection = useEventStore((s) => s.activeSection);

  useEffect(() => {
    let cancelled = false;

    const read = () =>
      fetchWorkspaces()
        .then((res) => {
          if (cancelled) return;
          const front = res.workspaces.find((w) => w.active);
          setCodingMode({
            active: Boolean(front?.focus_mode),
            hasWorkspace: res.workspaces.length > 0,
            workspace: front?.focus_mode ? front.name : "",
          });
        })
        .catch(() => {
          // Backend not up yet, or a transient failure. Leave the last known
          // value: blanking the badge on a hiccup would report "not in coding
          // mode", which is a claim about the assistant, not about the fetch.
        });

    void read();

    const onChanged = () => void read();
    window.addEventListener("jarvis:agentic-ide-mode", onChanged);
    window.addEventListener("jarvis:agentic-ide-changed", onChanged);

    return () => {
      cancelled = true;
      window.removeEventListener("jarvis:agentic-ide-mode", onChanged);
      window.removeEventListener("jarvis:agentic-ide-changed", onChanged);
    };
    // activeSection is a dependency, not a listener: re-running the effect on
    // navigation IS path 4.
  }, [setCodingMode, activeSection]);
}
