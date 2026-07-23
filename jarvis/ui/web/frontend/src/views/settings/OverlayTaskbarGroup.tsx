import { useEffect, useState } from "react";
import { Monitor, Eye, Volume2, Bell, MousePointer } from "lucide-react";
import { Switch } from "@/components/ui/switch";
import { useOverlayStyle, type OverlayStyle } from "@/hooks/useOverlayStyle";
import { StylePreview } from "@/components/overlay/OverlayStylePreviews";
import { useBarPersistent } from "@/hooks/useBarPersistent";
import { useBarFollowCursor } from "@/hooks/useBarFollowCursor";
import { BarSizeGroup } from "@/views/settings/BarSizeGroup";
import { useMuteMusic } from "@/hooks/useMuteMusic";
import { useSoundEffects } from "@/hooks/useSoundEffects";
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
        <BarSizeGroup />
      </section>

      <section>
        <h4 className="mb-2 font-display text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          {t("taskbar_view.behavior_title")}
        </h4>
        <div className="overflow-hidden rounded-lg border border-border bg-card/60">
          <BarPersistentRow />
          <div className="mx-4 border-t border-border/60" />
          <FollowCursorRow />
          <div className="mx-4 border-t border-border/60" />
          <MuteMusicRow />
          <div className="mx-4 border-t border-border/60" />
          <SoundEffectsRow />
        </div>
      </section>
    </div>
  );
}

/** A label + description on the left, a toggle on the right (grouped-card layout). */
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

function FollowCursorRow() {
  const t = useT();
  const { enabled, loading, setEnabled } = useBarFollowCursor();
  const pushToast = useEventStore((s) => s.pushToast);
  const [saving, setSaving] = useState(false);

  async function onToggle(next: boolean) {
    setSaving(true);
    try {
      const res = await setEnabled(next);
      pushToast(
        res.applied_live ? "success" : "warning",
        res.applied_live
          ? t("taskbar_view.follow_cursor.saved")
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
      icon={MousePointer}
      title={t("taskbar_view.follow_cursor.title")}
      description={t("taskbar_view.follow_cursor.description")}
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

function SoundEffectsRow() {
  const t = useT();
  const { enabled, loading, setEnabled } = useSoundEffects();
  const pushToast = useEventStore((s) => s.pushToast);
  const [saving, setSaving] = useState(false);

  async function onToggle(next: boolean) {
    setSaving(true);
    try {
      await setEnabled(next);
      pushToast(
        "success",
        next
          ? t("taskbar_view.sound_effects.enabled_toast")
          : t("taskbar_view.sound_effects.disabled_toast"),
      );
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <ToggleRow
      icon={Bell}
      title={t("taskbar_view.sound_effects.title")}
      description={t("taskbar_view.sound_effects.description")}
      checked={enabled ?? true}
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
  const [style, setStyle] = useState<OverlayStyle>("jarvis_bar");
  const [saving, setSaving] = useState(false);
  const [needsRestart, setNeedsRestart] = useState(false);
  const [restarting, setRestarting] = useState(false);
  // Armed after the backend refuses the restart (HTTP 409) because missions are
  // running; the next click resends with ``force=true``.
  const [forceArmed, setForceArmed] = useState(false);

  useEffect(() => {
    if (config) setStyle(config.style);
  }, [config]);

  // The window goes away on success, so we never clear ``restarting`` there.
  async function onRestartNow(force: boolean) {
    if (restarting) return;
    setRestarting(true);
    try {
      const url = force
        ? "/api/settings/restart-app?force=true"
        : "/api/settings/restart-app";
      const res = await fetch(url, { method: "POST" });
      if (res.status === 409) {
        // Live missions would be killed — surface the count and arm a force
        // restart instead of killing them silently.
        let count = 0;
        try {
          const body = await res.json();
          count = body?.detail?.missions?.length ?? 0;
        } catch {
          /* malformed body — still arm the override */
        }
        setRestarting(false);
        setForceArmed(true);
        pushToast("warning", `${count} ${t("topbar.restart_missions_running")}`);
        return;
      }
      if (!res.ok) throw new Error(`restart-failed:${res.status}`);
      pushToast("info", t("taskbar_view.restarting"));
    } catch (e) {
      setRestarting(false);
      pushToast("error", (e as Error).message);
    }
  }

  const options = config?.options ?? (["jarvis_bar", "mascot", "none"] as OverlayStyle[]);

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
                onClick={() => onRestartNow(forceArmed)}
                disabled={restarting}
                className="rounded-md border border-primary/50 bg-primary/10 px-3 py-1.5 text-xs font-medium text-primary transition-colors hover:bg-primary/20 disabled:opacity-60"
              >
                {restarting
                  ? t("taskbar_view.restarting")
                  : forceArmed
                    ? t("topbar.restart_force")
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
