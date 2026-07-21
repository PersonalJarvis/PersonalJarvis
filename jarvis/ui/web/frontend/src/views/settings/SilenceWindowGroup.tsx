import { useEffect, useRef, useState } from "react";
import { Timer } from "lucide-react";
import { useSilenceWindow } from "@/hooks/useSilenceWindow";
import { useEventStore } from "@/store/events";
import { useT } from "@/i18n";

/**
 * "Thinking pause" slider inside the Settings view. Tunes the voice endpoint
 * silence window (how long Jarvis waits in silence before submitting). Range
 * 0.5–5.0 s, step 0.1 s, default 1.5 s. The label tracks the slider live; the
 * PUT fires on release (pointer/key up) so a 0.1 s-step drag does not storm the
 * backend. The change persists to jarvis.toml, applies live to the classic
 * pipeline, and configures every newly opened Realtime provider session (a
 * headless host falls back to "applies on next start").
 */
export function SilenceWindowGroup() {
  const t = useT();
  const { config, loading, setMs } = useSilenceWindow();
  const pushToast = useEventStore((s) => s.pushToast);

  // Local slider value (ms). Mirrors the server value once GET resolves; the
  // label follows it instantly on drag while the PUT waits for commit.
  const [ms, setLocalMs] = useState(1500);
  const [saving, setSaving] = useState(false);
  // The last value we actually committed — guards against an idle mouseUp (no
  // drag) firing a redundant PUT.
  const committedRef = useRef(1500);

  useEffect(() => {
    if (config) {
      setLocalMs(config.ms);
      committedRef.current = config.ms;
    }
  }, [config]);

  const seconds = (ms / 1000).toFixed(1);

  async function commit(next: number) {
    if (next === committedRef.current) return; // no change → no PUT
    committedRef.current = next;
    setSaving(true);
    try {
      const res = await setMs(next);
      pushToast(
        "success",
        t("settings_view.silence_window.saved_toast").replace(
          "{0}",
          `${(res.ms / 1000).toFixed(1)} ${t("settings_view.silence_window.unit_seconds")}`,
        ),
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
    const def = config?.default ?? 1500;
    setLocalMs(def);
    void commit(def);
  }

  const showReset = ms !== (config?.default ?? 1500);

  return (
    <div className="mt-2 rounded-lg border border-border bg-card/60 p-4">
      <div className="flex items-start gap-3">
        <Timer className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-4">
            <h4 className="font-display text-sm font-semibold">
              {t("settings_view.silence_window.title")}
            </h4>
            <span className="font-mono text-sm text-primary">
              {`${seconds} ${t("settings_view.silence_window.unit_seconds")}`}
            </span>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            {t("settings_view.silence_window.description")}
          </p>

          <input
            type="range"
            min={config?.min ?? 500}
            max={config?.max ?? 5000}
            step={100}
            value={ms}
            disabled={loading || saving}
            onChange={(e) => setLocalMs(Number(e.target.value))}
            onMouseUp={() => void commit(ms)}
            onKeyUp={() => void commit(ms)}
            onTouchEnd={() => void commit(ms)}
            className="mt-4 w-full accent-primary disabled:opacity-50"
          />

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
