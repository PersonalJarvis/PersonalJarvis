import { useCallback, useEffect, useState } from "react";

/**
 * Master TTS output volume (how loudly Jarvis speaks) from
 * GET /api/settings/tts-volume. The value is a 0.0–1.0 amplitude gain
 * (1.0 = full); the UI renders it as a 0–100% slider. Mirrors useSilenceWindow.
 */
export interface TtsVolumeConfig {
  volume: number;
  default: number;
  min: number;
  max: number;
}

export interface TtsVolumeSaveResult {
  ok: boolean;
  volume: number;
  default: number;
  persisted: boolean;
  applied_live: boolean;
  restart_required: boolean;
}

/** Loads /api/settings/tts-volume and exposes setVolume(). */
export function useTtsVolume() {
  const [config, setConfig] = useState<TtsVolumeConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refetch = useCallback(async () => {
    setError(null);
    try {
      const res = await fetch("/api/settings/tts-volume");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: TtsVolumeConfig = await res.json();
      setConfig(data);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refetch();
  }, [refetch]);

  const setVolume = useCallback(
    async (volume: number): Promise<TtsVolumeSaveResult> => {
      const res = await fetch("/api/settings/tts-volume", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ volume, persist: true }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail ?? `HTTP ${res.status}`);
      const result = body as TtsVolumeSaveResult;
      setConfig((prev) => (prev ? { ...prev, volume: result.volume } : prev));
      return result;
    },
    [],
  );

  return { config, loading, error, refetch, setVolume };
}
