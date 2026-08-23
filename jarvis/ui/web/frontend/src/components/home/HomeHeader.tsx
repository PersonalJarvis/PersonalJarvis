import { useEventStore } from "@/store/events";
import { useVoiceEngineDisplay } from "@/hooks/useVoiceEngineDisplay";
import { useVoiceReadiness } from "@/hooks/useVoiceReadiness";
import { useVoiceMode } from "@/hooks/useVoiceMode";
import { TopBarActions } from "@/components/layout/TopBar";
import { CodingModeBadge } from "@/components/layout/CodingModeBadge";
import { MascotGigi } from "@/components/MascotGigi";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

/**
 * The front page's header row: who is talking (mascot, name, voice state),
 * what will answer (engine + model — click to change), and the app chrome
 * actions (coding mode, theme, own window, update, restart).
 *
 * Same content the mission deck's header carried, in a calmer frame: plain
 * pills instead of instrument readouts. The shell TopBar steps aside on this
 * section (TopBar.tsx), so this row is the only chrome above the stage.
 */
export function HomeHeader() {
  const t = useT();
  const assistantName = useEventStore((s) => s.assistantName);
  const voiceState = useEventStore((s) => s.voiceState);
  const setActive = useEventStore((s) => s.setActiveSection);
  const { connected, warming, bootWarming, voiceWarming } = useVoiceReadiness();
  const voiceMode = useVoiceMode();
  const engine = useVoiceEngineDisplay();

  const stateLabel = !connected
    ? bootWarming
      ? t("voice_state.booting")
      : t("voice_state.offline")
    : voiceWarming
      ? t("voice_state.starting")
      : voiceMode.connecting
        ? t("voice_state.connecting")
        : t(`voice_state.${voiceState}`);
  const live =
    connected && !warming && (voiceState === "listening" || voiceState === "thinking" || voiceState === "speaking");

  return (
    <header
      data-testid="home-header"
      className="flex h-12 shrink-0 items-center gap-3 border-b border-border px-4"
    >
      <span className="flex h-7 w-7 shrink-0 items-center justify-center" aria-hidden>
        <MascotGigi size={28} reactToVoice enableComments={false} />
      </span>
      <div className="flex min-w-0 items-baseline gap-2">
        <span className="font-display text-sm font-semibold tracking-tight">{assistantName}</span>
        <span
          data-testid="home-voice-state"
          className={cn(
            "truncate font-mono text-[10px] uppercase tracking-[0.14em]",
            live ? "text-primary" : "text-muted-foreground",
          )}
        >
          {stateLabel}
        </span>
      </div>

      <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
        <HeaderPill
          label={t(engine.tier === "realtime" ? "deck.stat_realtime" : "deck.stat_brain")}
          value={engine.providerLabel}
          hot
          onClick={() => setActive("apikeys")}
          testId="home-stat-engine"
        />
        <HeaderPill
          label={t("deck.stat_model")}
          value={engine.model || "—"}
          onClick={() => setActive("apikeys")}
          testId="home-stat-model"
        />
        <CodingModeBadge />
        <TopBarActions />
      </div>
    </header>
  );
}

function HeaderPill({
  label,
  value,
  hot = false,
  onClick,
  testId,
}: {
  label: string;
  value: string;
  hot?: boolean;
  onClick: () => void;
  testId: string;
}) {
  const t = useT();
  return (
    <button
      type="button"
      onClick={onClick}
      title={t("home.model_hint")}
      data-testid={testId}
      className="inline-flex h-7 max-w-[260px] items-center gap-1.5 rounded-md border border-border bg-secondary/40 px-2 text-xs transition-colors hover:border-primary/50"
    >
      <span className="font-mono text-[9px] uppercase tracking-[0.14em] text-muted-foreground">
        {label}
      </span>
      <span className={cn("truncate font-medium", hot ? "text-primary" : "text-foreground")}>
        {value}
      </span>
    </button>
  );
}
