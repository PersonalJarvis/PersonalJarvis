import { useEffect, useState } from "react";
import { PawPrint } from "lucide-react";
import { Switch } from "@/components/ui/switch";
import {
  useOverlayStyle,
  OVERLAY_STYLES,
  type OverlayStyle,
} from "@/hooks/useOverlayStyle";
import { StylePreview } from "@/components/overlay/OverlayStylePreviews";
import { useBarPersistent } from "@/hooks/useBarPersistent";
import { useBarFollowCursor } from "@/hooks/useBarFollowCursor";
import { BarSizeGroup } from "@/views/settings/BarSizeGroup";
import { useMuteMusic } from "@/hooks/useMuteMusic";
import { useSoundEffects } from "@/hooks/useSoundEffects";
import { useRestartApp } from "@/hooks/useRestartApp";
import { useEventStore } from "@/store/events";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { SettingsCard, SettingsRow, SettingsSection } from "@/views/settings/SettingsLayout";

/**
 * "Bar & Overlay" group inside the Settings view — the on-screen overlay
 * appearance (Bar / Mascot / Voice orb / Pet / None) and two dictation behaviours: show the bar at
 * all times, and mute music while a voice session is active. Moved here from the
 * former standalone Taskbar section; the controls, hooks, and i18n keys
 * (``taskbar_view.*`` + ``settings_view.overlay_style.*``) are unchanged.
 */
export function OverlayTaskbarGroup() {
  const t = useT();
  const overlay = useOverlayStyle();
  const isBar = (overlay.config?.style ?? "jarvis_bar") === "jarvis_bar";

  return (
    <SettingsSection title={t("settings_view.overlay_taskbar_group_title")}>
      <SettingsCard>
        <OverlayStylePanel overlay={overlay} />
        {isBar && <BarSizeGroup />}
      </SettingsCard>
      <SettingsCard>
        {isBar && <>
          <BarPersistentRow />
          <FollowCursorRow />
        </>}
        <MuteMusicRow />
        <SoundEffectsRow />
      </SettingsCard>
    </SettingsSection>
  );
}

/** A label + description on the left, a toggle on the right. */
function ToggleRow({
  title,
  description,
  checked,
  disabled,
  onToggle,
}: {
  title: string;
  description: string;
  checked: boolean;
  disabled?: boolean;
  onToggle: (next: boolean) => void;
}) {
  return (
    <SettingsRow
      title={title}
      description={description}
      control={
        <Switch
          checked={checked}
          disabled={disabled}
          aria-label={title}
          onCheckedChange={onToggle}
        />
      }
    />
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
      title={t("taskbar_view.sound_effects.title")}
      description={t("taskbar_view.sound_effects.description")}
      checked={enabled ?? true}
      disabled={loading || saving}
      onToggle={onToggle}
    />
  );
}

/**
 * On-screen overlay style selector (Bar / Mascot / Voice orb / Pet / None).
 * Reuses the settings_view.overlay_style.* i18n. A switch between the bar and
 * an orb-window style cannot apply live (BUG-031: Tcl cross-thread abort), so
 * the app self-restarts to deliver it (`useRestartApp`).
 */
function OverlayStylePanel({ overlay }: { overlay: ReturnType<typeof useOverlayStyle> }) {
  const t = useT();
  const { config, loading, error, saveStyle } = overlay;
  const pushToast = useEventStore((s) => s.pushToast);
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const [style, setStyle] = useState<OverlayStyle>("jarvis_bar");
  const [saving, setSaving] = useState(false);
  const [needsRestart, setNeedsRestart] = useState(false);
  const { restart, restarting, buttonLabel: restartLabel } = useRestartApp();

  useEffect(() => {
    if (config) setStyle(config.style);
  }, [config]);

  const options = config?.options ?? OVERLAY_STYLES;

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
      setStyle(config?.style ?? "jarvis_bar");
      pushToast("error", (e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <SettingsRow
      title={t("settings_view.overlay_style.title")}
      description={t("settings_view.overlay_style.description")}
    >
      {/* Visual preview cards — click to apply (no dropdown). They wrap on a
          narrow window so a fifth style never squeezes the previews into
          unreadable slivers. */}
      <div className="grid grid-cols-[repeat(auto-fit,minmax(110px,1fr))] gap-2.5 pt-1">
        {options.map((opt) => {
          const active = opt === style;
          return (
            <button
              key={opt}
              type="button"
              onClick={() => onPick(opt)}
              disabled={saving || loading}
              aria-pressed={active}
              className={cn(
                "flex flex-col items-center gap-2 rounded-md border p-2 transition-colors disabled:opacity-60",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                active
                  ? "border-accent bg-accent-soft"
                  : "border-border hover:border-border-strong hover:bg-secondary",
              )}
            >
              <div className="flex h-16 w-full items-center justify-center overflow-hidden rounded bg-background">
                <StylePreview style={opt} />
              </div>
              <span
                className={cn(
                  "text-sm font-medium",
                  active ? "text-foreground-strong" : "text-muted-foreground",
                )}
              >
                {t(`settings_view.overlay_style.options.${opt}`)}
              </span>
            </button>
          );
        })}
      </div>

      {/* Which pet, its size and its bubble live on their own page. */}
      {style === "pet" && (
        <button
          type="button"
          data-testid="overlay-style-open-pets"
          onClick={() => setActiveSection("pets")}
          className="inline-flex items-center gap-1.5 text-sm font-medium text-accent hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <PawPrint className="h-3.5 w-3.5" aria-hidden />
          {t("pets.open_settings")}
        </button>
      )}

      {needsRestart && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-md bg-secondary px-3 py-2.5">
          <p className="text-sm text-foreground">
            {t("settings_view.overlay_style.restart_required")}
          </p>
          <Button size="sm" variant="secondary" onClick={() => void restart()} disabled={restarting}>
            {restartLabel}
          </Button>
        </div>
      )}
      {error && <p className="text-sm text-destructive">{error}</p>}
    </SettingsRow>
  );
}
