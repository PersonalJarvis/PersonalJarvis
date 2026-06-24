import { useCallback, useEffect, useState } from "react";

/** Global "Sound effects" master switch (ui.sound_effects). Mutes/unmutes all
 * synthesized earcons (wake chime, hang-up tone, boot-ready cue) at once. The
 * spoken voice is unaffected. */
export interface SoundEffectsResult {
  ok: boolean;
  enabled: boolean;
  persisted: boolean;
  applied_live: boolean;
}

export function useSoundEffects() {
  const [enabled, setEnabledState] = useState<boolean | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refetch = useCallback(async () => {
    setError(null);
    try {
      const res = await fetch("/api/settings/sound-effects");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setEnabledState(Boolean(data.enabled));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refetch();
  }, [refetch]);

  const setEnabled = useCallback(
    async (next: boolean): Promise<SoundEffectsResult> => {
      const res = await fetch("/api/settings/sound-effects", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: next }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail ?? `HTTP ${res.status}`);
      setEnabledState(Boolean(body.enabled));
      return body as SoundEffectsResult;
    },
    [],
  );

  return { enabled, loading, error, refetch, setEnabled };
}
