/**
 * How the assistant would describe you — the written portrait.
 *
 * `GET /api/board/bio` holds an LLM-written paragraph drawn from activity,
 * memory and past conversations, with a three-way feedback loop that steers
 * the next one. The feedback buttons say what they do to the next text, not
 * how the reader feels.
 *
 * A stored portrait that stops mid-word (an older generator kept a reply the
 * model never finished) is not shown as if it were the portrait: the page
 * says it was cut off and offers a new one.
 */
import { Loader2, RefreshCw, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useBio, useBioFeedback, useBioRegenerate, type BioFeedbackKind } from "@/hooks/useBoard";
import { useI18nStore, useT } from "@/i18n";
import { localeForUiLanguage } from "@/components/runs/format";
import { useEventStore } from "@/store/events";
import { ProfileGroup } from "@/views/profile/ProfileGroup";

/**
 * The three signals the backend accepts. The strings are API contract values,
 * matched by name in `jarvis/board/profile.py` — they are not display text and
 * never reach the screen; the labels beside them come from the locale.
 */
const FEEDBACK: { kind: BioFeedbackKind; labelKey: string }[] = [
  { kind: "trifft", labelKey: "profile_view.portrait_fits" }, // i18n-allow: API contract value
  { kind: "trifft_nicht", labelKey: "profile_view.portrait_misses" }, // i18n-allow: API contract value
  { kind: "haerter", labelKey: "profile_view.portrait_harder" }, // i18n-allow: API contract value
];

/** True when the text ends like a finished sentence. */
export function isFinishedText(text: string): boolean {
  return /[.!?…"'”»)\]]$/.test(text.trim());
}

export function PortraitSection() {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const bio = useBio();
  const regenerate = useBioRegenerate();
  const feedback = useBioFeedback();

  const text = bio.data?.text?.trim() || null;
  const generatedAt = bio.data?.generated_at ?? null;
  const cutOff = !!text && !isFinishedText(text);
  const written = generatedAt
    ? new Date(generatedAt).toLocaleDateString(localeForUiLanguage(useI18nStore.getState().ui), {
        day: "numeric",
        month: "short",
        year: "numeric",
      })
    : null;

  const sendFeedback = (kind: BioFeedbackKind) => {
    if (!generatedAt) return;
    feedback.mutate(
      { bio_generated_at: generatedAt, kind },
      {
        onSuccess: () => pushToast("success", t("profile_view.portrait_thanks")),
        onError: (err: Error) => pushToast("error", err.message),
      },
    );
  };

  const write = () =>
    regenerate.mutate(
      {},
      {
        onSuccess: () => pushToast("success", t("profile_view.portrait_written")),
        onError: (err: Error) => pushToast("error", err.message),
      },
    );

  const writeButton = (label: string) => (
    <Button type="button" size="sm" variant="outline" onClick={write} disabled={regenerate.isPending}>
      {regenerate.isPending ? <Loader2 className="animate-spin" /> : <Sparkles />}
      {label}
    </Button>
  );

  return (
    <ProfileGroup
      testId="portrait"
      title={t("profile_view.portrait_title")}
      description={t("profile_view.portrait_description")}
      aside={
        text && !cutOff ? (
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="text-muted-foreground"
            onClick={write}
            disabled={regenerate.isPending}
          >
            {regenerate.isPending ? <Loader2 className="animate-spin" /> : <RefreshCw />}
            {t("profile_view.portrait_rewrite")}
          </Button>
        ) : null
      }
    >
      <div className="px-6 py-5">
        {bio.isLoading ? (
          <div role="status" aria-busy="true" className="flex flex-col gap-2.5">
            <div className="h-3.5 w-full animate-pulse rounded-full bg-secondary" />
            <div className="h-3.5 w-11/12 animate-pulse rounded-full bg-secondary" />
            <div className="h-3.5 w-3/5 animate-pulse rounded-full bg-secondary" />
          </div>
        ) : text && !cutOff ? (
          <>
            <blockquote
              data-testid="portrait-text"
              className="border-l-2 border-accent pl-4 text-lg leading-relaxed text-foreground [overflow-wrap:anywhere]"
            >
              {text}
            </blockquote>
            <div className="mt-5 flex flex-wrap items-center justify-between gap-3">
              <p className="text-sm text-muted-foreground">
                {written && t("profile_view.portrait_written_on").replace("{0}", written)}
              </p>
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm text-muted-foreground">{t("profile_view.portrait_ask")}</span>
                {FEEDBACK.map((f) => (
                  <Button
                    key={f.kind}
                    type="button"
                    size="sm"
                    variant="outline"
                    disabled={feedback.isPending || !generatedAt}
                    onClick={() => sendFeedback(f.kind)}
                  >
                    {t(f.labelKey)}
                  </Button>
                ))}
              </div>
            </div>
          </>
        ) : (
          <div
            data-testid={cutOff ? "portrait-cut-off" : "portrait-empty"}
            className="flex flex-wrap items-center justify-between gap-4"
          >
            <p className="max-w-prose text-base text-muted-foreground">
              {cutOff ? t("profile_view.portrait_cut_off") : t("profile_view.portrait_empty_body")}
            </p>
            {writeButton(cutOff ? t("profile_view.portrait_rewrite") : t("profile_view.portrait_write"))}
          </div>
        )}
      </div>
    </ProfileGroup>
  );
}
