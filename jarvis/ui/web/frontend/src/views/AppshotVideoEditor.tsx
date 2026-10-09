import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { Film, Loader2, Pause, Play, RotateCcw, SkipBack, SkipForward, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { QuickTooltip } from "@/components/ui/tooltip";
import { useCapabilities } from "@/hooks/useCapabilities";
import { fill, useLocaleChunk, useT } from "@/i18n";
import {
  RECORDING_SPEEDS,
  appshotRecordingUrl,
  exportAppshotRecording,
  saveAppshotRecording,
} from "@/lib/appshotApi";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";

/**
 * The video editor: plays a finished screen recording and trims it.
 *
 * It opens in the appshot editor's window when a recording card is played.
 * Playback, scrubbing and trimming happen on the original file in the page;
 * only Save writes something — the chosen part at the chosen speed becomes a
 * new recording on the backend (`/recording/{id}/export`), the original stays.
 */

/** Shortest clip the trim handles allow, in seconds. */
const MIN_CLIP_S = 0.5;
/** Frames in the timeline's film strip at most. */
const MAX_THUMBS = 14;
const FRAME_S = 1 / 30;

export interface Trim {
  start: number;
  end: number;
}

/** `m:ss.t` under an hour, `h:mm:ss` beyond. */
export function formatVideoTime(seconds: number, precise = true): string {
  const s = Math.max(0, seconds);
  const hours = Math.floor(s / 3600);
  const minutes = Math.floor((s % 3600) / 60);
  const whole = Math.floor(s % 60);
  if (hours) return `${hours}:${String(minutes).padStart(2, "0")}:${String(whole).padStart(2, "0")}`;
  const tenth = precise ? `.${Math.floor((s * 10) % 10)}` : "";
  return `${minutes}:${String(whole).padStart(2, "0")}${tenth}`;
}

/** Move one trim edge, keeping the clip at least `MIN_CLIP_S` long and inside the video. */
export function moveTrimEdge(trim: Trim, edge: "start" | "end", at: number, duration: number): Trim {
  const min = Math.min(MIN_CLIP_S, duration);
  if (edge === "start") return { ...trim, start: Math.max(0, Math.min(at, trim.end - min)) };
  return { ...trim, end: Math.min(duration, Math.max(at, trim.start + min)) };
}

type Drag = { kind: "scrub" } | { kind: "start" | "end" };

export function AppshotVideoEditor({ recordingId, onClose }: { recordingId: string; onClose: () => void }) {
  const t = useT();
  const ready = useLocaleChunk("appshot_editor");
  const label = (key: string) => (ready ? t(`appshot_editor.${key}`) : "");
  const caps = useCapabilities();
  const native = caps.data?.native_file_actions ?? false;
  const pushToast = useEventStore((s) => s.pushToast);

  const src = appshotRecordingUrl(recordingId);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const trackRef = useRef<HTMLDivElement | null>(null);
  const [load, setLoad] = useState<"loading" | "ready" | "failed">("loading");
  const [duration, setDuration] = useState(0);
  const [size, setSize] = useState<[number, number]>([0, 0]);
  const [time, setTime] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState<number>(1);
  const [trim, setTrim] = useState<Trim>({ start: 0, end: 0 });
  const trimRef = useRef(trim);
  trimRef.current = trim;
  const [drag, setDrag] = useState<Drag | null>(null);
  const [saving, setSaving] = useState(false);
  const thumbs = useFilmStrip(src, load === "ready" ? duration : 0, size);

  const trimmed = duration > 0 && (trim.start > 0.05 || trim.end < duration - 0.05);

  // Smooth playhead: follow the video every frame while it plays, and stop
  // at the end of the trimmed part.
  useEffect(() => {
    if (!playing) return;
    let frame = 0;
    const step = () => {
      const video = videoRef.current;
      if (!video) return;
      const { end } = trimRef.current;
      if (video.currentTime >= end - 0.01) {
        video.pause();
        video.currentTime = end;
        setTime(end);
        return;
      }
      setTime(video.currentTime);
      frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [playing]);

  useEffect(() => {
    if (videoRef.current) videoRef.current.playbackRate = speed;
  }, [speed]);

  const seek = useCallback((at: number) => {
    const video = videoRef.current;
    if (!video || !duration) return;
    const clamped = Math.max(0, Math.min(at, duration));
    video.currentTime = clamped;
    setTime(clamped);
  }, [duration]);

  const toggle = useCallback(() => {
    const video = videoRef.current;
    if (!video || load !== "ready") return;
    if (!video.paused) {
      video.pause();
      return;
    }
    const { start, end } = trimRef.current;
    // Play inside the trimmed part; at its end, start over.
    if (video.currentTime >= end - 0.05 || video.currentTime < start) video.currentTime = start;
    void video.play().catch(() => undefined);
  }, [load]);

  const save = useCallback(async () => {
    if (load !== "ready" || saving) return;
    setSaving(true);
    try {
      const unchanged = !trimmed && speed === 1;
      const { id } = unchanged
        ? { id: recordingId }
        : await exportAppshotRecording(recordingId, { start_s: trim.start, end_s: trim.end, speed });
      if (native) {
        const { path, filename } = await saveAppshotRecording(id);
        pushToast("success", fill(t("appshot_editor.saved_to"), { 0: path }), { filePath: path, filename });
      } else {
        // A browser: the file comes down through the browser's own download.
        const link = document.createElement("a");
        link.href = appshotRecordingUrl(id);
        link.download = `recording-${id.slice(0, 6)}.mp4`;
        link.click();
      }
    } catch (error) {
      pushToast("error", fill(t("appshot_editor.save_failed"), { 0: (error as Error).message }));
    } finally {
      setSaving(false);
    }
  }, [load, native, pushToast, recordingId, saving, speed, t, trim, trimmed]);

  // Keyboard: Space/K play, arrows seek (Shift = 5 s), , and . step a frame,
  // I and O set the trim at the playhead, Home/End jump, Ctrl+S saves, Esc closes.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target;
      if (target instanceof Element && target.closest("input, textarea, [contenteditable]")) return;
      const video = videoRef.current;
      const now = video?.currentTime ?? time;
      const key = event.key;
      if ((event.ctrlKey || event.metaKey) && key.toLowerCase() === "s") {
        event.preventDefault();
        void save();
        return;
      }
      if (event.ctrlKey || event.metaKey || event.altKey) return;
      const handled = (() => {
        switch (key) {
          case " ":
          case "k":
          case "K":
            toggle();
            return true;
          case "ArrowLeft":
            seek(now - (event.shiftKey ? 5 : 1));
            return true;
          case "ArrowRight":
            seek(now + (event.shiftKey ? 5 : 1));
            return true;
          case ",":
            video?.pause();
            seek(now - FRAME_S);
            return true;
          case ".":
            video?.pause();
            seek(now + FRAME_S);
            return true;
          case "Home":
            seek(trimRef.current.start);
            return true;
          case "End":
            seek(trimRef.current.end);
            return true;
          case "i":
          case "I":
            setTrim((current) => moveTrimEdge(current, "start", now, duration));
            return true;
          case "o":
          case "O":
            setTrim((current) => moveTrimEdge(current, "end", now, duration));
            return true;
          case "Escape":
            onClose();
            return true;
          default:
            return false;
        }
      })();
      if (handled) event.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [duration, onClose, save, seek, time, toggle]);

  // -- timeline gestures -------------------------------------------------------
  const timeAt = useCallback((clientX: number) => {
    const track = trackRef.current;
    if (!track || !duration) return 0;
    const box = track.getBoundingClientRect();
    return Math.max(0, Math.min(1, (clientX - box.left) / box.width)) * duration;
  }, [duration]);

  const onTrackDown = (event: ReactPointerEvent<HTMLDivElement>, kind: Drag["kind"]) => {
    if (event.button !== 0 || load !== "ready") return;
    event.preventDefault();
    event.stopPropagation();
    event.currentTarget.setPointerCapture?.(event.pointerId);
    setDrag({ kind } as Drag);
    const at = timeAt(event.clientX);
    if (kind === "scrub") seek(at);
  };

  const onTrackMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!drag) return;
    const at = timeAt(event.clientX);
    if (drag.kind === "scrub") {
      seek(at);
      return;
    }
    const next = moveTrimEdge(trimRef.current, drag.kind, at, duration);
    setTrim(next);
    // Show the frame at the edge being dragged.
    seek(drag.kind === "start" ? next.start : next.end);
  };

  const onTrackUp = () => setDrag(null);

  const pct = (seconds: number) => (duration ? `${(seconds / duration) * 100}%` : "0%");
  const clipLength = Math.max(0, trim.end - trim.start) / speed;

  return (
    <div className="relative flex h-full w-full flex-col overflow-hidden bg-popover text-popover-foreground" data-testid="appshot-video-editor" data-native={native}>
      {/* Top bar: title and size · free space to move the window · Save, close. */}
      <div className="flex h-12 shrink-0 items-center gap-2 border-b border-border px-3">
        <div className="flex min-w-0 items-center gap-2 pl-1">
          <Film className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
          <span className="truncate text-[13px] font-semibold">{label("video_title")}</span>
          {size[0] > 0 && (
            <span className="rounded-md bg-foreground/[0.06] px-1.5 py-0.5 text-[11px] font-medium tabular-nums text-muted-foreground">
              {size[0]} × {size[1]}
            </span>
          )}
        </div>
        <div className="pywebview-drag-region h-full min-w-4 flex-1" aria-hidden />
        <div className="flex shrink-0 items-center gap-1.5">
          <QuickTooltip content={`${label("video_save_hint")} (Ctrl+S)`} side="bottom">
            <button
              type="button"
              onClick={() => void save()}
              disabled={load !== "ready" || saving}
              data-testid="appshot-video-save"
              className="flex h-7 items-center gap-1.5 rounded-lg bg-accent px-3.5 text-[13px] font-medium text-accent-foreground transition-colors hover:bg-accent/90 disabled:opacity-50"
            >
              {saving && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />}
              {saving ? label("video_saving") : label("save")}
            </button>
          </QuickTooltip>
          <div className="mx-0.5 h-5 w-px shrink-0 bg-border" aria-hidden />
          <QuickTooltip content={`${label("close")} (Esc)`} side="bottom">
            <button
              type="button"
              onClick={onClose}
              aria-label={label("close")}
              data-testid="appshot-video-close"
              className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-destructive hover:text-destructive-foreground"
            >
              <X className="h-4 w-4" aria-hidden />
            </button>
          </QuickTooltip>
        </div>
      </div>

      {/* The video: a click plays or pauses it. */}
      <div className="relative flex min-h-0 flex-1 items-center justify-center bg-background/70 p-5">
        {load === "failed" ? (
          <div className="flex max-w-sm flex-col items-center gap-3 text-center" data-testid="appshot-video-failed">
            <p className="text-base text-muted-foreground">{label("video_gone")}</p>
            <Button type="button" variant="secondary" size="sm" onClick={onClose}>{label("close")}</Button>
          </div>
        ) : (
          <>
            <video
              ref={videoRef}
              src={src}
              preload="auto"
              playsInline
              onClick={toggle}
              onLoadedMetadata={(event) => {
                const video = event.currentTarget;
                const length = Number.isFinite(video.duration) ? video.duration : 0;
                setDuration(length);
                setSize([video.videoWidth, video.videoHeight]);
                setTrim({ start: 0, end: length });
                video.playbackRate = speed;
                setLoad("ready");
              }}
              onError={() => setLoad("failed")}
              onPlay={() => setPlaying(true)}
              onPause={() => setPlaying(false)}
              onSeeked={(event) => setTime(event.currentTarget.currentTime)}
              className={cn(
                "max-h-full max-w-full cursor-pointer rounded-xl bg-black shadow-2xl ring-1 ring-border transition-opacity duration-200",
                load === "ready" ? "opacity-100" : "opacity-0",
              )}
              data-testid="appshot-video"
            />
            {load === "loading" && (
              <div className="absolute inset-0 flex items-center justify-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                {label("video_loading")}
              </div>
            )}
            {load === "ready" && (
              <button
                type="button"
                onClick={toggle}
                aria-label={label("video_play")}
                tabIndex={-1}
                className={cn(
                  "absolute left-1/2 top-1/2 flex h-16 w-16 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full bg-black/45 text-white shadow-xl ring-1 ring-white/20 backdrop-blur-md transition-all duration-200 hover:scale-105 hover:bg-black/60",
                  playing ? "pointer-events-none scale-90 opacity-0" : "opacity-100",
                )}
              >
                <Play className="ml-1 h-7 w-7 fill-current" aria-hidden />
              </button>
            )}
          </>
        )}
      </div>

      {/* Timeline and transport. */}
      <div className="shrink-0 space-y-3 border-t border-border px-4 pb-3 pt-3.5">
        <div
          ref={trackRef}
          role="slider"
          aria-label={label("video_timeline")}
          aria-valuemin={0}
          aria-valuemax={Math.round(duration * 10) / 10}
          aria-valuenow={Math.round(time * 10) / 10}
          tabIndex={-1}
          onPointerDown={(event) => onTrackDown(event, "scrub")}
          onPointerMove={onTrackMove}
          onPointerUp={onTrackUp}
          onPointerCancel={onTrackUp}
          className={cn(
            "relative h-14 select-none overflow-hidden rounded-xl border border-border bg-foreground/[0.05]",
            load === "ready" ? "cursor-pointer" : "pointer-events-none opacity-60",
          )}
          data-testid="appshot-video-timeline"
        >
          {/* Film strip. */}
          <div className="absolute inset-0 flex">
            {thumbs.map((thumb, index) => (
              <div key={index} className="h-full min-w-0 flex-1 overflow-hidden border-r border-black/20 last:border-r-0">
                <img src={thumb} alt="" draggable={false} className="h-full w-full object-cover animate-in fade-in-0 duration-300" />
              </div>
            ))}
          </div>
          {/* Everything outside the trimmed part is dimmed. */}
          <div className="pointer-events-none absolute inset-y-0 left-0 bg-black/55" style={{ width: pct(trim.start) }} />
          <div className="pointer-events-none absolute inset-y-0 right-0 bg-black/55" style={{ width: `calc(100% - ${pct(trim.end)})` }} />
          {/* The trimmed part with its two handles. */}
          <div
            className="pointer-events-none absolute inset-y-0 rounded-[10px] border-2 border-accent"
            style={{ left: pct(trim.start), width: `calc(${pct(trim.end)} - ${pct(trim.start)})` }}
          />
          {(["start", "end"] as const).map((edge) => (
            <div
                key={edge}
                role="separator"
                aria-label={`${label(edge === "start" ? "video_trim_start" : "video_trim_end")} (${edge === "start" ? "I" : "O"})`}
                onPointerDown={(event) => onTrackDown(event, edge)}
                onPointerMove={onTrackMove}
                onPointerUp={onTrackUp}
                onPointerCancel={onTrackUp}
                className={cn(
                  "absolute inset-y-0 z-10 flex w-3.5 cursor-ew-resize items-center justify-center bg-accent transition-[filter] hover:brightness-110",
                  edge === "start" ? "rounded-l-[10px]" : "-translate-x-full rounded-r-[10px]",
                )}
                style={{ left: pct(edge === "start" ? trim.start : trim.end) }}
                data-testid={`appshot-video-trim-${edge}`}
              >
                <span className="h-5 w-[3px] rounded-full bg-accent-foreground/80" aria-hidden />
              </div>
          ))}
          {/* Playhead. */}
          <div
            className={cn("pointer-events-none absolute inset-y-0 z-20 w-0", drag?.kind === "scrub" ? "" : "transition-[left] duration-75 ease-linear")}
            style={{ left: pct(time) }}
            data-testid="appshot-video-playhead"
          >
            <div className="absolute inset-y-0 -left-px w-0.5 bg-white shadow-[0_0_0_1px_rgba(0,0,0,0.35)]" />
            <div className="absolute -left-[5px] -top-0.5 h-2.5 w-2.5 rounded-full bg-white shadow-[0_0_0_1px_rgba(0,0,0,0.35)]" />
          </div>
        </div>

        <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-3">
          <div className="flex min-w-0 items-baseline gap-1.5 text-[13px] tabular-nums">
            <span className="font-semibold text-foreground">{formatVideoTime(time)}</span>
            <span className="text-muted-foreground">/ {formatVideoTime(duration)}</span>
          </div>

          <div className="flex items-center gap-1.5">
            <QuickTooltip content={`${label("video_to_start")} (Home)`} side="top">
              <button
                type="button"
                onClick={() => seek(trim.start)}
                disabled={load !== "ready"}
                aria-label={label("video_to_start")}
                className="flex h-8 w-8 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-foreground/10 hover:text-foreground disabled:opacity-40"
              >
                <SkipBack className="h-4 w-4 fill-current" aria-hidden />
              </button>
            </QuickTooltip>
            <QuickTooltip content={`${label(playing ? "video_pause" : "video_play")} (Space)`} side="top">
              <button
                type="button"
                onClick={toggle}
                disabled={load !== "ready"}
                aria-label={label(playing ? "video_pause" : "video_play")}
                data-testid="appshot-video-play"
                className="flex h-10 w-10 items-center justify-center rounded-full bg-foreground text-background shadow-md transition-transform hover:scale-105 active:scale-95 disabled:opacity-40"
              >
                {playing ? <Pause className="h-[18px] w-[18px] fill-current" aria-hidden /> : <Play className="ml-0.5 h-[18px] w-[18px] fill-current" aria-hidden />}
              </button>
            </QuickTooltip>
            <QuickTooltip content={`${label("video_to_end")} (End)`} side="top">
              <button
                type="button"
                onClick={() => seek(trim.end)}
                disabled={load !== "ready"}
                aria-label={label("video_to_end")}
                className="flex h-8 w-8 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-foreground/10 hover:text-foreground disabled:opacity-40"
              >
                <SkipForward className="h-4 w-4 fill-current" aria-hidden />
              </button>
            </QuickTooltip>
          </div>

          <div className="flex min-w-0 items-center justify-end gap-2">
            {trimmed && (
              <QuickTooltip content={label("video_trim_reset")} side="top">
                <button
                  type="button"
                  onClick={() => setTrim({ start: 0, end: duration })}
                  aria-label={label("video_trim_reset")}
                  data-testid="appshot-video-trim-reset"
                  className="flex h-7 w-7 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-foreground/10 hover:text-foreground"
                >
                  <RotateCcw className="h-3.5 w-3.5" aria-hidden />
                </button>
              </QuickTooltip>
            )}
            {(trimmed || speed !== 1) && (
              <span className="hidden whitespace-nowrap text-[12px] tabular-nums text-muted-foreground sm:inline" data-testid="appshot-video-clip-length">
                {fill(label("video_clip_length"), { 0: formatVideoTime(clipLength, false) })}
              </span>
            )}
            <div
              className="flex items-center gap-0.5 rounded-xl border border-border bg-foreground/[0.04] p-0.5"
              role="group"
              aria-label={label("video_speed")}
              data-testid="appshot-video-speed"
            >
              {RECORDING_SPEEDS.map((value) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => setSpeed(value)}
                  aria-pressed={speed === value}
                  className={cn(
                    "h-6 rounded-lg px-2 text-[12px] font-medium tabular-nums transition-colors",
                    speed === value ? "bg-accent text-accent-foreground shadow-sm" : "text-muted-foreground hover:bg-foreground/10 hover:text-foreground",
                  )}
                >
                  {value}×
                </button>
              ))}
            </div>
          </div>
        </div>
        <p className="text-center text-[11px] text-muted-foreground/80">{label("video_shortcuts")}</p>
      </div>
    </div>
  );
}

/**
 * Small frames along the video for the timeline, taken by a second, hidden
 * video element so playback is never disturbed. Empty until the first frame
 * is ready; frames appear one by one.
 */
function useFilmStrip(src: string, duration: number, size: [number, number]): string[] {
  const [thumbs, setThumbs] = useState<string[]>([]);
  const count = useMemo(() => {
    if (!duration || !size[0] || !size[1]) return 0;
    // About one frame per 80 px of a typical timeline, never more than MAX_THUMBS.
    return Math.max(4, Math.min(MAX_THUMBS, Math.round(duration / 2) + 4));
  }, [duration, size]);

  useEffect(() => {
    setThumbs([]);
    if (!count) return;
    let cancelled = false;
    const video = document.createElement("video");
    video.muted = true;
    video.preload = "auto";
    video.src = src;
    const canvas = document.createElement("canvas");
    const height = 96;
    canvas.height = height;
    canvas.width = Math.max(1, Math.round((height * size[0]) / size[1]));
    const ctx = canvas.getContext("2d");
    const grab = (at: number) =>
      new Promise<string>((resolve, reject) => {
        const done = () => {
          video.removeEventListener("seeked", done);
          video.removeEventListener("error", fail);
          try {
            ctx?.drawImage(video, 0, 0, canvas.width, canvas.height);
            resolve(canvas.toDataURL("image/jpeg", 0.72));
          } catch (error) {
            reject(error);
          }
        };
        const fail = () => reject(new Error("seek failed"));
        video.addEventListener("seeked", done);
        video.addEventListener("error", fail);
        video.currentTime = at;
      });
    void (async () => {
      try {
        await new Promise<void>((resolve, reject) => {
          if (video.readyState >= 1) resolve();
          video.addEventListener("loadedmetadata", () => resolve(), { once: true });
          video.addEventListener("error", () => reject(new Error("load failed")), { once: true });
        });
        for (let i = 0; i < count && !cancelled; i += 1) {
          const frame = await grab(((i + 0.5) * duration) / count);
          if (!cancelled) setThumbs((current) => [...current, frame]);
        }
      } catch {
        // The strip is decoration: the timeline still works without it.
      }
    })();
    return () => {
      cancelled = true;
      video.removeAttribute("src");
      video.load();
    };
  }, [count, duration, size, src]);

  // Keep the strip's slots stable while frames arrive.
  return thumbs.length ? [...thumbs, ...Array<string>(Math.max(0, count - thumbs.length)).fill("")].map((thumb) => thumb || TRANSPARENT) : [];
}

const TRANSPARENT = "data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==";
