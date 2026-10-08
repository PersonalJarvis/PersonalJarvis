import { useEffect, useId, useState } from "react";
import { Button } from "@/components/ui/button";
import { BrandedSelect } from "@/components/ui/select";
import { useT } from "@/i18n";
import type { AppshotRecording, AppshotSettings, AppshotSettingsPatch } from "@/lib/appshotApi";

const fieldClass = "mt-1 h-10 w-full rounded-md border border-border bg-background px-3 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50";

export function AppshotRecordingSettings({ settings, recording, disabled, onSave, onDirty }: {
  settings: AppshotSettings;
  recording: AppshotRecording | null;
  disabled: boolean;
  onSave: (patch: AppshotSettingsPatch) => Promise<void>;
  onDirty: (dirty: boolean) => void;
}) {
  const t = useT();
  const id = useId();
  const resolution = settings.recording_resolution ?? "1080p";
  const fps = settings.recording_fps ?? 60;
  const bitrate = settings.recording_bitrate_mbps ?? 12;
  const audio = settings.recording_system_audio ?? false;
  const [draft, setDraft] = useState({ resolution, fps, bitrate: String(bitrate), audio });
  useEffect(() => {
    setDraft({ resolution, fps, bitrate: String(bitrate), audio });
  }, [resolution, fps, bitrate, audio]);
  const dirty = draft.resolution !== resolution || draft.fps !== fps
    || draft.bitrate !== String(bitrate) || draft.audio !== audio;
  useEffect(() => { onDirty(dirty); }, [dirty, onDirty]);
  const validBitrate = /^\d+$/.test(draft.bitrate) && Number(draft.bitrate) >= 1 && Number(draft.bitrate) <= 100;
  const audioCapability = recording?.capability?.system_audio;

  return (
    <form className="mt-4 space-y-3 border-t border-border pt-4" onSubmit={(event) => {
      event.preventDefault();
      if (!validBitrate || disabled || !dirty) return;
      void onSave({ recording_resolution: draft.resolution, recording_fps: draft.fps,
        recording_bitrate_mbps: Number(draft.bitrate), recording_system_audio: draft.audio });
    }}>
      <fieldset disabled={disabled} className="grid gap-3 sm:grid-cols-3">
        <legend className="sr-only">{t("appshots.recording_quality")}</legend>
        <label htmlFor={`${id}-resolution`} className="text-sm text-foreground">
          {t("appshots.recording_resolution")}
          <BrandedSelect id={`${id}-resolution`} className={fieldClass} value={draft.resolution}
            ariaLabel={t("appshots.recording_resolution")} disabled={disabled}
            onValueChange={(value) => setDraft({ ...draft, resolution: value as typeof resolution })}
            options={[
              { value: "720p", label: "720p · HD" },
              { value: "1080p", label: "1080p · Full HD" },
              { value: "1440p", label: "1440p · QHD" },
              { value: "2160p", label: "2160p · 4K" },
              { value: "native", label: t("appshots.recording_native") },
            ]} />
        </label>
        <label htmlFor={`${id}-fps`} className="text-sm text-foreground">
          {t("appshots.recording_fps")}
          <BrandedSelect id={`${id}-fps`} className={fieldClass} value={String(draft.fps)}
            ariaLabel={t("appshots.recording_fps")} disabled={disabled}
            onValueChange={(value) => setDraft({ ...draft, fps: Number(value) as typeof fps })}
            options={[30, 60, 120].map((value) => ({ value: String(value), label: `${value} FPS` }))} />
        </label>
        <label htmlFor={`${id}-bitrate`} className="text-sm text-foreground">
          {t("appshots.recording_bitrate")}
          <input id={`${id}-bitrate`} className={fieldClass} type="number" min={1} max={100} step={1}
            value={draft.bitrate} aria-invalid={!validBitrate} aria-describedby={`${id}-bitrate-help`}
            onChange={(event) => setDraft({ ...draft, bitrate: event.target.value })} />
        </label>
      </fieldset>
      <p id={`${id}-bitrate-help`} className={`text-xs ${validBitrate ? "text-muted-foreground" : "text-destructive"}`}>
        {t("appshots.recording_bitrate_hint")}
      </p>
      <label className="flex items-center gap-2 text-sm text-foreground">
        <input type="checkbox" checked={draft.audio} className="h-4 w-4 accent-primary"
          disabled={disabled || (!draft.audio && !audioCapability?.available)}
          onChange={(event) => setDraft({ ...draft, audio: event.target.checked })} />
        {t("appshots.recording_system_audio")}
      </label>
      <p className="text-xs text-muted-foreground">{t("appshots.recording_audio_hint")}</p>
      {audioCapability?.detail && <p className="text-xs text-muted-foreground">{audioCapability.detail}</p>}
      <p className="text-xs text-muted-foreground">{t("appshots.recording_monitor_hint")}</p>
      {dirty && <Button type="submit" variant="secondary" disabled={disabled || !validBitrate}>
        {t("appshots.recording_apply")}
      </Button>}
    </form>
  );
}
