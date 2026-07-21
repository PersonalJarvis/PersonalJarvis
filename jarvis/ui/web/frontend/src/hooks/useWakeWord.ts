import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Current wake-word configuration as returned by GET /api/settings/wake-word.
 * Mirrors the backend response in jarvis/ui/web/settings_routes.py.
 */
export interface WakeWordConfig {
  phrase: string;
  engine: string;
  custom_model_path: string;
  fuzzy_match_ratio: number;
  // The independent wake-word language pin ("auto" = derive a sensible
  // default; a concrete code pins the acoustic model's language and never
  // follows the app display language).
  language: string;
  engines: string[];
  instant_phrases: string[];
  local_whisper_available: boolean;
  // The activation master switch: true = always-on wake word (needs a local
  // model for the user's word), false = Call shortcut only.
  enabled: boolean;
}

/**
 * Payload for PUT /api/settings/wake-word. Optional fields fall back to the
 * backend defaults when omitted.
 */
export interface WakeWordPayload {
  phrase: string;
  engine: string;
  custom_model_path?: string;
  fuzzy_match_ratio?: number;
  persist?: boolean;
}

/**
 * Result of a successful wake-word save. `resolved_engine` may differ from the
 * requested engine when the chosen phrase forces a fallback — `degraded` flags
 * that case so the UI can warn the user.
 */
export interface WakeWordSaveResult {
  ok: boolean;
  phrase: string;
  engine: string;
  resolved_engine: string;
  degraded: boolean;
  // False when no local model matches the user's word: the wake word is off and
  // the Call shortcut is the activation.
  wake_available: boolean;
  message: string;
  persisted: boolean;
  restart_required: boolean;
}

export interface WakeActivationResult {
  ok: boolean;
  enabled: boolean;
  applied_live: boolean;
  restart_required: boolean;
}

/**
 * Returns true when the wake-word config is fully set up (phrase is non-empty).
 * Used by WakeWordOnboardingGate to decide whether to show the blocking overlay.
 */
export function isConfigured(config: WakeWordConfig | null): boolean {
  return !!config && config.phrase.trim().length > 0;
}

/**
 * Loads /api/settings/wake-word and exposes a saveWakeWord() that PUTs the new
 * config and returns the resolved result. Mirrors the fetch/error/loading shape
 * of useProviders. After a successful save it dispatches the window event
 * 'jarvis:wake-word-changed' (consistent with the existing 'jarvis:*-switched'
 * events) so other components can re-read live state.
 */
export function useWakeWord() {
  const [config, setConfig] = useState<WakeWordConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refetch = useCallback(async () => {
    setError(null);
    try {
      const res = await fetch("/api/settings/wake-word");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: WakeWordConfig = await res.json();
      setConfig(data);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refetch();
    const onChanged = () => void refetch();
    window.addEventListener("jarvis:wake-word-changed", onChanged);
    return () => {
      window.removeEventListener("jarvis:wake-word-changed", onChanged);
    };
  }, [refetch]);

  const saveWakeWord = useCallback(
    async (payload: WakeWordPayload): Promise<WakeWordSaveResult> => {
      const res = await fetch("/api/settings/wake-word", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ persist: true, ...payload }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(body.detail ?? `HTTP ${res.status}`);
      }
      const result = body as WakeWordSaveResult;
      window.dispatchEvent(new CustomEvent("jarvis:wake-word-changed"));
      // The assistant's display name is derived from the wake phrase, so bylines
      // that read from /api/settings/assistant-name must re-seed on every save.
      window.dispatchEvent(new CustomEvent("jarvis:assistant-name-changed"));
      return result;
    },
    [],
  );

  // Pin the wake-word language (PUT /api/settings/wake-language). Applies
  // immediately: the backend live-swaps the wake plan and provisions the
  // matching model in the background when it is not on disk yet. Deliberately
  // its OWN setting — never writes the app display language or the general
  // STT recognition language.
  const setWakeLanguage = useCallback(async (language: string): Promise<void> => {
    const res = await fetch("/api/settings/wake-language", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ language }),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(body.detail ?? `HTTP ${res.status}`);
    }
    window.dispatchEvent(new CustomEvent("jarvis:wake-word-changed"));
  }, []);

  // Turn the always-on wake word ON/OFF (the activation master switch). The
  // backend applies it to a running voice pipeline and only requests a restart
  // when no live desktop voice pipeline exists.
  const setWakeActivation = useCallback(
    async (enabled: boolean): Promise<WakeActivationResult> => {
      const res = await fetch("/api/settings/wake-word/activation", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(body.detail ?? `HTTP ${res.status}`);
      }
      window.dispatchEvent(new CustomEvent("jarvis:wake-word-changed"));
      return body as WakeActivationResult;
    },
    [],
  );

  return { config, loading, error, refetch, saveWakeWord, setWakeLanguage, setWakeActivation };
}

/**
 * Progress of the in-app "local speech pack" (faster-whisper) install that
 * unlocks ANY wake phrase. Mirrors the backend state machine in
 * jarvis/ui/web/settings_routes.py (enable-local-speech).
 */
export interface LocalSpeechStatus {
  state: "idle" | "running" | "done" | "error";
  message: string;
  available: boolean;
}

/**
 * Drives POST /api/settings/wake-word/enable-local-speech and polls its status
 * endpoint until the install finishes. Calls `onInstalled` once the pack is
 * present so the caller can refetch the wake config. The install runs in the
 * backend; this only starts it and reflects progress.
 */
export function useLocalSpeechInstall(onInstalled?: () => void) {
  const [status, setStatus] = useState<LocalSpeechStatus>({
    state: "idle",
    message: "",
    available: true,
  });
  const pollRef = useRef<number | null>(null);
  const onInstalledRef = useRef(onInstalled);
  onInstalledRef.current = onInstalled;

  const stopPoll = useCallback(() => {
    if (pollRef.current !== null) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const poll = useCallback(async () => {
    try {
      const res = await fetch(
        "/api/settings/wake-word/enable-local-speech/status",
      );
      const data = (await res.json()) as LocalSpeechStatus;
      setStatus(data);
      if (data.state === "done" || data.state === "error") {
        stopPoll();
        if (data.state === "done" && data.available) onInstalledRef.current?.();
      }
    } catch {
      // Transient network blip during a long install — keep polling.
    }
  }, [stopPoll]);

  const install = useCallback(async () => {
    setStatus({ state: "running", message: "", available: false });
    try {
      const res = await fetch("/api/settings/wake-word/enable-local-speech", {
        method: "POST",
      });
      const data = (await res.json()) as LocalSpeechStatus & { already?: boolean };
      if (data.state === "done" && data.available) {
        setStatus({ state: "done", message: data.message ?? "", available: true });
        onInstalledRef.current?.();
        return;
      }
      stopPoll();
      pollRef.current = window.setInterval(() => void poll(), 2000);
    } catch (e) {
      setStatus({ state: "error", message: (e as Error).message, available: false });
    }
  }, [poll, stopPoll]);

  // Clean up the interval if the panel unmounts mid-install.
  useEffect(() => () => stopPoll(), [stopPoll]);

  return { status, install };
}
