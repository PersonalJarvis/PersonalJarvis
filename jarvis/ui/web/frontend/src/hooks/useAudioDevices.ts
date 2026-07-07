import { useCallback, useEffect, useState } from "react";

/**
 * Audio device pickers (Settings): which device Jarvis's voice plays on and
 * which microphone it listens with. GET /api/settings/audio-devices lists one
 * entry per physical device plus the current [audio] selection; PUT persists
 * a device NAME (or the "auto-headset" sentinel for automatic selection) and
 * live-applies it to the running voice pipeline. Mirrors useTtsVolume.
 */
export interface AudioDeviceEntry {
  name: string;
  is_default: boolean;
}

export interface AudioDevicesConfig {
  available: boolean;
  auto_value: string;
  outputs: AudioDeviceEntry[];
  inputs: AudioDeviceEntry[];
  selected_output: string;
  selected_input: string;
}

export interface AudioDeviceSaveResult {
  ok: boolean;
  selected_output: string;
  selected_input: string;
  persisted: boolean;
  applied_live: boolean;
  restart_required: boolean;
}

/** Loads /api/settings/audio-devices and exposes select() + refetch(). */
export function useAudioDevices() {
  const [config, setConfig] = useState<AudioDevicesConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refetch = useCallback(async () => {
    setError(null);
    setLoading(true);
    try {
      const res = await fetch("/api/settings/audio-devices");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: AudioDevicesConfig = await res.json();
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

  const select = useCallback(
    async (
      kind: "output" | "input",
      device: string,
    ): Promise<AudioDeviceSaveResult> => {
      const body =
        kind === "output"
          ? { output_device: device, persist: true }
          : { input_device: device, persist: true };
      const res = await fetch("/api/settings/audio-devices", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const payload = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(payload.detail ?? `HTTP ${res.status}`);
      const result = payload as AudioDeviceSaveResult;
      setConfig((prev) =>
        prev
          ? {
              ...prev,
              selected_output: result.selected_output,
              selected_input: result.selected_input,
            }
          : prev,
      );
      return result;
    },
    [],
  );

  return { config, loading, error, refetch, select };
}
