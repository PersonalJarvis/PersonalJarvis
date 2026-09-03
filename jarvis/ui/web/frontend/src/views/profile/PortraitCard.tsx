/**
 * What the assistant would say about you if asked.
 *
 * `GET /api/board/bio` has held an LLM-written prose portrait of the user —
 * generated from board statistics, awareness episodes, mission history and
 * the memory file — since it was built, with a three-way feedback loop that
 * calibrates the next generation. Nothing in the product rendered it. On a
 * page whose whole subject is "what does it know about me", it is the single
 * most direct answer available, so it opens the rail.
 *
 * The feedback is not a rating. It is the one signal that changes how the
 * next portrait is written, which is why the buttons say what they do to the
 * text rather than how the reader feels about it.
 */
import { Loader2, RefreshCw, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useBio, useBioFeedback, useBioRegenerate, type BioFeedbackKind } from "@/hooks/useBoard";
import { useT } from "@/i18n";
import { useEventStore } from "@/store/events";

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

export function PortraitCard() {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const bio = useBio();
  const regenerate = useBioRegenerate();
  const feedback = useBioFeedback();

  const text = bio.data?.text?.trim() || null;
  const generatedAt = bio.data?.generated_at ?? null;

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

  return (
    <Card className="profile-rise">
      <CardHeader className="flex-row items-center justify-between gap-3 pb-2">
        <CardTitle className="flex items-center gap-2">
          <Sparkles aria-hidden className="h-4 w-4 text-muted-foreground" />
          {t("profile_view.portrait_title")}
        </CardTitle>
        {text && (
          <Button
            type="button"
            size="icon"
            variant="ghost"
            className="h-8 w-8 text-muted-foreground"
            onClick={write}
            disabled={regenerate.isPending}
            title={t("profile_view.portrait_rewrite")}
            aria-label={t("profile_view.portrait_rewrite")}
          >
            {regenerate.isPending ? <Loader2 className="animate-spin" /> : <RefreshCw />}
          </Button>
        )}
      </CardHeader>

      <CardContent>
        {bio.isLoading ? (
          <div role="status" aria-busy="true" className="flex flex-col gap-2">
            <div className="h-3 w-full animate-pulse rounded-full bg-sheen/[0.06]" />
            <div className="h-3 w-11/12 animate-pulse rounded-full bg-sheen/[0.06]" />
            <div className="h-3 w-4/6 animate-pulse rounded-full bg-sheen/[0.06]" />
          </div>
        ) : text ? (
          <>
            <p
              data-testid="portrait-text"
              className="text-reading text-foreground [overflow-wrap:anywhere]"
            >
              {text}
            </p>
            <div className="mt-4 flex flex-wrap gap-2">
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
          </>
        ) : (
          // Not a failure: the portrait is written on demand and this box has
          // never been asked. So it asks.
          <div data-testid="portrait-empty">
            <p className="text-body text-muted-foreground">
              {t("profile_view.portrait_empty_body")}
            </p>
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="mt-3"
              onClick={write}
              disabled={regenerate.isPending}
            >
              {regenerate.isPending ? <Loader2 className="animate-spin" /> : <Sparkles />}
              {t("profile_view.portrait_write")}
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
