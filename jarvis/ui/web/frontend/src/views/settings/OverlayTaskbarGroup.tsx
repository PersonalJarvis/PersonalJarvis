import { useEffect, useState } from "react";
import { Monitor, Eye, Volume2 } from "lucide-react";
import { Switch } from "@/components/ui/switch";
import { MascotGigi } from "@/components/MascotGigi";
import { useOverlayStyle, type OverlayStyle } from "@/hooks/useOverlayStyle";
import { useBarPersistent } from "@/hooks/useBarPersistent";
import { useMuteMusic } from "@/hooks/useMuteMusic";
import { useEventStore } from "@/store/events";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

/**
 * "Bar & Overlay" group inside the Settings view — the on-screen overlay
 * appearance (Bar / Mascot / None) and two dictation behaviours: show the bar at
 * all times, and mute music while a voice session is active. Moved here from the
 * former standalone Taskbar section; the controls, hooks, and i18n keys
 * (``taskbar_view.*`` + ``settings_view.overlay_style.*``) are unchanged.
 */
export function OverlayTaskbarGroup() {
  const t = useT();

  return (
    <div className="mt-8 space-y-4">
      <h3 className="font-display text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {t("settings_view.overlay_taskbar_group_title")}
      </h3>

      <section>
        <h4 className="mb-2 font-display text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          {t("taskbar_view.appearance_title")}
        </h4>
        <OverlayStylePanel />
      </section>

      <section>
        <h4 className="mb-2 font-display text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          {t("taskbar_view.behavior_title")}
        </h4>
        <div className="overflow-hidden rounded-lg border border-border bg-card/60">
          <BarPersistentRow />
          <div className="mx-4 border-t border-border/60" />
          <MuteMusicRow />
        </div>
      </section>
    </div>
  );
}

/** A label + description on the left, a toggle on the right (Wispr layout). */
function ToggleRow({
  icon: Icon,
  title,
  description,
  checked,
  disabled,
  onToggle,
}: {
  icon: React.ComponentType<{ className?: string }>;
  title: string;
  description: string;
  checked: boolean;
  disabled?: boolean;
  onToggle: (next: boolean) => void;
}) {
  return (
    <div className="flex items-start gap-3 p-4">
      <Icon className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
      <div className="min-w-0 flex-1">
        <div className="font-medium">{title}</div>
        <p className="mt-0.5 text-xs text-muted-foreground">{description}</p>
      </div>
      <Switch checked={checked} disabled={disabled} onCheckedChange={onToggle} />
    </div>
  );
}

function BarPersistentRow() {
  const t = useT();
  const { enabled, loading, setEnabled } = useBarPersistent();
  const pushToast = useEventStore((s) => s.pushToast);
  const [saving, setSaving] = useState(false);

  async function onToggle(next: boolean) {
    setSaving(true);
    try {
      const res = await setEnabled(next);
      pushToast(
        res.applied_live ? "success" : "warning",
        res.applied_live
          ? t("taskbar_view.bar_persistent.saved")
          : t("taskbar_view.restart_required"),
      );
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <ToggleRow
      icon={Eye}
      title={t("taskbar_view.bar_persistent.title")}
      description={t("taskbar_view.bar_persistent.description")}
      checked={enabled ?? true}
      disabled={loading || saving}
      onToggle={onToggle}
    />
  );
}

function MuteMusicRow() {
  const t = useT();
  const { enabled, loading, setEnabled } = useMuteMusic();
  const pushToast = useEventStore((s) => s.pushToast);
  const [saving, setSaving] = useState(false);

  async function onToggle(next: boolean) {
    setSaving(true);
    try {
      await setEnabled(next);
      pushToast(
        "success",
        next
          ? t("taskbar_view.mute_music.enabled_toast")
          : t("taskbar_view.mute_music.disabled_toast"),
      );
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <ToggleRow
      icon={Volume2}
      title={t("taskbar_view.mute_music.title")}
      description={t("taskbar_view.mute_music.description")}
      checked={enabled ?? false}
      disabled={loading || saving}
      onToggle={onToggle}
    />
  );
}

/**
 * On-screen overlay style selector (Bar / Mascot / None). Reuses the
 * settings_view.overlay_style.* i18n. A bar <-> mascot switch cannot apply live
 * (BUG-031: Tcl cross-thread abort), so the app self-restarts to deliver it.
 */
function OverlayStylePanel() {
  const t = useT();
  const { config, loading, error, saveStyle } = useOverlayStyle();
  const pushToast = useEventStore((s) => s.pushToast);
  const [style, setStyle] = useState<OverlayStyle>("whisper_bar");
  const [saving, setSaving] = useState(false);
  const [needsRestart, setNeedsRestart] = useState(false);
  const [restarting, setRestarting] = useState(false);

  useEffect(() => {
    if (config) setStyle(config.style);
  }, [config]);

  // The window goes away on success, so we never clear ``restarting`` there.
  async function onRestartNow() {
    if (restarting) return;
    setRestarting(true);
    try {
      const res = await fetch("/api/settings/restart-app", { method: "POST" });
      if (!res.ok) throw new Error(`restart-failed:${res.status}`);
      pushToast("info", t("taskbar_view.restarting"));
    } catch (e) {
      setRestarting(false);
      pushToast("error", (e as Error).message);
    }
  }

  const options = config?.options ?? (["whisper_bar", "mascot", "none"] as OverlayStyle[]);

  async function onPick(opt: OverlayStyle) {
    if (saving) return;
    setStyle(opt);
    setSaving(true);
    setNeedsRestart(false);
    try {
      const res = await saveStyle(opt);
      if (res.applied_live) {
        pushToast("success", t("settings_view.overlay_style.saved"));
      } else {
        setNeedsRestart(true);
        pushToast("warning", t("settings_view.overlay_style.restart_required"));
      }
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="rounded-lg border border-border bg-card/60 p-4">
      <div className="flex items-start gap-3">
        <Monitor className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
        <div className="min-w-0 flex-1">
          <h4 className="font-display text-sm font-semibold">
            {t("settings_view.overlay_style.title")}
          </h4>
          <p className="mt-1 text-xs text-muted-foreground">
            {t("settings_view.overlay_style.description")}
          </p>

          {/* Visual preview cards — click to apply (no dropdown). */}
          <div className="mt-4 grid grid-cols-3 gap-3">
            {options.map((opt) => (
              <button
                key={opt}
                type="button"
                onClick={() => onPick(opt)}
                disabled={saving || loading}
                aria-pressed={opt === style}
                className={cn(
                  "flex flex-col items-center gap-2 rounded-lg border p-3 transition-all disabled:opacity-60",
                  opt === style
                    ? "border-primary bg-primary/5 ring-1 ring-primary/50"
                    : "border-border bg-background/40 hover:border-primary/50",
                )}
              >
                <div className="flex h-16 w-full items-center justify-center overflow-hidden rounded-md bg-card/80">
                  <StylePreview style={opt} />
                </div>
                <span
                  className={cn(
                    "text-xs font-medium",
                    opt === style ? "text-primary" : "text-muted-foreground",
                  )}
                >
                  {t(`settings_view.overlay_style.options.${opt}`)}
                </span>
              </button>
            ))}
          </div>

          {needsRestart && (
            <div className="mt-3 flex flex-wrap items-center gap-3">
              <p className="text-xs text-amber-500">
                {t("settings_view.overlay_style.restart_required")}
              </p>
              <button
                type="button"
                onClick={onRestartNow}
                disabled={restarting}
                className="rounded-md border border-primary/50 bg-primary/10 px-3 py-1.5 text-xs font-medium text-primary transition-colors hover:bg-primary/20 disabled:opacity-60"
              >
                {restarting
                  ? t("taskbar_view.restarting")
                  : t("taskbar_view.restart_now")}
              </button>
            </div>
          )}
          {error && <p className="mt-3 text-xs text-destructive">{error}</p>}
        </div>
      </div>
    </div>
  );
}

/** Visual preview for each overlay style (mascot reuses the real Gigi SVG). */
function StylePreview({ style }: { style: OverlayStyle }) {
  if (style === "mascot") {
    return <MascotGigi size={46} reactToVoice={false} enableComments={false} />;
  }
  if (style === "whisper_bar") return <BarPreview />;
  return <NonePreview />;
}

function BarPreview() {
  const heights = [6, 11, 15, 8, 14, 9, 7];
  return (
    <svg viewBox="0 0 100 40" className="w-20" aria-hidden="true">
      <rect
        x="6"
        y="11"
        width="88"
        height="18"
        rx="9"
        fill="#0e0d0c"
        stroke="#d7b669"
        strokeWidth="1.6"
      />
      {heights.map((h, i) => (
        <rect
          key={i}
          x={24 + i * 8}
          y={20 - h / 2}
          width="3"
          height={h}
          rx="1.5"
          fill="#e7c46e"
        />
      ))}
    </svg>
  );
}

export function NonePreview() {
  return (
    <svg viewBox="0 0 100 40" className="w-20 opacity-50" aria-hidden="true">
      <rect
        x="6"
        y="11"
        width="88"
        height="18"
        rx="9"
        fill="none"
        stroke="#7c766b"
        strokeWidth="1.6"
        strokeDasharray="4 3"
      />
      {/* Diagonal "disabled" strike — kept inside the dashed box (y 11..29)
          and symmetric about its centre (50, 20) so it never juts out as a
          stub above/below the pill. */}
      <line x1="25" y1="25" x2="75" y2="15" stroke="#7c766b" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}
