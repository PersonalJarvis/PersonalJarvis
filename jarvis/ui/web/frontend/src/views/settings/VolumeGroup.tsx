import { useEffect, useRef, useState } from "react";
import { Volume2 } from "lucide-react";
import { useTtsVolume } from "@/hooks/useTtsVolume";
import { useEventStore } from "@/store/events";
import { useT } from "@/i18n";

/**
 * "Volume" slider inside the Settings view. Tunes how loudly Jarvis speaks
 * (master TTS output gain). The backend value is a 0.0–1.0 amplitude factor;
 * this slider presents it as 0–100% (100% = full, the historical loudness).
 * The label tracks the slider live; the PUT fires on release (pointer/key up)
 * so a drag does not storm the backend. The change persists to jarvis.toml and
 * applies live to the running player — no restart (a headless host falls back
 * to "applies on next start").
 */
export function VolumeGroup() {
  const t = useT();
  const { config, loading, setVolume } = useTtsVolume();
  const pushToast = useEventStore((s) => s.pushToast);

  // Local slider value in PERCENT (0–100). Mirrors the server 0.0–1.0 factor
  // once GET resolves; the label follows it instantly on drag while the PUT
  // waits for commit.
  const [pct, setLocalPct] = useState(100);
  const [saving, setSaving] = useState(false);
  // The last percent we actually committed — guards an idle mouseUp (no drag)
  // from firing a redundant PUT.
  const committedRef = useRef(100);

  useEffect(() => {
    if (config) {
      const p = Math.round(config.volume * 100);
      setLocalPct(p);
      committedRef.current = p;
    }
  }, [config]);

  async function commit(nextPct: number) {
    if (nextPct === committedRef.current) return; // no change → no PUT
    committedRef.current = nextPct;
    setSaving(true);
    try {
      const res = await setVolume(nextPct / 100);
      pushToast(
        "success",
        t("settings_view.volume.saved_toast").replace(
          "{0}",
          `${Math.round(res.volume * 100)}%`,
        ),
      );
      if (res.restart_required) {
        pushToast("warning", t("settings_view.volume.restart_caption"));
      }
    } catch (e) {
      pushToast("error", (e as Error).message);
      // Revert the local value to the last known-good so the UI does not lie.
      setLocalPct(committedRef.current);
    } finally {
      setSaving(false);
    }
  }

  function onReset() {
    const def = Math.round((config?.default ?? 1) * 100);
    setLocalPct(def);
    void commit(def);
  }

  const showReset = pct !== Math.round((config?.default ?? 1) * 100);

  return (
    <div className="mt-2 rounded-lg border border-border bg-card/60 p-4">
      <div className="flex items-start gap-3">
        <Volume2 className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-4">
            <h4 className="font-display text-sm font-semibold">
              {t("settings_view.volume.title")}
            </h4>
            <span className="font-mono text-sm text-primary">{`${pct}%`}</span>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            {t("settings_view.volume.description")}
          </p>

          <input
            type="range"
            min={0}
            max={100}
            step={1}
            value={pct}
            disabled={loading || saving}
            onChange={(e) => setLocalPct(Number(e.target.value))}
            onMouseUp={() => void commit(pct)}
            onKeyUp={() => void commit(pct)}
            onTouchEnd={() => void commit(pct)}
            className="mt-4 w-full accent-primary disabled:opacity-50"
          />

          {showReset && (
            <button
              type="button"
              onClick={onReset}
              disabled={saving}
              className="mt-3 text-[11px] text-muted-foreground underline hover:text-foreground disabled:opacity-50"
            >
              {t("settings_view.volume.reset")}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
