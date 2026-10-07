import { useState } from "react";
import { RefreshCw } from "lucide-react";
import {
  useAudioDevices,
  type AudioDeviceEntry,
} from "@/hooks/useAudioDevices";
import { useEventStore } from "@/store/events";
import { useT } from "@/i18n";
import { SettingsRow, SettingsSelect } from "@/views/settings/SettingsLayout";

/**
 * "Audio devices" card inside the Settings view: one dropdown for the OUTPUT
 * device (where Jarvis's voice plays) and one for the MICROPHONE (what Jarvis
 * listens with). The first option is always "Automatic (recommended)" — the
 * auto-headset resolver; concrete entries are physical devices by display
 * name (stable across reboots, unlike device indices). A pick saves
 * immediately and applies live when safe; a device connected after app start
 * is activated on the next start. The picker rescans on OS device-change
 * events, while the button remains a manual fallback. On a host without audio
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

  const rescan = (
    <button
      type="button"
      onClick={() => void refetch()}
      disabled={loading}
      title={t("settings_view.audio_devices.rescan")}
      aria-label={t("settings_view.audio_devices.rescan")}
      className="flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
    >
      <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
    </button>
  );

  if (config && !config.available) {
    return (
      <SettingsRow
        id="settings-audio-devices"
        title={t("settings_view.audio_devices.title")}
        description={t("settings_view.audio_devices.none_found")}
        control={rescan}
      >
        {error && <p className="text-sm text-destructive">{error}</p>}
      </SettingsRow>
    );
  }

  // Both rows sit inside one boundary, so they draw their own hairline.
  return (
    <div className="divide-y divide-border">
      <DevicePicker
        id="settings-audio-devices"
        label={t("settings_view.audio_devices.output_label")}
        description={t("settings_view.audio_devices.description")}
        testId="audio-output-select"
        devices={config?.outputs ?? []}
        selected={config?.selected_output ?? autoValue}
        autoValue={autoValue}
        autoLabel={t("settings_view.audio_devices.auto_option")}
        defaultSuffix={t("settings_view.audio_devices.default_suffix")}
        disabled={loading || saving !== null}
        onSelect={(device) => void onSelect("output", device)}
        trailing={rescan}
        error={error}
      />
      <DevicePicker
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
    </div>
  );
}

function DevicePicker({
  id,
  label,
  description,
  testId,
  devices,
  selected,
  autoValue,
  autoLabel,
  defaultSuffix,
  disabled,
  onSelect,
  trailing,
  error,
}: {
  id?: string;
  label: string;
  description?: string;
  testId: string;
  devices: AudioDeviceEntry[];
  selected: string;
  autoValue: string;
  autoLabel: string;
  defaultSuffix: string;
  disabled: boolean;
  onSelect: (device: string) => void;
  trailing?: React.ReactNode;
  error?: string | null;
}) {
  // A persisted name whose device is currently unplugged still shows as the
  // selected value (an extra option) so the UI never lies about the config;
  // the backend resolver falls back to automatic until it reappears.
  const known = devices.some((d) => d.name === selected);
  const showOrphan = selected !== autoValue && !known;

  return (
    <SettingsRow
      id={id}
      title={label}
      description={description}
      control={
        <>
          <SettingsSelect
            testId={testId}
            value={selected}
            onValueChange={onSelect}
            ariaLabel={label}
            disabled={disabled}
            className="max-w-[18rem]"
            options={[
              { value: autoValue, label: autoLabel },
              ...devices.map((device) => ({
                value: device.name,
                label: device.is_default
                  ? `${device.name} ${defaultSuffix}`
                  : device.name,
              })),
              ...(showOrphan ? [{ value: selected, label: selected }] : []),
            ]}
          />
          {trailing}
        </>
      }
    >
      {error && <p className="text-sm text-destructive">{error}</p>}
    </SettingsRow>
  );
}
