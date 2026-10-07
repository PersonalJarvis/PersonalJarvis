import { useEffect, useId, useState } from "react";
import { Keyboard, Loader2, Mic, Square } from "lucide-react";

import { Button } from "@/components/ui/button";
import { SkeletonBar } from "@/components/layout/PanelSkeleton";
import type { DictationStatus } from "@/hooks/useDictation";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { formatCombo } from "@/views/settings/KeybindRow";
import { KeyCombo, StatusDot } from "@/views/voice/voiceUi";

/**
 * The top of the Dictation tab: the one instruction a person needs ("hold
 * these keys and speak"), the live state, and the one action on the screen.
 *
 * The push-to-talk keys are drawn as large keycaps inside the sentence, so
 * the instruction and the key read as one thing. While recording, the state
 * line pulses in the accent and counts the seconds; nothing else moves.
 */
export interface DictationControlPanelProps {
  status: DictationStatus | null;
  loading: boolean;
  /** A start/stop request is in flight. */
  busy: boolean;
  /** Already-localized greeting line ("Welcome back, Ada"). */
  greeting: string;
  onToggle: () => void;
  onOpenShortcuts: () => void;
}

type StateTone = "ready" | "live" | "warning" | "error" | "idle";

export function DictationControlPanel({
  status,
  loading,
  busy,
  greeting,
  onToggle,
  onOpenShortcuts,
}: DictationControlPanelProps) {
  const t = useT();
  const headingId = useId();
  const active = Boolean(status?.active);
  const available = Boolean(status?.available);
  const blocked = Boolean(status?.insertion && !status.insertion.can_insert);
  const hotkey = status?.hotkey ?? "";
  const handsFree = status?.hotkey_toggle ?? "";
  const elapsed = useElapsedSeconds(active);

  // Blocked insertion still records — the words go to the clipboard — so it is
  // "ready with a caveat", a warning dot rather than a fault.
  const [tone, stateLabel]: [StateTone, string] = loading
    ? ["idle", t("dictation.loading")]
    : active
      ? ["live", t("dictation.state_active")]
      : !available
        ? ["error", t("dictation.state_unavailable")]
        : blocked
          ? ["warning", t("dictation.state_clipboard_only")]
          : ["ready", t("dictation.state_ready")];

  const body =
    !loading && !available
      ? status?.reason || t("dictation.state_unavailable")
      : active
        ? t("dictation.hero_body_active")
        : t("dictation.hero_body");

  return (
    <section
      aria-labelledby={headingId}
      data-testid="dictation-panel"
      data-state={loading ? "loading" : active ? "active" : available ? "ready" : "unavailable"}
      className={cn(
        "overflow-hidden rounded-xl border bg-card transition-colors",
        active ? "border-accent" : "border-border",
      )}
    >
      <div className="flex flex-col gap-6 p-5 sm:p-6">
        <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 text-sm">
          <p className="min-w-0 truncate text-muted-foreground" data-testid="dictation-welcome">
            {greeting}
          </p>
          <p className="inline-flex shrink-0 items-center gap-2 font-medium text-foreground">
            <StatusDot tone={tone} live={active} />
            {/* Only the state word is announced; the ticking seconds beside
                it would be read out once a second. */}
            <span aria-live="polite" data-testid="dictation-state">
              {stateLabel}
            </span>
            {active && (
              <span
                aria-hidden="true"
                className="tabular-nums text-muted-foreground"
                data-testid="dictation-elapsed"
              >
                {formatElapsed(elapsed)}
              </span>
            )}
          </p>
        </div>

        <div className="flex flex-col gap-2">
          <h2
            id={headingId}
            className="flex flex-wrap items-center gap-x-2.5 gap-y-2 text-xl font-semibold text-foreground-strong"
            data-testid="dictation-instruction"
          >
            {loading ? (
              <SkeletonBar className="h-9 w-72 max-w-full" />
            ) : active ? (
              t("dictation.hero_listening")
            ) : available && hotkey ? (
              <ComboSentence template={t("dictation.hero_hold")} combo={hotkey} size="lg" />
            ) : (
              t("dictation.hero_no_key")
            )}
          </h2>
          {loading ? (
            <SkeletonBar className="h-4 w-80 max-w-full" />
          ) : (
            <p className="text-sm text-muted-foreground">{body}</p>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Button
            disabled={loading || busy || !available}
            onClick={onToggle}
            data-testid="dictation-toggle"
          >
            {busy ? (
              <Loader2 aria-hidden="true" className="animate-spin motion-reduce:animate-none" />
            ) : active ? (
              <Square aria-hidden="true" />
            ) : (
              <Mic aria-hidden="true" />
            )}
            {active ? t("dictation.stop") : t("dictation.start")}
          </Button>
          {!loading && available && (
            <Button variant="ghost" onClick={onOpenShortcuts} data-testid="dictation-open-shortcuts">
              <Keyboard aria-hidden="true" />
              {hotkey ? t("dictation.hero_shortcuts") : t("dictation.hero_assign")}
            </Button>
          )}
        </div>
      </div>

      {!loading && available && !active && handsFree && (
        <p
          className="flex flex-wrap items-center gap-x-1.5 gap-y-1 border-t border-border px-5 py-3 text-sm text-muted-foreground sm:px-6"
          data-testid="dictation-hands-free"
        >
          <ComboSentence template={t("dictation.hero_hands_free")} combo={handsFree} size="sm" />
        </p>
      )}
    </section>
  );
}

/**
 * A sentence with keycaps where its `{0}` placeholder stands. The locale owns
 * the word order (some languages put the key mid-sentence, others at the end),
 * so the template is split at the placeholder instead of glued from fragments.
 */
function ComboSentence({
  template,
  combo,
  size,
}: {
  template: string;
  combo: string;
  size: "sm" | "lg";
}) {
  const [before, after = ""] = template.split("{0}");
  return (
    <>
      {before.trim() && <span>{before.trim()}</span>}
      <KeyCombo keys={formatCombo(combo).split(" + ")} size={size} />
      {after.trim() && <span>{after.trim()}</span>}
    </>
  );
}

/** Whole seconds since `active` last turned true; 0 while inactive. */
function useElapsedSeconds(active: boolean): number {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (!active) return;
    const started = Date.now();
    const id = window.setInterval(() => {
      setElapsed(Math.floor((Date.now() - started) / 1000));
    }, 1000);
    return () => {
      window.clearInterval(id);
      setElapsed(0);
    };
  }, [active]);
  return elapsed;
}

/** "0:07", "12:30" — minutes are never padded, seconds always are. */
function formatElapsed(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = String(seconds % 60).padStart(2, "0");
  return `${m}:${s}`;
}
