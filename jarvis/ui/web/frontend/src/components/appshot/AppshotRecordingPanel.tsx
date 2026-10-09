import { useEffect, useState } from "react";
import { Loader2, Square, Video } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";
import { AppshotLibrary } from "@/views/AppshotLibrary";
import {
  controlAppshotRecording, fetchAppshotRecording,
  requestRecordingPermission, type AppshotRecording, type AppshotSettings, type AppshotSettingsPatch,
} from "@/lib/appshotApi";
import { AppshotRecordingSettings } from "./AppshotRecordingSettings";

/** Start, stop, quality and the kept videos as playable tiles. Its shortcut
 *  lives with the other appshot shortcuts at the top of the page. */
export function AppshotRecordingPanel({ settings, saving, onSaved, onSettings }: {
  settings: AppshotSettings;
  saving: boolean;
  onSaved?: (id: string) => void;
  onSettings?: (patch: AppshotSettingsPatch) => Promise<void>;
}) {
  const t = useT();
  const [recording, setRecording] = useState<AppshotRecording | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [loadError, setLoadError] = useState("");
  const [revision, setRevision] = useState(0);
  const [settingsDirty, setSettingsDirty] = useState(false);

  useEffect(() => {
    if (recording?.phase === "saved" && recording.id) onSaved?.(recording.id);
  }, [recording?.phase, recording?.id, onSaved]);

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    let failures = 0;
    async function refresh() {
      try {
        const next = await fetchAppshotRecording(controller.signal);
        if (controller.signal.aborted) return;
        setRecording(next);
        failures = 0;
        setLoadError("");
      } catch (cause) {
        if (controller.signal.aborted) return;
        failures += 1;
        setLoadError((cause as Error).message);
      }
      if (!controller.signal.aborted) {
        // One in-flight request, jitter and bounded backoff; hidden views sleep.
        const delay = document.hidden ? 10000 : Math.min(30000, 1500 * 2 ** failures);
        timer = setTimeout(refresh, delay + Math.random() * 500);
      }
    }
    void refresh();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [revision]);

  const active = recording && ["selecting", "recording", "stopping"].includes(recording.phase);
  const stop = recording?.phase === "recording";
  async function control() {
    setBusy(true);
    setError("");
    try {
      const next = await controlAppshotRecording(active ? "stop" : "start");
      setRecording((current) => ({ ...current, ...next }));
      setRevision((value) => value + 1);
    } catch (cause) {
      setError((cause as Error).message);
    } finally { setBusy(false); }
  }

  const elapsed = Math.floor(recording?.duration_s ?? 0);
  const time = `${Math.floor(elapsed / 60).toString().padStart(2, "0")}:${(elapsed % 60).toString().padStart(2, "0")}`;
  return (
    <section aria-label={t("appshots.recording_title")} className="mt-5 rounded-xl border border-border bg-card p-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <h2 className="flex items-center gap-2 text-base font-medium text-foreground">
            <Video className="h-4 w-4" aria-hidden />{t("appshots.recording_title")}
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">{t("appshots.recording_hint")}</p>
        </div>
        <Button type="button" variant={active ? "secondary" : "default"}
          disabled={busy || recording?.phase === "stopping" || (!active && (saving || settingsDirty || !settings.enabled || !recording?.capability?.available))}
          onClick={() => void control()} data-testid="appshots-recording-control">
          {busy || recording?.phase === "stopping" ? <Loader2 className="animate-spin" aria-hidden /> : active ? <Square aria-hidden /> : <Video aria-hidden />}
          {active ? t(stop ? "appshots.recording_stop" : "appshots.recording_cancel") : t("appshots.recording_start")}
        </Button>
      </div>
      {onSettings && <AppshotRecordingSettings settings={settings} recording={recording}
        disabled={saving || busy || Boolean(active)} onSave={onSettings} onDirty={setSettingsDirty} />}
      {recording?.capability?.detail && <p className="mt-3 text-sm text-muted-foreground">{recording.capability.detail}</p>}
      {recording?.capability?.permission_required && (
        <Button type="button" variant="secondary" className="mt-2" disabled={busy} onClick={async () => {
          setBusy(true);
          try { await requestRecordingPermission(); setRevision((value) => value + 1); }
          catch (cause) { setError((cause as Error).message); }
          finally { setBusy(false); }
        }}>{t("appshots.recording_permission")}</Button>
      )}
      <p role="status" aria-live="polite" className="mt-3 text-sm text-muted-foreground">
        {recording && !["idle", "error"].includes(recording.phase) ? t(`appshots.recording_${recording.phase}`) : ""}
        {recording?.phase === "recording" || recording?.phase === "saved" ? ` · ${time}` : ""}
      </p>
      {recording?.width && recording?.height && recording?.fps && (
        <p className="mt-1 text-xs text-muted-foreground">
          {recording.display_name ? `${recording.display_name} · ` : ""}
          {recording.width} × {recording.height} · {recording.fps} FPS · {recording.bitrate_mbps} Mbps
        </p>
      )}
      {Boolean(recording?.dropped_frames) && <p className="mt-1 text-xs text-muted-foreground">
        {t("appshots.recording_dropped_hint")}
      </p>}
      {(error || loadError || recording?.message) && <p role="alert" className="mt-2 text-sm text-destructive">{error || loadError || recording?.message}</p>}
      {/* A new or removed video changes the recorder's list; the tiles reload then. */}
      <AppshotLibrary enabled={undefined} recordingsOnly
        refreshKey={(recording?.recent ?? []).map((video) => video.id).join(",")} />
    </section>
  );
}
