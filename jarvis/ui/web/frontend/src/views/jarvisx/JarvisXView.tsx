import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AppWindow,
  Aperture,
  Copy,
  Crop,
  ExternalLink,
  FolderOpen,
  Loader2,
  Monitor,
  Pencil,
  Play,
  Square,
  Trash2,
  Video,
} from "lucide-react";

import { PageHeader } from "@/components/layout/PageHeader";
import { TabBar } from "@/components/layout/SectionTabBar";
import { Button } from "@/components/ui/button";
import { QuickTooltip } from "@/components/ui/tooltip";
import { useT } from "@/i18n";
import {
  JARVISX_EVENTS,
  captureJarvisX,
  copyJarvisXItem,
  deleteJarvisXItem,
  fetchJarvisXItems,
  fetchJarvisXRecordStatus,
  fetchJarvisXSettings,
  formatDuration,
  isJarvisXEvent,
  normalizeRecordStatus,
  openJarvisXEditor,
  revealJarvisXItem,
  startJarvisXRecording,
  stopJarvisXRecording,
  withVersion,
  type JarvisXItem,
  type JarvisXMode,
  type JarvisXRecordMode,
  type JarvisXRecordStatus,
  type JarvisXSettings,
} from "@/lib/jarvisxApi";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";
import { JarvisXConfirmDialog } from "@/views/jarvisx/JarvisXConfirmDialog";
import { JarvisXEditor } from "@/views/jarvisx/JarvisXEditor";
import { JarvisXSettingsPanel } from "@/views/jarvisx/JarvisXSettingsPanel";
import { uiLocale } from "@/lib/boardInsights";

/**
 * Jarvis X — screenshots and screen recordings, without the assistant.
 *
 * Two tabs: the library of everything captured (newest first, live over the
 * websocket) with the capture and record buttons on top, and the settings
 * (shortcuts, the corner thumbnail, the save folder). A click on a capture
 * opens the annotation editor over the page; "Open in window" hands the same
 * editor to its own desktop window.
 */

type Tab = "library" | "settings";

function relativeTime(iso: string, t: (key: string) => string, now: number): string {
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return "";
  const diff = Math.max(0, Math.round((now - ms) / 1000));
  if (diff < 45) return t("jarvisx.time_now");
  if (diff < 3600) return t("jarvisx.time_minutes").replace("{0}", String(Math.max(1, Math.round(diff / 60))));
  const date = new Date(ms);
  const sameDay = new Date(now).toDateString() === date.toDateString();
  return sameDay
    ? date.toLocaleTimeString(uiLocale(), { hour: "2-digit", minute: "2-digit" })
    : date.toLocaleDateString(uiLocale(), { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

export function JarvisXView() {
  const t = useT();
  const [tab, setTab] = useState<Tab>("library");
  const [settings, setSettings] = useState<JarvisXSettings | null>(null);
  const [settingsError, setSettingsError] = useState<string | null>(null);
  const [settingsAttempt, setSettingsAttempt] = useState(0);

  useEffect(() => {
    let live = true;
    setSettingsError(null);
    fetchJarvisXSettings()
      .then((next) => live && setSettings(next))
      .catch((error: Error) => live && setSettingsError(error.message));
    return () => {
      live = false;
    };
  }, [settingsAttempt]);

  return (
    <div data-testid="jarvisx-view" className="flex h-full flex-col overflow-y-auto bg-background px-8 pb-10 scrollbar-jarvis">
      <div className="w-full max-w-[1400px]">
        <PageHeader
          icon={<Aperture />}
          title={t("jarvisx.title")}
          description={t("jarvisx.subtitle")}
          tabs={
            <TabBar
              tabs={[
                { id: "library", label: t("jarvisx.tab_library") },
                { id: "settings", label: t("jarvisx.tab_settings") },
              ]}
              active={tab}
              onChange={(id) => setTab(id as Tab)}
            />
          }
        />
        <div className="pt-5">
          {tab === "library" ? (
            <JarvisXLibrary settings={settings} />
          ) : (
            <JarvisXSettingsPanel
              settings={settings}
              error={settingsError}
              onRetry={() => setSettingsAttempt((n) => n + 1)}
              onSettings={setSettings}
            />
          )}
        </div>
      </div>
    </div>
  );
}

function CaptureButton({
  icon,
  label,
  disabled,
  busy,
  onClick,
  testId,
  title,
}: {
  icon: React.ReactNode;
  label: string;
  disabled?: boolean;
  busy?: boolean;
  onClick: () => void;
  testId: string;
  title?: string;
}) {
  return (
    <Button type="button" variant="outline" size="sm" disabled={disabled} onClick={onClick} data-testid={testId} title={title}>
      {busy ? <Loader2 className="animate-spin" aria-hidden /> : icon}
      {label}
    </Button>
  );
}

export function JarvisXLibrary({ settings }: { settings: JarvisXSettings | null }) {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  // The newest Jarvis X event — its id changes on every capture, edit, delete
  // or recording change, which is all this view needs to know to refresh.
  const lastEvent = useEventStore((s) => s.events.find((event) => isJarvisXEvent(event.name)));
  const [items, setItems] = useState<JarvisXItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [record, setRecord] = useState<JarvisXRecordStatus>({ recording: false, mode: null, elapsed_s: 0 });
  const [recordSince, setRecordSince] = useState(() => Date.now());
  const [now, setNow] = useState(() => Date.now());
  const [busy, setBusy] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<JarvisXItem | null>(null);
  const versions = useRef(new Map<string, number>());

  const load = useCallback(async () => {
    try {
      setItems(await fetchJarvisXItems(100));
      setError(null);
    } catch (err) {
      setError((err as Error).message);
      setItems((current) => current ?? []);
    }
  }, []);

  useEffect(() => {
    void load();
    fetchJarvisXRecordStatus()
      .then((status) => {
        setRecord(status);
        setRecordSince(Date.now());
      })
      .catch(() => undefined);
  }, [load]);

  useEffect(() => {
    if (!lastEvent) return;
    if (lastEvent.name === JARVISX_EVENTS.recording) {
      setRecord(normalizeRecordStatus(lastEvent.payload));
      setRecordSince(Date.now());
      return;
    }
    if (lastEvent.name === JARVISX_EVENTS.updated) {
      const id = (lastEvent.payload as { id?: unknown } | undefined)?.id;
      if (typeof id === "string") versions.current.set(id, (versions.current.get(id) ?? 0) + 1);
    }
    void load();
  }, [lastEvent, load]);

  // One clock for the relative times and the recording timer.
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), record.recording ? 1000 : 30_000);
    return () => window.clearInterval(id);
  }, [record.recording]);

  const run = useCallback(
    async (key: string, action: () => Promise<{ ok?: boolean; message?: string } | void>, success?: string) => {
      setBusy(key);
      try {
        const result = await action();
        if (result && result.ok === false) pushToast("warning", result.message || t("jarvisx.action_failed"));
        else if (success) pushToast("success", success);
      } catch (err) {
        pushToast("error", (err as Error).message);
      } finally {
        setBusy(null);
      }
    },
    [pushToast, t],
  );

  const capture = (mode: JarvisXMode) =>
    run(`capture-${mode}`, async () => {
      const result = await captureJarvisX(mode);
      if (result.ok && result.item) {
        setItems((current) => [result.item!, ...(current ?? []).filter((i) => i.id !== result.item!.id)]);
      }
      return result;
    });

  const startRecording = (mode: JarvisXRecordMode) =>
    run(`record-${mode}`, async () => {
      const result = await startJarvisXRecording(mode);
      if (result.ok !== false) {
        setRecord({ recording: true, mode, elapsed_s: 0 });
        setRecordSince(Date.now());
      }
      return result;
    });

  const stopRecording = () =>
    run("record-stop", async () => {
      const result = await stopJarvisXRecording();
      if (result.ok !== false) setRecord({ recording: false, mode: null, elapsed_s: 0 });
      return result;
    });

  const openInWindow = (item: JarvisXItem) =>
    run(`open-${item.id}`, async () => {
      try {
        const result = await openJarvisXEditor(item.id);
        if (result.ok === false) setEditing(item.id);
      } catch {
        // No desktop shell to open a window in: edit right here instead.
        setEditing(item.id);
      }
    });

  const remove = async (item: JarvisXItem) => {
    setConfirmDelete(null);
    await run(`delete-${item.id}`, async () => {
      const result = await deleteJarvisXItem(item.id);
      setItems((current) => (current ?? []).filter((i) => i.id !== item.id));
      return result;
    }, t("jarvisx.deleted"));
  };

  const disabled = settings !== null && !settings.enabled;
  const recordingAvailable = settings?.recording_available ?? true;
  const recordingTitle = !recordingAvailable ? settings?.recording_detail || t("jarvisx.recording_unavailable") : undefined;
  const elapsed = record.elapsed_s + (record.recording ? (now - recordSince) / 1000 : 0);

  const sorted = useMemo(() => items ?? [], [items]);

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-center gap-2 rounded-xl border border-border bg-card p-3">
        <CaptureButton
          icon={<Crop aria-hidden />}
          label={t("jarvisx.capture_region")}
          disabled={disabled || busy !== null}
          busy={busy === "capture-region"}
          onClick={() => void capture("region")}
          testId="jarvisx-capture-region"
        />
        <CaptureButton
          icon={<AppWindow aria-hidden />}
          label={t("jarvisx.capture_window")}
          disabled={disabled || busy !== null}
          busy={busy === "capture-window"}
          onClick={() => void capture("window")}
          testId="jarvisx-capture-window"
        />
        <CaptureButton
          icon={<Monitor aria-hidden />}
          label={t("jarvisx.capture_fullscreen")}
          disabled={disabled || busy !== null}
          busy={busy === "capture-fullscreen"}
          onClick={() => void capture("fullscreen")}
          testId="jarvisx-capture-fullscreen"
        />
        <span className="mx-1 hidden h-6 w-px bg-border sm:block" aria-hidden />
        {record.recording ? (
          <div className="flex items-center gap-3" data-testid="jarvisx-recording">
            <span className="flex items-center gap-2 text-sm font-medium text-foreground" role="status">
              <span className="relative flex h-2.5 w-2.5">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-destructive opacity-60 motion-reduce:animate-none" />
                <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-destructive" />
              </span>
              {t("jarvisx.recording")}
              <span className="tabular-nums text-muted-foreground">{formatDuration(elapsed)}</span>
            </span>
            <Button
              type="button"
              size="sm"
              variant="destructive"
              disabled={busy === "record-stop"}
              onClick={() => void stopRecording()}
              data-testid="jarvisx-record-stop"
            >
              <Square aria-hidden className="fill-current" />
              {t("jarvisx.record_stop")}
            </Button>
          </div>
        ) : (
          <>
            <CaptureButton
              icon={<Video aria-hidden />}
              label={t("jarvisx.record_region")}
              disabled={disabled || busy !== null || !recordingAvailable}
              busy={busy === "record-region"}
              onClick={() => void startRecording("region")}
              testId="jarvisx-record-region"
              title={recordingTitle}
            />
            <CaptureButton
              icon={<Video aria-hidden />}
              label={t("jarvisx.record_fullscreen")}
              disabled={disabled || busy !== null || !recordingAvailable}
              busy={busy === "record-fullscreen"}
              onClick={() => void startRecording("fullscreen")}
              testId="jarvisx-record-fullscreen"
              title={recordingTitle}
            />
          </>
        )}
        {disabled && <span className="ml-auto text-sm text-muted-foreground">{t("jarvisx.disabled_hint")}</span>}
      </div>

      {error && (
        <p role="alert" className="rounded-lg border border-border bg-card px-4 py-3 text-sm text-muted-foreground">
          {t("jarvisx.load_failed").replace("{0}", error)}
        </p>
      )}

      {items === null ? (
        <div className="flex h-40 items-center justify-center" role="status" aria-busy="true">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" aria-hidden />
        </div>
      ) : sorted.length === 0 ? (
        !error && (
          <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-border px-6 py-16 text-center">
            <Aperture className="h-8 w-8 text-muted-foreground" aria-hidden />
            <p className="mt-3 text-base font-medium text-foreground">{t("jarvisx.empty_title")}</p>
            <p className="mt-1 max-w-md text-sm text-muted-foreground">{t("jarvisx.empty_body")}</p>
          </div>
        )
      ) : (
        <ul data-testid="jarvisx-grid" className="grid grid-cols-[repeat(auto-fill,minmax(220px,1fr))] gap-4">
          {sorted.map((item) => (
            <LibraryCard
              key={item.id}
              item={item}
              version={versions.current.get(item.id) ?? 0}
              now={now}
              busy={busy?.endsWith(item.id) ?? false}
              onOpen={() => setEditing(item.id)}
              onOpenWindow={() => void openInWindow(item)}
              onCopy={() =>
                void run(`copy-${item.id}`, () => copyJarvisXItem(item.id, Boolean(item.edited_url)), t("jarvisx.copied"))
              }
              onReveal={() => void run(`reveal-${item.id}`, () => revealJarvisXItem(item.id))}
              onDelete={() => setConfirmDelete(item)}
            />
          ))}
        </ul>
      )}

      {confirmDelete && (
        <JarvisXConfirmDialog
          title={t("jarvisx.delete_title")}
          body={t("jarvisx.delete_body")}
          confirmLabel={t("jarvisx.delete")}
          onCancel={() => setConfirmDelete(null)}
          onConfirm={() => void remove(confirmDelete)}
        />
      )}

      {editing && (
        <div
          className="fixed inset-0 z-[150] flex bg-scrim/60 p-4 md:p-8"
          role="dialog"
          aria-modal="true"
          aria-label={t("jarvisx.editor.title")}
        >
          <JarvisXEditor
            itemId={editing}
            variant="overlay"
            onClose={() => {
              setEditing(null);
              void load();
            }}
          />
        </div>
      )}
    </div>
  );
}

function LibraryCard({
  item,
  version,
  now,
  busy,
  onOpen,
  onOpenWindow,
  onCopy,
  onReveal,
  onDelete,
}: {
  item: JarvisXItem;
  version: number;
  now: number;
  busy: boolean;
  onOpen: () => void;
  onOpenWindow: () => void;
  onCopy: () => void;
  onReveal: () => void;
  onDelete: () => void;
}) {
  const t = useT();
  const [broken, setBroken] = useState(false);
  const isVideo = item.kind === "video";
  const thumb = version ? withVersion(item.thumb_url, version) : item.thumb_url;
  const modeLabel = t(`jarvisx.mode_${item.mode}`);

  return (
    <li
      data-testid="jarvisx-item"
      className="group relative flex flex-col overflow-hidden rounded-xl border border-border bg-card transition-colors hover:border-border-strong focus-within:border-border-strong"
    >
      <button
        type="button"
        onClick={onOpen}
        aria-label={t(isVideo ? "jarvisx.open_video" : "jarvisx.open_editor").replace("{0}", item.filename)}
        className="relative block aspect-[16/10] w-full overflow-hidden bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
      >
        {broken ? (
          <span className="flex h-full w-full items-center justify-center text-muted-foreground">
            {isVideo ? <Video className="h-6 w-6" aria-hidden /> : <Aperture className="h-6 w-6" aria-hidden />}
          </span>
        ) : (
          <img
            src={thumb}
            alt=""
            loading="lazy"
            draggable={false}
            onError={() => setBroken(true)}
            className="h-full w-full object-contain"
          />
        )}
        <span className="absolute bottom-2 left-2 flex items-center gap-1">
          <span className="rounded bg-scrim/70 px-1.5 py-0.5 text-micro font-medium text-white">{modeLabel}</span>
          {item.edited_url && (
            <span className="rounded bg-accent px-1.5 py-0.5 text-micro font-medium text-accent-foreground">
              {t("jarvisx.edited")}
            </span>
          )}
        </span>
        {isVideo && (
          <span className="absolute bottom-2 right-2 flex items-center gap-1 rounded bg-scrim/70 px-1.5 py-0.5 text-micro font-medium tabular-nums text-white">
            <Play className="h-3 w-3 fill-current" aria-hidden />
            {formatDuration(item.duration_s) || t("jarvisx.kind_video")}
          </span>
        )}
      </button>
      <div className="flex items-center justify-between gap-2 px-3 py-2.5">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium text-foreground" title={item.filename}>
            {relativeTime(item.created_at, t, now)}
          </p>
          <p className="truncate text-micro text-muted-foreground">
            {[isVideo ? t("jarvisx.kind_video") : t("jarvisx.kind_image"), item.width && item.height ? `${item.width} × ${item.height}` : ""]
              .filter(Boolean)
              .join(" · ")}
          </p>
        </div>
        {busy && <Loader2 className="h-4 w-4 shrink-0 animate-spin text-muted-foreground" aria-hidden />}
      </div>
      {/* Actions float over the picture on hover or keyboard focus. */}
      <div className="pointer-events-none absolute right-2 top-2 flex gap-1 opacity-0 transition-opacity group-hover:pointer-events-auto group-hover:opacity-100 group-focus-within:pointer-events-auto group-focus-within:opacity-100">
        {!isVideo && (
          <CardAction label={t("jarvisx.edit")} onClick={onOpen} testId="jarvisx-item-edit">
            <Pencil aria-hidden />
          </CardAction>
        )}
        <CardAction label={t("jarvisx.open_window")} onClick={onOpenWindow} testId="jarvisx-item-window">
          <ExternalLink aria-hidden />
        </CardAction>
        <CardAction label={t("jarvisx.copy")} onClick={onCopy} testId="jarvisx-item-copy">
          <Copy aria-hidden />
        </CardAction>
        <CardAction label={t("jarvisx.reveal")} onClick={onReveal} testId="jarvisx-item-reveal">
          <FolderOpen aria-hidden />
        </CardAction>
        <CardAction label={t("jarvisx.delete")} onClick={onDelete} testId="jarvisx-item-delete" danger>
          <Trash2 aria-hidden />
        </CardAction>
      </div>
    </li>
  );
}

function CardAction({
  label,
  onClick,
  children,
  testId,
  danger,
}: {
  label: string;
  onClick: () => void;
  children: React.ReactNode;
  testId: string;
  danger?: boolean;
}) {
  return (
    <QuickTooltip content={label} side="bottom">
      <button
        type="button"
        aria-label={label}
        onClick={onClick}
        data-testid={testId}
        className={cn(
          "grid h-7 w-7 place-items-center rounded-md border border-border bg-popover text-foreground shadow-sm transition-colors [&>svg]:h-3.5 [&>svg]:w-3.5",
          "hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          danger && "hover:text-destructive",
        )}
      >
        {children}
      </button>
    </QuickTooltip>
  );
}
