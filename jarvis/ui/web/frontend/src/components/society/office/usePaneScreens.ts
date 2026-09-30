/**
 * Live terminal screens for the coding agents who sit at their desk right now.
 *
 * One batched request per tick for the seated targets only, jittered so
 * windows and panes never fire in lockstep (AP-33). Never gated on
 * `document.hidden`: the desktop WebView reports hidden while on screen.
 * A failed poll keeps the last good screens; the first failure of a streak is
 * logged, the rest stay quiet until a poll succeeds again.
 */
import { useEffect, useRef, useState } from "react";
import { fetchPaneScreens, type PaneScreen } from "@/lib/paneScreensApi";
import { screenChanged } from "./terminalScreen";
import { seatedAtDesk } from "./walkerRegistry";

// The feed is an in-memory read of the pane's screen buffer and unchanged
// screens are never redrawn, so a near-live rate costs next to nothing.
const POLL_MIN_MS = 450;
const POLL_JITTER_MS = 250;
/** Never more panes per request than this; the feed accepts up to 8. */
export const MAX_SCREEN_TARGETS = 6;

export interface PaneScreenTarget { agentId: string; workspaceId: string; key: string }

/** The targets due this tick: seated ones only, at most MAX_SCREEN_TARGETS. Pure. */
export function dueTargets(targets: readonly PaneScreenTarget[], seated: ReadonlySet<string>): PaneScreenTarget[] {
  return targets.filter((t) => seated.has(t.agentId)).slice(0, MAX_SCREEN_TARGETS);
}

/**
 * Fold fetched screens into the map by agent id. Returns `prev` itself when
 * nothing changed, so React skips the render; drops agents no longer targeted. Pure.
 */
export function mergeScreens(
  prev: ReadonlyMap<string, PaneScreen>,
  fetched: readonly PaneScreen[],
  due: readonly PaneScreenTarget[],
  targeted: ReadonlySet<string>,
): ReadonlyMap<string, PaneScreen> {
  const byPane = new Map(due.map((t) => [`${t.workspaceId}:${t.key}`, t.agentId]));
  let next: Map<string, PaneScreen> | null = null;
  for (const id of prev.keys()) {
    if (targeted.has(id)) continue;
    next ??= new Map(prev);
    next.delete(id);
  }
  for (const screen of fetched) {
    const agentId = byPane.get(`${screen.workspace_id}:${screen.key}`);
    if (!agentId || !screenChanged((next ?? prev).get(agentId), screen)) continue;
    next ??= new Map(prev);
    next.set(agentId, screen);
  }
  return next ?? prev;
}

export function usePaneScreens(targets: PaneScreenTarget[], awake: boolean): ReadonlyMap<string, PaneScreen> {
  const [screens, setScreens] = useState<ReadonlyMap<string, PaneScreen>>(new Map());
  const targetsRef = useRef(targets);
  targetsRef.current = targets;
  const active = awake && targets.length > 0;
  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    let failing = false;
    let timer: ReturnType<typeof setTimeout>;
    const schedule = () => { timer = setTimeout(() => void tick(), POLL_MIN_MS + Math.random() * POLL_JITTER_MS); };
    const tick = async () => {
      const due = dueTargets(targetsRef.current, seatedAtDesk);
      if (due.length > 0) {
        try {
          const fetched = await fetchPaneScreens(due.map((t) => ({ workspaceId: t.workspaceId, key: t.key })));
          if (cancelled) return;
          failing = false;
          const targeted = new Set(targetsRef.current.map((t) => t.agentId));
          setScreens((prev) => mergeScreens(prev, fetched, due, targeted));
        } catch (error) {
          if (cancelled) return;
          // The monitors keep their last good frame; one log line per failure streak.
          if (!failing) console.warn("Terminal screen poll failed; keeping the last screens", error);
          failing = true;
        }
      }
      if (!cancelled) schedule();
    };
    timer = setTimeout(() => void tick(), Math.random() * POLL_JITTER_MS);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [active]);
  return screens;
}
