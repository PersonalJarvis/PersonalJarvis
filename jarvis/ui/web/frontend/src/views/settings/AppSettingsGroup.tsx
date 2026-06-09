import { useState } from "react";
import { Power, Zap } from "lucide-react";
import { Switch } from "@/components/ui/switch";
import { useAutostart } from "@/hooks/useAutostart";
import { useEventStore } from "@/store/events";
import { useT } from "@/i18n";

/**
 * "App settings" group inside the Settings view. Currently hosts the
 * login-autostart toggle ("Launch app at login"). Flipping it installs/removes
 * the OS autostart entry live — Windows ``.lnk`` / macOS LaunchAgent / Linux XDG
 * ``.desktop`` — and persists ``[autostart].enabled`` to jarvis.toml. The entry
 * launches the full desktop app (voice + Orb), so "Hey Jarvis" works right after
 * a reboot.
 *
 * On a headless host (no display) ``supported`` is false: the switch is disabled
 * with an honest caption, because there is no GUI login session to autostart
 * into. The toggle still persists the intent in that case (it just cannot create
 * an OS entry there).
 */
export function AppSettingsGroup() {
  const t = useT();

  return (
    <div className="mt-8 space-y-4">
      <h3 className="font-display text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {t("settings_view.app_settings_group_title")}
      </h3>
      <AutostartRow />
    </div>
  );
}

/**
 * Label + description on the left, a toggle on the right (Wispr layout). The
 * switch reflects the backend's authoritative ``enabled`` once GET resolves and
 * is disabled while loading/saving or on an unsupported host.
 */
function AutostartRow() {
  const t = useT();
  const { config, loading, error, setEnabled } = useAutostart();
  const pushToast = useEventStore((s) => s.pushToast);
  const [saving, setSaving] = useState(false);

  const supported = config?.supported ?? true;
  const enabled = config?.enabled ?? false;
  // Windows-only: the throttled .lnk fallback is active and can be upgraded to a
  // logon scheduled task (instant start) via a one-time permission prompt.
  const canUpgradeInstantStart =
    supported &&
    enabled &&
    config?.platform === "win32" &&
    config?.mechanism === "shortcut";
  const instantStartActive =
    config?.platform === "win32" && config?.mechanism === "scheduled_task";

  async function onToggle(next: boolean) {
    setSaving(true);
    try {
      const res = await setEnabled(next);
      if (next && res.supported && res.applied_live) {
        pushToast("success", t("settings_view.autostart.enabled_toast"));
      } else if (next && !res.supported) {
        pushToast("warning", res.detail || t("settings_view.autostart.unsupported"));
      } else if (!next) {
        pushToast("success", t("settings_view.autostart.disabled_toast"));
      }
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  // Re-apply with enabled=true → on Windows this registers the scheduled task via
  // a one-time UAC prompt and drops the fallback shortcut.
  async function onEnableInstantStart() {
    setSaving(true);
    try {
      const res = await setEnabled(true);
      if (res.mechanism === "scheduled_task") {
        pushToast("success", t("settings_view.autostart.instant_start_enabled_toast"));
      } else {
        pushToast("warning", t("settings_view.autostart.instant_start_declined_toast"));
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
        <Power className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-4">
            <h4 className="font-medium">{t("settings_view.autostart.title")}</h4>
            <Switch
              checked={enabled}
              disabled={loading || saving || !supported}
              onCheckedChange={onToggle}
            />
          </div>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {t("settings_view.autostart.description")}
          </p>

          {error && <p className="mt-3 text-xs text-destructive">{error}</p>}

          {!supported && !loading && (
            <p className="mt-3 text-xs text-amber-500">
              {config?.detail || t("settings_view.autostart.unsupported")}
            </p>
          )}

          {instantStartActive && (
            <p className="mt-3 text-xs text-emerald-500">
              {t("settings_view.autostart.instant_start_active")}
            </p>
          )}

          {canUpgradeInstantStart && (
            <div className="mt-3 rounded-md border border-border bg-background/50 p-3">
              <p className="text-xs text-muted-foreground">
                {t("settings_view.autostart.instant_start_hint")}
              </p>
              <button
                type="button"
                disabled={saving}
                onClick={onEnableInstantStart}
                className="mt-2 inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
              >
                <Zap className="h-3.5 w-3.5" />
                {t("settings_view.autostart.enable_instant_start")}
              </button>
            </div>
          )}

          {supported && config?.entry_path && (
            <p className="mt-2 break-all font-mono text-[11px] text-muted-foreground">
              {config.entry_path}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
