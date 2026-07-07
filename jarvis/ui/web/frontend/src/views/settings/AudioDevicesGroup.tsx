import { useState } from "react";
import { Headphones, Mic, RefreshCw, Volume2 } from "lucide-react";
import {
  useAudioDevices,
  type AudioDeviceEntry,
} from "@/hooks/useAudioDevices";
import { useEventStore } from "@/store/events";
import { useT } from "@/i18n";

/**
 * "Audio devices" card inside the Settings view: one dropdown for the OUTPUT
 * device (where Jarvis's voice plays) and one for the MICROPHONE (what Jarvis
 * listens with). The first option is always "Automatic (recommended)" — the
 * auto-headset resolver; concrete entries are physical devices by display
 * name (stable across reboots, unlike device indices). A pick saves
 * immediately and applies live to the running voice pipeline; the rescan
 * button re-enumerates after plugging in a headset. On a host without audio
 * hardware the card degrades to a caption instead of empty dropdowns.
 */
export function AudioDevicesGroup() {
  const t = useT();
  const { config, loading, error, refetch, select } = useAudioDevices();
  const pushToast = useEventStore((s) => s.pushToast);
  const [saving, setSaving] = useState<"output" | "input" | null>(null);

  async function onSelect(kind: "output" | "input", device: string) {
    setSaving(kind);
    try {
      const res = await select(kind, device);
      pushToast(
        "success",
        t(
          kind === "output"
            ? "settings_view.audio_devices.output_saved_toast"
            : "settings_view.audio_devices.input_saved_toast",
        ),
      );
      if (res.restart_required) {
        pushToast("warning", t("settings_view.audio_devices.restart_caption"));
      }
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setSaving(null);
    }
  }

  const autoValue = config?.auto_value ?? "auto-headset";

  return (
    <div className="mt-2 rounded-lg border border-border bg-card/60 p-4">
      <div className="flex items-start gap-3">
        <Headphones className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-4">
            <h4 className="font-display text-sm font-semibold">
              {t("settings_view.audio_devices.title")}
            </h4>
            <button
              type="button"
              onClick={() => void refetch()}
              disabled={loading}
              title={t("settings_view.audio_devices.rescan")}
              aria-label={t("settings_view.audio_devices.rescan")}
              className="text-muted-foreground hover:text-foreground disabled:opacity-50"
            >
              <RefreshCw
                className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`}
              />
            </button>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            {t("settings_view.audio_devices.description")}
          </p>

          {error && <p className="mt-3 text-xs text-destructive">{error}</p>}

          {config && !config.available ? (
            <p className="mt-3 text-xs text-muted-foreground">
              {t("settings_view.audio_devices.none_found")}
            </p>
          ) : (
            <>
              <DevicePicker
                icon={<Volume2 className="h-3.5 w-3.5 text-primary" />}
                label={t("settings_view.audio_devices.output_label")}
                testId="audio-output-select"
                devices={config?.outputs ?? []}
                selected={config?.selected_output ?? autoValue}
                autoValue={autoValue}
                autoLabel={t("settings_view.audio_devices.auto_option")}
                defaultSuffix={t("settings_view.audio_devices.default_suffix")}
                disabled={loading || saving !== null}
                onSelect={(device) => void onSelect("output", device)}
              />
              <DevicePicker
                icon={<Mic className="h-3.5 w-3.5 text-primary" />}
                label={t("settings_view.audio_devices.input_label")}
                testId="audio-input-select"
                devices={config?.inputs ?? []}
                selected={config?.selected_input ?? autoValue}
                autoValue={autoValue}
                autoLabel={t("settings_view.audio_devices.auto_option")}
                defaultSuffix={t("settings_view.audio_devices.default_suffix")}
                disabled={loading || saving !== null}
                onSelect={(device) => void onSelect("input", device)}
              />
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function DevicePicker({
  icon,
  label,
  testId,
  devices,
  selected,
  autoValue,
  autoLabel,
  defaultSuffix,
  disabled,
  onSelect,
}: {
  icon: React.ReactNode;
  label: string;
  testId: string;
  devices: AudioDeviceEntry[];
  selected: string;
  autoValue: string;
  autoLabel: string;
  defaultSuffix: string;
  disabled: boolean;
  onSelect: (device: string) => void;
}) {
  // A persisted name whose device is currently unplugged still shows as the
  // selected value (an extra option) so the UI never lies about the config;
  // the backend resolver falls back to automatic until it reappears.
  const known = devices.some((d) => d.name === selected);
  const showOrphan = selected !== autoValue && !known;

  return (
    <>
      <label className="mt-4 flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
        {icon}
        {label}
      </label>
      <select
        data-testid={testId}
        value={selected}
        onChange={(e) => onSelect(e.target.value)}
        disabled={disabled}
        className="mt-1 w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary disabled:opacity-50"
      >
        <option value={autoValue}>{autoLabel}</option>
        {devices.map((d) => (
          <option key={d.name} value={d.name}>
            {d.is_default ? `${d.name} ${defaultSuffix}` : d.name}
          </option>
        ))}
        {showOrphan && <option value={selected}>{selected}</option>}
      </select>
    </>
  );
}
