/**
 * The open question — ONE at a time, with the sentence you can speak.
 *
 * A blank profile has eighteen holes in it. Listing them is a chore list; the
 * card asks a single question in priority order and hands over the literal
 * words to say, so filling the profile is a conversation rather than a form.
 * It is the only accent-washed object in the view, which is what makes it the
 * one thing the eye goes to when the profile is still thin.
 */
import { useMemo, useState } from "react";
import { ChevronRight, Mic, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { useT } from "@/i18n";
import { TOTAL_FIELDS, collectOpenQuestions } from "@/views/profile/ledger";

export function AskCard({ meta }: { meta: Record<string, unknown> }) {
  const t = useT();
  const [idx, setIdx] = useState(0);

  const open = useMemo(() => collectOpenQuestions(meta, TOTAL_FIELDS), [meta]);

  if (open.length === 0) {
    return (
      <Card>
        <CardHeader className="pb-0">
          <CardTitle>{t("profile_view.ask_title")}</CardTitle>
        </CardHeader>
        <CardContent>
          <EmptyState
            className="max-w-none py-6"
            icon={<Sparkles />}
            title={t("profile_view.ask_empty_title")}
            description={t("profile_view.ask_empty_body")}
          />
        </CardContent>
      </Card>
    );
  }

  const q = open[idx % open.length];

  return (
    <Card className="border-accent/20 bg-accent-soft">
      <CardHeader className="flex-row items-center justify-between gap-3 pb-2">
        <span className="flex min-w-0 items-center gap-2 text-sm font-medium text-accent">
          <Sparkles aria-hidden className="h-4 w-4 shrink-0" />
          <span className="truncate">{t("profile_view.ask_title")}</span>
        </span>
        <span className="shrink-0 text-sm tabular-nums text-muted-foreground">
          {(idx % open.length) + 1}/{open.length}
        </span>
      </CardHeader>

      <CardContent>
        <p className="text-lg font-semibold text-foreground-strong">
          {t(`profile_view.questions.${q.field}`)}
        </p>

        <p className="mt-3 flex items-start gap-2 text-base text-muted-foreground">
          <Mic aria-hidden className="mt-0.5 h-4 w-4 shrink-0" />
          <span className="min-w-0">
            {t("profile_view.ask_say_prefix")}:{" "}
            <span className="text-foreground">“{t(`profile_view.says.${q.field}`)}”</span>
          </span>
        </p>

        <div className="mt-4 flex items-center justify-between gap-3">
          <span className="min-w-0 truncate text-sm text-muted-foreground">
            {t(`profile_view.clusters.${q.cluster}.label`)}
          </span>
          <Button
            type="button"
            size="sm"
            variant="outline"
            data-testid="ask-next"
            onClick={() => setIdx((i) => i + 1)}
          >
            {t("profile_view.ask_next")}
            <ChevronRight />
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
