import { useEffect, useRef, useState } from "react";
import { Timer } from "lucide-react";
import { useSilenceWindow } from "@/hooks/useSilenceWindow";
import { useEventStore } from "@/store/events";
import { useT } from "@/i18n";

/** The automatic setting: every voice mode keeps its own factory timing. */
const AUTOMATIC_MS = 0;
/** Fallback for the lowest REAL window, until the GET brings the server's own. */
const MANUAL_MIN_FALLBACK_MS = 500;

/**
 * "Thinking pause" slider inside the Settings view. Tunes how long Jarvis
 * waits in silence before it takes the user's turn — ONE value for both voice
 * engines (maintainer directive 2026-08-18; the pipeline-only scope of
 * 2026-07-21 is history).
 *
 * The slider starts at AUTOMATIC (0, the default): no override is sent
 * anywhere, so the classic pipeline uses its 1.5 s rule and a realtime
 * provider ends the turn on its own factory timing — the shortest pause there
 * is, and the one a caller gets from the vendor's own client (maintainer
 * 2026-08-23). Above that the range is 0.5–5.0 s in 0.1 s steps; the dead gap
 * between automatic and the floor snaps to whichever side is nearer, so a drag
 * can always reach both. The label tracks the slider live; the PUT fires on
 * release (pointer/key up) so a 0.1 s-step drag does not storm the backend.
 * The change persists to jarvis.toml and applies live to the classic pipeline;
 * a realtime session reads it when it opens, so there it lands on the next
 * call. A headless host falls back to "applies on next start".
 */
export function SilenceWindowGroup() {
  const t = useT();
  const { config, loading, setMs } = useSilenceWindow();
  const pushToast = useEventStore((s) => s.pushToast);

  // Local slider value (ms). Mirrors the server value once GET resolves; the
  // label follows it instantly on drag while the PUT waits for commit.
  const [ms, setLocalMs] = useState(AUTOMATIC_MS);
  const [saving, setSaving] = useState(false);
  // The last value we actually committed — guards against an idle mouseUp (no
  // drag) firing a redundant PUT.
  const committedRef = useRef(AUTOMATIC_MS);

  useEffect(() => {
    if (config) {
      setLocalMs(config.ms);
      committedRef.current = config.ms;
    }
  }, [config]);

  const manualMin = config?.manual_min ?? MANUAL_MIN_FALLBACK_MS;
  const defaultMs = config?.default ?? AUTOMATIC_MS;
  const isAutomatic = ms <= AUTOMATIC_MS;

  /** Snap the dead gap below the floor to the nearer end so both stay reachable. */
  function snap(raw: number): number {
    if (raw >= manualMin) return raw;
    return raw > manualMin / 2 ? manualMin : AUTOMATIC_MS;
  }

  /** The slider's own wording for a value: "Automatic", or seconds. */
  function describe(value: number): string {
    return value <= AUTOMATIC_MS
      ? t("settings_view.silence_window.automatic_label")
      : `${(value / 1000).toFixed(1)} ${t("settings_view.silence_window.unit_seconds")}`;
  }

  async function commit(next: number) {
    if (next === committedRef.current) return; // no change → no PUT
    committedRef.current = next;
    setSaving(true);
    try {
      const res = await setMs(next);
      pushToast(
        "success",
        res.ms <= AUTOMATIC_MS
          ? t("settings_view.silence_window.saved_toast_automatic")
          : t("settings_view.silence_window.saved_toast").replace("{0}", describe(res.ms)),
      );
      if (res.restart_required) {
        pushToast("warning", t("settings_view.silence_window.restart_caption"));
      }
    } catch (e) {
      pushToast("error", (e as Error).message);
      // Revert the local value to the last known-good so the UI does not lie.
      setLocalMs(committedRef.current);
    } finally {
      setSaving(false);
    }
  }

  function onReset() {
    setLocalMs(defaultMs);
    void commit(defaultMs);
  }

  const showReset = ms !== defaultMs;

  return (
    <div className="mt-2 rounded-lg border border-border bg-card/60 p-4">
      <div className="flex items-start gap-3">
        <Timer className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-4">
            <h4 className="font-display text-sm font-semibold">
              {t("settings_view.silence_window.title")}
            </h4>
            <span className="font-mono text-sm text-primary">{describe(ms)}</span>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            {t("settings_view.silence_window.description")}
          </p>

          <input
            type="range"
            min={config?.min ?? AUTOMATIC_MS}
            max={config?.max ?? 5000}
            step={100}
            value={ms}
            disabled={loading || saving}
            onChange={(e) => setLocalMs(snap(Number(e.target.value)))}
            onMouseUp={() => void commit(ms)}
            onKeyUp={() => void commit(ms)}
            onTouchEnd={() => void commit(ms)}
            className="mt-4 w-full accent-primary disabled:opacity-50"
          />

          {isAutomatic && (
            <p className="mt-2 text-[11px] text-muted-foreground">
              {t("settings_view.silence_window.automatic_caption")}
            </p>
          )}

          {showReset && (
            <button
              type="button"
              onClick={onReset}
              disabled={saving}
              className="mt-3 text-[11px] text-muted-foreground underline hover:text-foreground disabled:opacity-50"
            >
              {t("settings_view.silence_window.reset")}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
