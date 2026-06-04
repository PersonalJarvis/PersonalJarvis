import { Check, Loader2, RefreshCw, Sparkles, X, Zap } from "lucide-react";
import { useEffect, useState } from "react";
import {
  type BioFeedbackKind,
  useBio,
  useBioFeedback,
  useBioRegenerate,
} from "@/hooks/useBoard";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";

/**
 * "Wer ist dieser User?" — AI-generierte Selbstbeobachtung von Jarvis im
 * "Ich-Erzaehler"-Stil, beissend mit Augenzwinkern (Brainstorm 2026-05-02).
 *
 * Drei Reagier-Buttons unter dem Text kalibrieren die naechste Bio-Generation
 * (Trifft / Trifft nicht / Haerter). Klicks werden NICHT sofort regeneriert —
 * sie fliessen als Tone-Vector in den Sonntags-Run.
 */
export function AIProfileCard() {
  const t = useT();
  const bio = useBio();
  const regen = useBioRegenerate();
  const feedback = useBioFeedback();

  const data = bio.data;
  const [lastFeedback, setLastFeedback] = useState<BioFeedbackKind | null>(null);

  // "Notiert."-Toast nach 2.5s wieder ausblenden, damit es nicht stehen bleibt.
  useEffect(() => {
    if (!lastFeedback) return;
    const t = setTimeout(() => setLastFeedback(null), 2500);
    return () => clearTimeout(t);
  }, [lastFeedback]);

  const handleFeedback = (kind: BioFeedbackKind) => {
    if (!data?.generated_at) return;
    feedback.mutate(
      { bio_generated_at: data.generated_at, kind },
      { onSuccess: () => setLastFeedback(kind) },
    );
  };

  const stalenessLabel = (() => {
    const d = data?.staleness_days;
    if (d == null) return null;
    if (d === 0) return t("board_view.ai_profile_today");
    if (d === 1) return t("board_view.ai_profile_one_day_ago");
    return t("board_view.ai_profile_days_ago").replace("{0}", String(d));
  })();

  return (
    <section className="space-y-3 rounded-xl border border-border bg-card/30 p-5 backdrop-blur">
      <header className="flex items-start gap-3">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg border border-primary/40 bg-primary/10 text-primary">
          <Sparkles className="h-4 w-4" />
        </div>
        <div className="flex-1 min-w-0">
          <h3 className="font-display text-sm font-semibold">{t("board_view.ai_profile_title")}</h3>
          <p className="text-xs text-muted-foreground">
            {t("board_view.ai_profile_description")}
          </p>
        </div>
        <button
          type="button"
          onClick={() => regen.mutate({})}
          disabled={regen.isPending}
          className={cn(
            "inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1 text-[11px] font-medium transition-colors",
            "hover:border-primary/40 hover:bg-background/60",
            regen.isPending && "opacity-60",
          )}
          title={t("board_view.ai_profile_regenerate_tooltip")}
        >
          <RefreshCw className={cn("h-3 w-3", regen.isPending && "animate-spin")} />
          {t("board_view.ai_profile_regenerate")}
        </button>
      </header>

      {bio.isLoading && (
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2 className="h-3 w-3 animate-spin" /> {t("board_view.ai_profile_loading")}
        </div>
      )}

      {!bio.isLoading && !data?.text && (
        <div className="rounded-lg border border-dashed border-border/60 p-4 text-sm text-muted-foreground">
          <div className="flex items-center gap-2">
            <Loader2 className="h-3 w-3 animate-spin opacity-60" />
            <span>{t("board_view.ai_profile_collecting")}</span>
          </div>
          <p className="mt-1.5 text-xs opacity-80">
            {t("board_view.ai_profile_first_bio_hint")}
          </p>
        </div>
      )}

      {data?.text && (
        <div className="space-y-3">
          <p className="whitespace-pre-wrap text-sm leading-relaxed text-foreground/90">
            {data.text}
          </p>
          <div className="flex items-center gap-3 text-[10px] uppercase tracking-wider text-muted-foreground">
            {stalenessLabel && <span>{stalenessLabel}</span>}
            {data.triggered_by && <span>· {data.triggered_by}</span>}
            {data.model_used && <span>· {data.model_used}</span>}
          </div>
          <div className="flex flex-wrap items-center gap-2 border-t border-border/40 pt-3">
            <FeedbackButton
              kind="trifft"
              icon={<Check className="h-3 w-3" />}
              label={t("board_view.feedback_correct")}
              colorClass="border-emerald-500/40 bg-emerald-500/10 text-emerald-300 hover:bg-emerald-500/20"
              active={lastFeedback === "trifft"}
              disabled={feedback.isPending}
              onClick={() => handleFeedback("trifft")}
            />
            <FeedbackButton
              kind="trifft_nicht"
              icon={<X className="h-3 w-3" />}
              label={t("board_view.feedback_incorrect")}
              colorClass="border-rose-500/40 bg-rose-500/10 text-rose-300 hover:bg-rose-500/20"
              active={lastFeedback === "trifft_nicht"}
              disabled={feedback.isPending}
              onClick={() => handleFeedback("trifft_nicht")}
            />
            <FeedbackButton
              kind="haerter"
              icon={<Zap className="h-3 w-3" />}
              label={t("board_view.feedback_harder")}
              colorClass="border-amber-500/40 bg-amber-500/10 text-amber-300 hover:bg-amber-500/20"
              active={lastFeedback === "haerter"}
              disabled={feedback.isPending}
              onClick={() => handleFeedback("haerter")}
            />
            {lastFeedback && (
              <span className="text-[11px] italic text-muted-foreground">
                {t("board_view.feedback_noted")}
              </span>
            )}
          </div>
        </div>
      )}

      {regen.isError && (
        <div className="text-xs text-destructive">
          {t("board_view.regenerate_failed")}: {(regen.error as Error).message}
        </div>
      )}
      {regen.data && !regen.data.ok && regen.data.reason && (
        <div className="text-xs text-amber-400">
          {regen.data.reason}
        </div>
      )}
    </section>
  );
}

interface FeedbackButtonProps {
  kind: BioFeedbackKind;
  icon: React.ReactNode;
  label: string;
  colorClass: string;
  active: boolean;
  disabled: boolean;
  onClick: () => void;
}

function FeedbackButton({
  icon,
  label,
  colorClass,
  active,
  disabled,
  onClick,
}: FeedbackButtonProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-[11px] font-medium transition-colors",
        colorClass,
        active && "ring-1 ring-current",
        disabled && "opacity-60",
      )}
    >
      {icon}
      {label}
    </button>
  );
}
