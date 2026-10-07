import { useT } from "@/i18n";
import { useEventStore } from "@/store/events";
import { useMusicSettings } from "@/hooks/useMusicSettings";
import {
  MUSIC_PLAYBACK_MODES,
  MUSIC_SERVICES,
  type MusicPlaybackMode,
  type MusicService,
} from "@/lib/musicSettings";
import {
  SettingsCard,
  SettingsRow,
  SettingsSection,
  SettingsSelect,
} from "@/views/settings/SettingsLayout";

/**
 * "Music" group inside the Settings view — two connectors, one domain.
 *
 * Which service a request that names no service goes to (Spotify vs YouTube
 * Music), and where YouTube Music plays (the background player window or the
 * browser). One row per choice with its dropdown on the right; both option lists
 * are the frozen TS mirror of `music_constants.py`, and the row descriptions
 * say which connectors are actually connected so a choice never looks live
 * when it would do nothing.
 */
export function MusicGroup() {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const { settings, loading, save } = useMusicSettings();

  const connected = new Set(settings?.connected ?? []);
  const playerAvailable = settings?.background_player_available ?? true;

  const choose = async (patch: {
    preferred_service?: MusicService;
    playback?: MusicPlaybackMode;
  }) => {
    try {
      await save(patch);
      pushToast("success", t("settings_view.music.saved_toast"));
    } catch (e) {
      pushToast("error", (e as Error).message);
    }
  };

  const serviceDescription = (service: MusicService): string => {
    if (service === "auto") return t("settings_view.music.service_options.auto");
    const state = connected.has(service)
      ? t("settings_view.music.connected")
      : t("settings_view.music.not_connected");
    return `${t(`settings_view.music.service_options.${service}`)} — ${state}`;
  };

  const playbackDescription = (mode: MusicPlaybackMode): string => {
    const base = t(`settings_view.music.playback_options.${mode}`);
    if (mode === "background" && !playerAvailable) {
      return `${base} — ${t("settings_view.music.player_unavailable")}`;
    }
    return base;
  };

  return (
    <SettingsSection title={t("settings_view.music_group_title")}>
      <SettingsCard>
        <SettingsRow
          title={t("settings_view.music.service_section")}
          description={t("settings_view.music.service_hint")}
          control={
            <SettingsSelect
              value={settings?.preferred_service ?? "auto"}
              disabled={loading}
              testId="music-service"
              ariaLabel={t("settings_view.music.service_section")}
              onValueChange={(service) => void choose({ preferred_service: service as MusicService })}
              options={MUSIC_SERVICES.map((service) => ({
                value: service,
                label: t(`settings_view.music.service_labels.${service}`),
                description: serviceDescription(service),
              }))}
            />
          }
        />
        <SettingsRow
          title={t("settings_view.music.playback_section")}
          description={t("settings_view.music.playback_hint")}
          control={
            <SettingsSelect
              value={settings?.playback ?? "background"}
              disabled={loading}
              testId="music-playback"
              ariaLabel={t("settings_view.music.playback_section")}
              onValueChange={(mode) => void choose({ playback: mode as MusicPlaybackMode })}
              options={MUSIC_PLAYBACK_MODES.map((mode) => ({
                value: mode,
                label: t(`settings_view.music.playback_labels.${mode}`),
                description: playbackDescription(mode),
              }))}
            />
          }
        />
      </SettingsCard>
    </SettingsSection>
  );
}
