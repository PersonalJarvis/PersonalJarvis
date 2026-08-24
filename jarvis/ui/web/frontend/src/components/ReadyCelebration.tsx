import { useCallback, useEffect, useRef, useState } from "react";
import { Sparkles, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { getReadiness, markReadyCelebrated, type ReadinessResponse } from "@/hooks/useStarterPlans";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

/**
 * The one-time "all lights green" note.
 *
 * Shown exactly once: the first time every section the active voice mode
 * needs answers `ok` in the section-health rollup (Pipeline: brain, tool
 * model, voice out, voice in — Realtime: live voice, tool model, agents).
 * The backend remembers the moment, so it never reappears — not after a
 * restart, not when a key is rotated later. Inline inside onboarding, a
 * banner above the top bar afterwards; same component, same truth.
 */
const REFRESH_EVENTS = [
  "jarvis:secret-configured",
  "jarvis:brain-switched",
  "jarvis:tts-switched",
  "jarvis:stt-switched",
  "jarvis:realtime-switched",
  "jarvis:computer-use-switched",
  "jarvis:subagent-switched",
  "jarvis:agent-switched",
  "jarvis:provider-tested",
  "jarvis:provider-config-changed",
];

export function ReadyCelebration({ inline = false }: { inline?: boolean }) {
  const t = useT();
  const [state, setState] = useState<ReadinessResponse | null>(null);
  const [dismissed, setDismissed] = useState(false);
  const version = useRef(0);

  const reload = useCallback(async (refresh: boolean) => {
    const mine = ++version.current;
    try {
      const next = await getReadiness(refresh);
      if (mine === version.current) setState(next);
    } catch {
      // Advisory only — a failed probe simply shows nothing.
    }
  }, []);

  useEffect(() => {
    void reload(false);
    let timer: number | undefined;
    const onChange = () => {
      window.clearTimeout(timer);
      // The section-health refresh runs real probes; collapse bursts.
      timer = window.setTimeout(() => void reload(true), 600);
    };
    REFRESH_EVENTS.forEach((e) => window.addEventListener(e, onChange));
    return () => {
      ++version.current;
      window.clearTimeout(timer);
      REFRESH_EVENTS.forEach((e) => window.removeEventListener(e, onChange));
    };
  }, [reload]);

  const visible = Boolean(state?.ready && !state?.celebrated) && !dismissed;
  if (!visible || !state) return null;

  async function acknowledge() {
    setDismissed(true);
    try {
      await markReadyCelebrated();
    } catch {
      // Worst case the note shows once more after a restart.
    }
  }

  const modeLabel =
    state.mode === "realtime"
      ? t("ready_note.mode_realtime")
      : t("ready_note.mode_pipeline");

  return (
    <div
      role="status"
      data-testid="ready-celebration"
      className={cn(
        "flex items-start gap-3 border-primary/60 bg-primary/[0.06] text-foreground",
        inline
          ? "rounded-control border px-4 py-3"
          : "border-b px-5 py-2.5",
      )}
    >
      <Sparkles aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
      <div className="min-w-0 flex-1 space-y-0.5">
        <p className="text-sm font-medium">{t("ready_note.title")}</p>
        <p className="text-[13px] leading-relaxed text-muted-foreground">
          {t("ready_note.body").replace("{0}", modeLabel)}
        </p>
      </div>
      <Button size="sm" onClick={() => void acknowledge()} className="h-8 shrink-0 rounded-control">
        {t("ready_note.go")}
      </Button>
      {!inline && (
        <button
          type="button"
          onClick={() => void acknowledge()}
          aria-label={t("common.close")}
          className="shrink-0 text-muted-foreground hover:text-foreground"
        >
          <X className="h-4 w-4" />
        </button>
      )}
    </div>
  );
}
