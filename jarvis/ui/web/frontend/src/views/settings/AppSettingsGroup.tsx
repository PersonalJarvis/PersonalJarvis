import { useState, type ReactNode } from "react";
import { Monitor, Moon, Sun, Zap } from "lucide-react";
import { SETUP_REPLAY_EVENT } from "@/components/onboarding/tourEvents";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { useAutostart } from "@/hooks/useAutostart";
import { useBackgroundAgents, type BackgroundAgentsPatch } from "@/hooks/useBackgroundAgents";
import { useTheme, type ThemePreference } from "@/hooks/useTheme";
import { useEventStore } from "@/store/events";
import { fill, useLocaleChunk, useT } from "@/i18n";
import {
  SettingsCard,
  SettingsNote,
  SettingsRow,
  SettingsSection,
  SettingsSegmented,
} from "@/views/settings/SettingsLayout";

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
export function AppSettingsGroup({ children }: { children?: ReactNode } = {}) {
  const t = useT();

  return (
    <SettingsSection title={t("settings_view.app_settings_group_title")}>
      <SettingsCard>
        <AppearanceRow />
        <AutostartRow />
        <BackgroundAgentsRows />
        {children}
        <TourRow />
      </SettingsCard>
    </SettingsSection>
  );
}

/**
 * Replays the onboarding — the setup window, then the walk through the app —
 * as a preview that restarts nothing. The strings live in the onboarding
 * locale chunk, which this row loads for itself.
 */
function TourRow() {
  const t = useT();
  const ready = useLocaleChunk("onboarding");
  if (!ready) return null;
  return (
    <SettingsRow
      title={t("app_tour.replay_title")}
      description={t("app_tour.replay_body")}
      control={
        <Button
          size="sm"
          variant="secondary"
          onClick={() => window.dispatchEvent(new CustomEvent(SETUP_REPLAY_EVENT))}
          data-testid="settings-replay-tour"
        >
          {t("app_tour.replay")}
        </Button>
      }
    />
  );
}

const THEME_OPTIONS: ReadonlyArray<{
  value: ThemePreference;
  icon: typeof Sun;
  labelKey: string;
}> = [
  { value: "dark", icon: Moon, labelKey: "settings_view.appearance.dark" },
  { value: "light", icon: Sun, labelKey: "settings_view.appearance.light" },
  { value: "system", icon: Monitor, labelKey: "settings_view.appearance.system" },
];

/**
 * Colour theme picker — three exclusive choices rather than a dark/light switch,
 * because "follow the system" is a third state a toggle cannot express.
 *
 * The repaint is instant and local (ThemeProvider writes the class on <html>);
 * the PUT that persists it to ``[ui] theme`` runs in the background. So the
 * control never feels like it is waiting on the network, and a failed write
 * costs the user only the persistence, not the switch.
 */
function AppearanceRow() {
  const t = useT();
  const { preference, theme, setPreference } = useTheme();

  return (
    <SettingsRow
      title={t("settings_view.appearance.title")}
      description={
        preference === "system"
          ? `${t("settings_view.appearance.description")} ${t(
              theme === "dark"
                ? "settings_view.appearance.system_now_dark"
                : "settings_view.appearance.system_now_light",
            )}`
          : t("settings_view.appearance.description")
      }
      control={
        <SettingsSegmented
          value={preference}
          onChange={setPreference}
          ariaLabel={t("settings_view.appearance.title")}
          options={THEME_OPTIONS.map(({ value, icon: Icon, labelKey }) => ({
            value,
            label: t(labelKey),
            icon: <Icon aria-hidden />,
          }))}
        />
      }
    />
  );
}

/**
 * Label + description on the left, a toggle on the right (grouped-card layout). The
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

  const hasDetails = Boolean(
    error ||
      (!supported && !loading) ||
      instantStartActive ||
      canUpgradeInstantStart ||
      (supported && config?.entry_path),
  );

  return (
    <SettingsRow
      title={t("settings_view.autostart.title")}
      description={t("settings_view.autostart.description")}
      control={
        <Switch
          checked={enabled}
          disabled={loading || saving || !supported}
          aria-label={t("settings_view.autostart.title")}
          onCheckedChange={onToggle}
        />
      }
    >
      {hasDetails && (
        <>
          {error && <p className="text-sm text-destructive">{error}</p>}

          {!supported && !loading && (
            <p className="text-sm text-foreground">
              {config?.detail || t("settings_view.autostart.unsupported")}
            </p>
          )}

          {instantStartActive && (
            <p className="text-sm text-muted-foreground">
              {t("settings_view.autostart.instant_start_active")}
            </p>
          )}

          {canUpgradeInstantStart && (
            <SettingsNote className="flex flex-wrap items-center justify-between gap-3">
              <span className="min-w-0 flex-1 text-muted-foreground">
                {t("settings_view.autostart.instant_start_hint")}
              </span>
              <Button size="sm" disabled={saving} onClick={onEnableInstantStart}>
                <Zap aria-hidden />
                {t("settings_view.autostart.enable_instant_start")}
              </Button>
            </SettingsNote>
          )}

          {supported && config?.entry_path && (
            <p className="break-all font-mono text-xs text-muted-foreground">
              {config.entry_path}
            </p>
          )}
        </>
      )}
    </SettingsRow>
  );
}

const CHANNEL_LABELS: Record<string, string> = { telegram: "Telegram", discord: "Discord" };

/**
 * "Keep agents running after closing": on quit the app hands routines and chat
 * channels to a windowless background service, which hands them back when the
 * app opens again. The second row makes the login entry start only that
 * service. The line under the first says what a quit would keep right now.
 */
function BackgroundAgentsRows() {
  const t = useT();
  const { config, loading, error, save } = useBackgroundAgents();
  const pushToast = useEventStore((s) => s.pushToast);
  const [saving, setSaving] = useState(false);

  if (config && !config.supported) return null;

  async function apply(patch: BackgroundAgentsPatch) {
    setSaving(true);
    try {
      await save(patch);
      pushToast("success", t("settings_view.background_agents.saved_toast"));
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  const keep = config?.keep_agents_running ?? true;
  const work = config?.work;
  const parts: string[] = [];
  if (work?.routines) {
    parts.push(fill(t("settings_view.background_agents.routines"), { count: work.routines }));
  }
  for (const channel of work?.channels ?? []) parts.push(CHANNEL_LABELS[channel] ?? channel);

  return (
    <>
      <SettingsRow
        title={t("settings_view.background_agents.title")}
        description={t("settings_view.background_agents.description")}
        control={
          <Switch
            checked={keep}
            disabled={loading || saving}
            aria-label={t("settings_view.background_agents.title")}
            onCheckedChange={(next) => void apply({ keep_agents_running: next })}
          />
        }
      >
        {(error || config?.running_as_service || (config && keep)) && (
          <>
            {error && <p className="text-sm text-destructive">{error}</p>}

            {config?.running_as_service && (
              <p className="text-sm text-foreground">
                {t("settings_view.background_agents.service_active")}
              </p>
            )}

            {config && keep && !config.running_as_service && (
              <p className="text-sm text-muted-foreground">
                {parts.length > 0
                  ? fill(t("settings_view.background_agents.keeps_now"), { what: parts.join(", ") })
                  : t("settings_view.background_agents.keeps_nothing")}
              </p>
            )}
          </>
        )}
      </SettingsRow>
      <SettingsRow
        title={t("settings_view.background_agents.login_title")}
        description={t("settings_view.background_agents.login_description")}
        control={
          <Switch
            checked={config?.background_only_at_login ?? false}
            disabled={loading || saving || !config?.autostart_enabled}
            aria-label={t("settings_view.background_agents.login_title")}
            onCheckedChange={(next) => void apply({ background_only_at_login: next })}
          />
        }
      />
    </>
  );
}
