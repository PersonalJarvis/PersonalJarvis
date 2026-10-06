import { useCallback, useEffect, useState } from "react";
import { Download, Loader2, Square, Video } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";
import {
  appshotRecordingUrl, controlAppshotRecording, fetchAppshotRecording,
  requestRecordingPermission, type AppshotRecording, type AppshotSettings,
} from "@/lib/appshotApi";
import { AppshotShortcutField } from "@/views/AppshotShortcutField";

export function AppshotRecordingPanel({ settings, saving, onShortcut }: {
  settings: AppshotSettings;
  saving: boolean;
  onShortcut: (shortcut: string) => Promise<void>;
}) {
  const t = useT();
  const [recording, setRecording] = useState<AppshotRecording | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [loadError, setLoadError] = useState("");
  const [revision, setRevision] = useState(0);
  const [shortcutNote, setShortcutNote] = useState<string | null>(null);
  const onShortcutStatus = useCallback((text: string | null) => {
    setShortcutNote((current) => (current === text ? current : text));
  }, []);

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
          disabled={busy || recording?.phase === "stopping" || (!active && (!settings.enabled || !recording?.capability?.available))}
          onClick={() => void control()} data-testid="appshots-recording-control">
          {busy || recording?.phase === "stopping" ? <Loader2 className="animate-spin" aria-hidden /> : active ? <Square aria-hidden /> : <Video aria-hidden />}
          {active ? t(stop ? "appshots.recording_stop" : "appshots.recording_cancel") : t("appshots.recording_start")}
        </Button>
      </div>
      <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4">
        <p className="text-sm text-foreground">{t("appshots.recording_shortcut")}</p>
        <AppshotShortcutField value={settings.recording_hotkey ?? ""} disabled={saving || Boolean(active)}
          isMac={/Mac/i.test(navigator.platform || "")} testId="appshots-recording-hotkey"
          label={t("appshots.recording_shortcut")} onSave={onShortcut} onStatus={onShortcutStatus} />
      </div>
      {shortcutNote && (
        <p className="mt-2 text-sm text-muted-foreground" data-testid="appshots-recording-hotkey-status">
          {shortcutNote}
        </p>
      )}
      {settings.recording_shortcut?.detail && settings.recording_hotkey && !settings.recording_shortcut.armed && (
        <p className="mt-2 text-sm text-muted-foreground">{settings.recording_shortcut.detail}</p>
      )}
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
      {(error || loadError || recording?.message) && <p role="alert" className="mt-2 text-sm text-destructive">{error || loadError || recording?.message}</p>}
      {recording?.phase === "saved" && (
        <div className="mt-3">
          <video key={recording.id} src={appshotRecordingUrl(recording.id)} controls preload="metadata"
            className="max-h-80 w-full rounded-lg bg-secondary" aria-label={t("appshots.recording_title")} />
          <a href={appshotRecordingUrl(recording.id)} download className="mt-3 inline-flex items-center gap-2 text-sm text-foreground underline underline-offset-4">
            <Download className="h-4 w-4" aria-hidden />{t("appshots.recording_download")}
          </a>
        </div>
      )}
      {Boolean(recording?.recent?.length) && (
        <ul className="mt-4 space-y-2 border-t border-border pt-3">
          {recording?.recent?.filter((video) => recording.phase !== "saved" || video.id !== recording.id).map((video) => (
            <li key={video.id}>
              <a href={appshotRecordingUrl(video.id)} download className="text-sm text-foreground underline underline-offset-4">
                {t("appshots.recording_download")} · {new Date(video.created_at * 1000).toLocaleString()}
              </a>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
