import { Check } from "lucide-react";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";
import { useEventStore } from "@/store/events";
import { useMusicSettings } from "@/hooks/useMusicSettings";
import {
  MUSIC_PLAYBACK_MODES,
  MUSIC_SERVICES,
  type MusicPlaybackMode,
  type MusicService,
} from "@/lib/musicSettings";

/**
 * "Music" group inside the Settings view — two connectors, one domain.
 *
 * Which service a request that names no service goes to (Spotify vs YouTube
 * Music), and where YouTube Music plays (the background player window or the
 * browser). Same row-button idiom as the Languages group; both option lists
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
    <div className="mb-8 space-y-4">
      <h3 className="font-display text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {t("settings_view.music_group_title")}
      </h3>

      <Section
        title={t("settings_view.music.service_section")}
        hint={t("settings_view.music.service_hint")}
      >
        {MUSIC_SERVICES.map((service) => (
          <ChoiceRow
            key={`service-${service}`}
            active={settings?.preferred_service === service}
            disabled={loading}
            label={t(`settings_view.music.service_labels.${service}`)}
            description={serviceDescription(service)}
            onClick={() => void choose({ preferred_service: service })}
          />
        ))}
      </Section>

      <Section
        title={t("settings_view.music.playback_section")}
        hint={t("settings_view.music.playback_hint")}
      >
        {MUSIC_PLAYBACK_MODES.map((mode) => (
          <ChoiceRow
            key={`playback-${mode}`}
            active={settings?.playback === mode}
            disabled={loading}
            label={t(`settings_view.music.playback_labels.${mode}`)}
            description={playbackDescription(mode)}
            onClick={() => void choose({ playback: mode })}
          />
        ))}
      </Section>
    </div>
  );
}

function Section({
  title,
  hint,
  children,
}: {
  title: string;
  hint: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div className="mb-1 text-[10px] uppercase tracking-wider text-muted-foreground">
        {title}
      </div>
      <div className="mb-3 text-xs text-muted-foreground">{hint}</div>
      <ul className="space-y-2">{children}</ul>
    </div>
  );
}

function ChoiceRow({
  active,
  disabled,
  label,
  description,
  onClick,
}: {
  active: boolean;
  disabled: boolean;
  label: string;
  description: string;
  onClick: () => void;
}) {
  return (
    <li>
      <button
        type="button"
        onClick={onClick}
        disabled={disabled}
        aria-pressed={active}
        className={cn(
          "flex w-full items-center gap-3 rounded-lg border px-4 py-3 text-left text-sm transition-colors disabled:opacity-60",
          active
            ? "border-primary/40 bg-primary/5 shadow-[0_0_0_1px_hsl(var(--primary)/0.15)]"
            : "border-border bg-card/60 hover:border-primary/30 hover:bg-card/80",
        )}
      >
        <div className="flex-1">
          <div className="font-medium">{label}</div>
          <div className="mt-0.5 text-xs text-muted-foreground">{description}</div>
        </div>
        {active && <Check className="h-4 w-4 shrink-0 text-primary" />}
      </button>
    </li>
  );
}
