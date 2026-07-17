import { useEffect } from "react";
import { useEventStore } from "@/store/events";
import {
  NEUTRAL_ASSISTANT_NAME,
  writeCachedAssistantName,
} from "@/lib/assistantNameCache";

/**
 * Retry schedule for the boot window. The seed fetch races backend startup
 * under autostart (the AuthGate deliberately lets the app mount even when the
 * backend is still unreachable), so a single fetch can fail and the neutral
 * "Assistant" fallback would stick on every surface until a full page reload.
 * Backoff caps at 15s and keeps going until the first success — the request
 * is a tiny local GET, so a quiet steady retry is cheaper than a stuck brand.
 */
const RETRY_DELAYS_MS = [1_000, 2_000, 4_000, 8_000, 15_000];

/**
 * Seed the resolved assistant name into the global store on mount.
 *
 * The assistant's display name (header wordmark + every assistant byline) is
 * derived solely from the wake phrase configured in `[trigger.wake_word].phrase`
 * (via `deriveAssistantName` on the backend). This hook reads the current value
 * on mount via GET /api/settings/assistant-name (mirroring useVoiceStatus /
 * useBrainStatus) and writes `resolved` into the store.
 *
 * Robustness — the fetch retries with capped backoff until it succeeds once:
 * under autostart the backend may still be binding/warming when the app mounts
 * (it can even answer 503 while `app.state.config` is unset), and a one-shot
 * fetch left the neutral fallback stuck until a reload (the "assistant
 * everywhere after PC reboot" bug). `useWakeWord.saveWakeWord` dispatches
 * `jarvis:assistant-name-changed` after a successful wake-word save, and
 * `useWebSocket` dispatches it on every WS welcome, so we re-fetch on that
 * event to keep every byline live without a reload.
 */
export function useAssistantNameSeed(): void {
  const setAssistantName = useEventStore((s) => s.setAssistantName);

  useEffect(() => {
    let cancelled = false;
    let attempt = 0;
    let retryTimer: number | undefined;
    const ctrl = new AbortController();

    const scheduleRetry = () => {
      if (cancelled) return;
      const delay =
        RETRY_DELAYS_MS[Math.min(attempt, RETRY_DELAYS_MS.length - 1)];
      attempt += 1;
      window.clearTimeout(retryTimer);
      retryTimer = window.setTimeout(seed, delay);
    };

    const seed = () => {
      void fetch("/api/settings/assistant-name", { signal: ctrl.signal })
        .then((r) => (r.ok ? r.json() : null))
        .then((data) => {
          if (cancelled) return;
          if (!data) {
            // Non-OK response (503 while the backend warms, auth hiccup) —
            // try again shortly instead of freezing the neutral fallback.
            scheduleRetry();
            return;
          }
          const resolved =
            typeof data.resolved === "string" ? data.resolved.trim() : "";
          if (resolved) {
            attempt = 0;
            setAssistantName(resolved);
            // Mirror into localStorage so the next boot can paint this name
            // instantly (store seed + index.html splash) with no fetch wait.
            // The neutral fallback is never cached: an empty cache already
            // yields it, and persisting it would poison the next boots with
            // "Assistant" if this response was a warmup artifact.
            if (resolved !== NEUTRAL_ASSISTANT_NAME) {
              writeCachedAssistantName(resolved);
            }
          }
        })
        .catch(() => {
          // Network/timeout — backend not up yet (autostart race) or headless.
          // Keep the store value and retry; a later rename event also re-seeds.
          if (!ctrl.signal.aborted) scheduleRetry();
        });
    };

    seed();
    const onChanged = () => {
      attempt = 0;
      seed();
    };
    window.addEventListener("jarvis:assistant-name-changed", onChanged);

    return () => {
      cancelled = true;
      window.clearTimeout(retryTimer);
      ctrl.abort();
      window.removeEventListener("jarvis:assistant-name-changed", onChanged);
    };
  }, [setAssistantName]);
}
