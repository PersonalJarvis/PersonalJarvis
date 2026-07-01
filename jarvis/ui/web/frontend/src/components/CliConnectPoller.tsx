/**
 * CliConnectPoller — invisible background component that checks whether
 * a running CLI login (e.g. ``firebase login`` in an external Windows
 * terminal) succeeded.
 *
 * Workflow:
 * 1. ConnectOAuthButton calls ``spawn-external`` AND sets ``cliConnectCoach``.
 * 2. An external terminal pops up, the user logs in via the browser.
 * 3. This poller (mounted in App.tsx, so ALWAYS active) sees the
 *    ``cliConnectCoach`` state and polls ``POST /api/clis/{name}/check`` every 3s.
 * 4. As soon as ``connected: true`` comes back → success toast, cache
 *    invalidation, reset the coach state.
 * 5. Timeout after ``MAX_ATTEMPTS * INTERVAL = 5min`` without success → warning.
 *
 * Important: the poller runs without any UI — it is active everywhere,
 * regardless of whether the user is in the CLIs view, the terminal view,
 * or elsewhere. Previously the polling loop lived in the CliConnectCoach
 * component (TerminalView), which only ran while the user had the
 * terminal section open. After the switch to an external terminal, the
 * user no longer went into the terminal view at all → polling never ran
 * → status never flipped to "connected".
 */
import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useEventStore } from "@/store/events";
import { useT } from "@/i18n";

const POLL_INTERVAL_MS = 3_000;
const MAX_ATTEMPTS = 100; // 100 * 3s = 5 minutes

export function CliConnectPoller() {
  const coach = useEventStore((s) => s.cliConnectCoach);
  const setCoach = useEventStore((s) => s.setCliConnectCoach);
  const pushToast = useEventStore((s) => s.pushToast);
  const qc = useQueryClient();
  const t = useT();
  const attemptsRef = useRef(0);

  useEffect(() => {
    if (!coach) {
      attemptsRef.current = 0;
      return;
    }

    let cancelled = false;
    attemptsRef.current = 0;

    const tick = async () => {
      attemptsRef.current += 1;
      try {
        const r = await fetch(`/api/clis/${coach.cliName}/check`, {
          method: "POST",
        });
        if (!r.ok || cancelled) return;
        const data = (await r.json()) as { connected?: boolean };
        if (cancelled) return;
        if (data.connected) {
          pushToast("success", `${coach.displayName} ${t("cli_connect_poller.connected")}`);
          qc.invalidateQueries({ queryKey: ["clis"] });
          qc.invalidateQueries({ queryKey: ["cli", coach.cliName] });
          setCoach(null);
          return;
        }
        if (attemptsRef.current >= MAX_ATTEMPTS) {
          pushToast(
            "warning",
            `${coach.displayName}: ${t("cli_connect_poller.login_incomplete")}`,
          );
          setCoach(null);
        }
      } catch {
        // Network error / backend briefly down — the next tick will retry.
      }
    };

    void tick();
    const id = window.setInterval(tick, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [coach?.cliName, coach?.displayName, qc, setCoach, pushToast]);

  return null;
}
