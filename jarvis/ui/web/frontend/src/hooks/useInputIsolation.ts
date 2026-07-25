import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Whether outside input software can type into this app's window.
 *
 * Mirrors ``jarvis/platform/input_isolation.py``. When the desktop app runs
 * elevated, Windows discards synthetic keystrokes and automation queries coming
 * from ordinary user software, so dictation apps, text expanders, clipboard
 * managers, and password-manager auto-type all go dead inside our window while
 * still working everywhere else — and nothing reports an error.
 */
export type InputIsolationReason = "none" | "elevated" | "root" | "unknown";

export type InputIsolationReport = {
  blocked: boolean;
  reason: InputIsolationReason;
  platform: string;
  summary: string;
  remedy: string;
  can_restart_unelevated: boolean;
};

/** Backoff for the boot race: the window mounts before the API answers. */
const RETRY_DELAYS_MS = [400, 1200, 3000];

export function useInputIsolation() {
  const [report, setReport] = useState<InputIsolationReport | null>(null);
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);

  const load = useCallback(async (): Promise<boolean> => {
    try {
      const response = await fetch("/api/settings/input-isolation");
      if (!response.ok) return false;
      const body = (await response.json()) as InputIsolationReport;
      setReport(body);
      return true;
    } catch {
      return false;
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    const scheduled = timers.current;

    // A single mount-time fetch is a known trap here: the frontend routinely
    // mounts while the backend is still coming up, and a one-shot 503 would
    // leave the user with no warning at all on exactly the machine that has the
    // problem. Retry a few times, then stop — this is a diagnostic, not a poll.
    void (async () => {
      if (await load()) return;
      RETRY_DELAYS_MS.forEach((delay) => {
        scheduled.push(
          setTimeout(() => {
            if (!cancelled) void load();
          }, delay),
        );
      });
    })();

    return () => {
      cancelled = true;
      scheduled.forEach(clearTimeout);
      scheduled.length = 0;
    };
  }, [load]);

  return { report, refetch: load };
}
