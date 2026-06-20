/**
 * CliConnectPoller — unsichtbare Hintergrund-Komponente die ueberprueft
 * ob ein laufender CLI-Login (z.B. ``firebase login`` in einem externen
 * Windows-Terminal) erfolgreich war.
 *
 * Workflow:
 * 1. ConnectOAuthButton ruft ``spawn-external`` UND setzt ``cliConnectCoach``.
 * 2. Externes Terminal poppt auf, User loggt sich im Browser ein.
 * 3. Dieser Poller (gemounted in App.tsx, also IMMER aktiv) sieht den
 *    ``cliConnectCoach``-State und pollt alle 3s ``POST /api/clis/{name}/check``.
 * 4. Sobald ``connected: true`` zurueckkommt → Success-Toast, Cache-Invalidate,
 *    Coach-State zuruecksetzen.
 * 5. Timeout nach ``MAX_ATTEMPTS * INTERVAL = 5min`` ohne Erfolg → Warning.
 *
 * Wichtig: Der Poller laeuft ohne UI — er ist ueberall aktiv, egal ob der
 * User in der CLIs-View, Terminal-View oder anderswo ist. Vorher hing der
 * Polling-Loop im CliConnectCoach-Component (TerminalView), was nur lief
 * wenn der User die Terminal-Section angeklickt hatte. Nach dem Switch auf
 * externes Terminal ging der User aber gar nicht mehr in die Terminal-View
 * → Polling lief nie → Status sprang nie auf "verbunden".
 */
import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useEventStore } from "@/store/events";
import { useT } from "@/i18n";

const POLL_INTERVAL_MS = 3_000;
const MAX_ATTEMPTS = 100; // 100 * 3s = 5 Minuten

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
        // Netzwerk-Fehler / Backend kurz weg — naechster Tick versucht's wieder.
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
