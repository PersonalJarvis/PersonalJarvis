import { useCallback, useEffect, useState } from "react";
import type {
  MusicPlaybackMode,
  MusicService,
  MusicSettings,
} from "@/lib/musicSettings";

/** Preferred music service + where YouTube Music plays (`[music]`). */
export interface MusicSettingsResult {
  ok: boolean;
  preferred_service: MusicService;
  playback: MusicPlaybackMode;
  persisted: boolean;
}

export function useMusicSettings() {
  const [settings, setSettings] = useState<MusicSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refetch = useCallback(async () => {
    setError(null);
    try {
      const res = await fetch("/api/settings/music");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setSettings((await res.json()) as MusicSettings);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refetch();
  }, [refetch]);

  const save = useCallback(
    async (patch: {
      preferred_service?: MusicService;
      playback?: MusicPlaybackMode;
    }): Promise<MusicSettingsResult> => {
      const res = await fetch("/api/settings/music", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail ?? `HTTP ${res.status}`);
      setSettings((prev) =>
        prev
          ? {
              ...prev,
              preferred_service: body.preferred_service,
              playback: body.playback,
            }
          : prev,
      );
      return body as MusicSettingsResult;
    },
    [],
  );

  return { settings, loading, error, refetch, save };
}
