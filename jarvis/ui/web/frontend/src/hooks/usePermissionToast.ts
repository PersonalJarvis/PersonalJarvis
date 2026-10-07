import { useCallback, useEffect, useRef } from "react";

import { useRestartApp } from "@/hooks/useRestartApp";
import { translate } from "@/i18n";
import { bootSettled } from "@/lib/bootStagger";
import { handlePermissionToastEvent, seedPermissionToasts } from "@/lib/permissionToast";

/**
 * The permission toast's entry points for the WebSocket hook: STABLE functions,
 * `onEvent` to call with every bus event (it reads only `PermissionNeeded` and
 * `PermissionResolved`, see `lib/permissionToast.ts`) and `seed` to call once per
 * (re)connect so an episode that opened while no window listened is not lost.
 *
 * It exists because the toast's "Quit and reopen" button is `useRestartApp`,
 * which is React state, while the socket handler is installed once and lives
 * outside React. The latest `restart` is kept in a ref so the handler never goes
 * stale, and the functions returned here never change identity.
 */
export function usePermissionToast(): {
  onEvent: (eventName: string, payload: unknown) => void;
  seed: () => void;
} {
  const { restart } = useRestartApp();
  const restartRef = useRef(restart);
  useEffect(() => {
    restartRef.current = restart;
  }, [restart]);

  const deps = useCallback(
    () => ({
      restart: () => restartRef.current({ failureMessage: translate("permissions.restart_failed") }),
    }),
    [],
  );
  const onEvent = useCallback(
    (eventName: string, payload: unknown) => handlePermissionToastEvent(eventName, payload, deps()),
    [deps],
  );
  const seed = useCallback(() => {
    // Read-only, and behind the boot burst (AP-26): never part of the launch path.
    bootSettled()
      .then(() => seedPermissionToasts(deps()))
      .catch((error) => console.warn("Permission toast seed failed:", error));
  }, [deps]);
  return { onEvent, seed };
}
