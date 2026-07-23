import { useEffect, useRef, useState } from "react";
import { Maximize2 } from "lucide-react";
import { useBarSize } from "@/hooks/useBarSize";
import { useEventStore } from "@/store/events";
import { useT } from "@/i18n";

/**
 * "Bar size" slider inside the Settings view. Tunes how big the on-screen bar
 * is — a proportional multiplier that scales WIDTH and HEIGHT together, so the
 * pill keeps its shape and only its size changes (0.5–2.0, presented as
 * 50–200%, default 100%). Unlike the Volume/Silence sliders (which commit only
 * on release), this one streams LIVE resizes while dragging — throttled, with
 * persist=false — so the bar grows/shrinks on screen in real time, then writes
 * jarvis.toml once on release (persist=true). The change applies live to the
 * running bar; a headless host falls back to "applies on next start".
 */
const LIVE_THROTTLE_MS = 60;

export function BarSizeGroup() {
  const t = useT();
  const { config, loading, setScale } = useBarSize();
  const pushToast = useEventStore((s) => s.pushToast);

  // Local slider value in PERCENT (50–200). Mirrors the server scale once GET
  // resolves; the label + the on-screen bar follow it live while dragging.
  // Pre-GET placeholder = the 135% product default.
  const [pct, setLocalPct] = useState(135);
  const [saving, setSaving] = useState(false);
  // The last percent we actually PERSISTED — guards an idle mouseUp (no drag)
  // from firing a redundant persist PUT.
  const committedRef = useRef(135);
  // Live-throttle bookkeeping for the drag stream (persist=false).
  const lastLiveRef = useRef(0);
  const pendingRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const min = Math.round((config?.min ?? 0.5) * 100);
  const max = Math.round((config?.max ?? 2) * 100);
  const def = Math.round((config?.default ?? 1.35) * 100);

  useEffect(() => {
    if (config) {
      const p = Math.round(config.scale * 100);
      setLocalPct(p);
      committedRef.current = p;
    }
  }, [config]);

  // Clear any pending trailing live-send if the view unmounts mid-drag.
  useEffect(
    () => () => {
      if (pendingRef.current) clearTimeout(pendingRef.current);
    },
    [],
  );

  // One live (persist=false) resize — resizes the on-screen bar without a disk
  // write. A transient error is swallowed; the release PUT reconciles.
  function sendLive(nextPct: number) {
    void setScale(nextPct / 100, false).catch(() => {
      /* transient live-apply error — the release persist PUT reconciles */
    });
  }

  // Drag handler: update the label instantly, then live-resize the bar,
  // throttled so a fast drag does not storm the backend while always
  // trailing-flushing the latest value so the bar lands on the final size.
  function onDrag(nextPct: number) {
    setLocalPct(nextPct);
    const now = Date.now();
    if (pendingRef.current) {
      clearTimeout(pendingRef.current);
      pendingRef.current = null;
    }
    if (now - lastLiveRef.current >= LIVE_THROTTLE_MS) {
      lastLiveRef.current = now;
      sendLive(nextPct);
    } else {
      pendingRef.current = setTimeout(() => {
        lastLiveRef.current = Date.now();
        pendingRef.current = null;
        sendLive(nextPct);
      }, LIVE_THROTTLE_MS);
    }
  }

  // Release handler: persist the final value to jarvis.toml (once).
  async function commit(nextPct: number) {
    if (pendingRef.current) {
      clearTimeout(pendingRef.current);
      pendingRef.current = null;
    }
    if (nextPct === committedRef.current) return; // no net change → no persist
    committedRef.current = nextPct;
    setSaving(true);
    try {
      const res = await setScale(nextPct / 100, true);
      pushToast(
        res.applied_live ? "success" : "warning",
        res.applied_live
          ? t("settings_view.bar_size.saved_toast").replace("{0}", `${nextPct}%`)
          : t("settings_view.bar_size.restart_caption"),
      );
    } catch (e) {
      pushToast("error", (e as Error).message);
      // Revert the local value to the last persisted so the UI does not lie.
      setLocalPct(committedRef.current);
      sendLive(committedRef.current);
    } finally {
      setSaving(false);
    }
  }

  function onReset() {
    setLocalPct(def);
    sendLive(def);
    void commit(def);
  }

  const showReset = pct !== def;

  return (
    <div className="mt-2 rounded-lg border border-border bg-card/60 p-4">
      <div className="flex items-start gap-3">
        <Maximize2 className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-4">
            <h4 className="font-display text-sm font-semibold">
              {t("settings_view.bar_size.title")}
            </h4>
            <span className="font-mono text-sm text-primary">{`${pct}%`}</span>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            {t("settings_view.bar_size.description")}
          </p>

          <input
            type="range"
            min={min}
            max={max}
            step={5}
            value={pct}
            disabled={loading}
            onChange={(e) => onDrag(Number(e.target.value))}
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
              {t("settings_view.bar_size.reset")}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
