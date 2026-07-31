/**
 * Ask view (design doc 04): evidence retrieval plus a cited answer
 * (`POST /api/ultrawiki/ask`). Every hit is a citation — title, snippet,
 * source, matched-by chips (keyword / semantic), timestamp, and the permalink
 * that deep-links back to where the item lives. When synthesis is unavailable,
 * the evidence remains usable instead of disappearing behind an error.
 *
 * Honesty: the legs line under the box states which retrieval legs are live
 * (from `/status` `search_legs`), and the empty state repeats the reason a
 * degraded leg gives instead of pretending the store is empty.
 */
import { useState } from "react";
import { ExternalLink, Loader2, Search } from "lucide-react";
import { useQuery } from "@tanstack/react-query";

import { cn } from "@/lib/utils";
import { useT } from "@/i18n";
import {
  askUltraWiki,
  type UltraWikiSearchHit,
  type UltraWikiStatus,
} from "@/lib/ultrawikiApi";

export function AskPanel({
  searchLegs,
  slots,
  ingestedItems = 0,
  onOpenSources,
}: {
  searchLegs: UltraWikiStatus["search_legs"] | undefined;
  /** Capability slots — used only for the honest `via` provenance line. */
  slots?: UltraWikiStatus["slots"];
  /** Items in the store; 0 means "nothing ingested yet", not "no hits". */
  ingestedItems?: number;
  onOpenSources?: () => void;
}): JSX.Element {
  const t = useT();
  const [input, setInput] = useState("");
  const [submitted, setSubmitted] = useState("");

  const searchQuery = useQuery({
    queryKey: ["ultrawiki", "ask", submitted],
    queryFn: () => askUltraWiki(submitted),
    enabled: submitted.length > 0,
    retry: false,
    staleTime: 5_000,
  });

  const results = searchQuery.data?.results ?? [];

  return (
    <div className="flex h-full min-h-0 flex-col p-4" data-testid="ultrawiki-ask-panel">
      <form
        className="flex items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          setSubmitted(input.trim());
        }}
      >
        <div className="relative flex-1">
          <Search
            className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
            aria-hidden
          />
          <input
            type="search"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder={t("ultrawiki.ask.placeholder")}
            aria-label={t("ultrawiki.ask.placeholder")}
            data-testid="ultrawiki-ask-input"
            className="w-full rounded-lg border border-border bg-background py-2 pl-9 pr-3 text-sm text-foreground placeholder:text-muted-foreground/70 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/40"
          />
        </div>
        <button
          type="submit"
          disabled={input.trim().length === 0}
          data-testid="ultrawiki-ask-submit"
          className="rounded-lg border border-border bg-card px-3 py-2 text-sm text-foreground transition-colors hover:bg-secondary disabled:cursor-not-allowed disabled:opacity-50"
        >
          {t("ultrawiki.panel.tab_ask")}
        </button>
      </form>

      <SearchLegsLine legs={searchLegs} slots={slots} />

      <div className="mt-3 min-h-0 flex-1 overflow-y-auto">
        {!submitted && ingestedItems === 0 ? (
          // Nothing is in the store yet. A blank panel here reads as "the
          // search is broken"; the two steps that actually fill it do not.
          <div
            className="space-y-2 py-6 text-sm text-muted-foreground"
            data-testid="ultrawiki-ask-nothing-ingested"
          >
            <p className="text-foreground">
              {t("ultrawiki.ask.nothing_ingested_title")}
            </p>
            <ol className="ml-4 list-decimal space-y-1 text-xs">
              <li>{t("ultrawiki.ask.nothing_ingested_step_approve")}</li>
              <li>{t("ultrawiki.ask.nothing_ingested_step_sync")}</li>
            </ol>
            <p className="text-xs">{t("ultrawiki.ask.nothing_ingested_note")}</p>
            {onOpenSources && (
              <button
                type="button"
                onClick={onOpenSources}
                className="text-xs underline underline-offset-2 hover:text-primary"
                data-testid="ultrawiki-ask-open-sources"
              >
                {t("ultrawiki.progress.open_sources")}
              </button>
            )}
          </div>
        ) : searchQuery.isFetching ? (
          <div className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
            {t("ultrawiki.ask.searching")}
          </div>
        ) : searchQuery.isError ? (
          <p
            className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive"
            role="alert"
            data-testid="ultrawiki-ask-error"
          >
            {t("ultrawiki.ask.search_failed").replace(
              "{0}",
              (searchQuery.error as Error).message,
            )}
          </p>
        ) : submitted && results.length === 0 && searchQuery.isSuccess ? (
          <div
            className="space-y-1.5 py-6 text-sm text-muted-foreground"
            data-testid="ultrawiki-ask-empty"
          >
            <p>{t("ultrawiki.ask.no_results").replace("{0}", submitted)}</p>
            {searchLegs?.vector?.available === false && (
              <p className="text-xs">
                {t("ultrawiki.ask.leg_vector_off").replace(
                  "{0}",
                  searchLegs.vector.reason ?? "",
                )}
              </p>
            )}
          </div>
        ) : (
          <div className="space-y-3">
            {searchQuery.data?.answer_status === "answered" && (
              <section
                className="rounded-xl border border-primary/25 bg-primary/[0.06] p-4"
                data-testid="ultrawiki-answer"
              >
                <p className="whitespace-pre-wrap text-sm leading-relaxed text-foreground">
                  {searchQuery.data.answer}
                </p>
                {searchQuery.data.provider && (
                  <p className="mt-2 text-[10px] text-muted-foreground">
                    {t("ultrawiki.ask.answered_via").replace(
                      "{0}",
                      searchQuery.data.provider,
                    )}
                  </p>
                )}
              </section>
            )}
            {searchQuery.data?.answer_status === "insufficient_evidence" && (
              <section
                className="rounded-xl border border-[#ffb84d]/30 bg-[#ffb84d]/10 p-4"
                data-testid="ultrawiki-insufficient-evidence"
              >
                <p className="whitespace-pre-wrap text-sm leading-relaxed text-foreground">
                  {searchQuery.data.answer}
                </p>
                {searchQuery.data.provider && (
                  <p className="mt-2 text-[10px] text-muted-foreground">
                    {t("ultrawiki.ask.answered_via").replace(
                      "{0}",
                      searchQuery.data.provider,
                    )}
                  </p>
                )}
              </section>
            )}
            {searchQuery.data?.answer_status === "answer_unavailable" && (
              <p
                className="rounded-md border border-[#ffb84d]/30 bg-[#ffb84d]/10 px-3 py-2 text-xs text-[#ffb84d]"
                data-testid="ultrawiki-answer-unavailable"
              >
                {t("ultrawiki.ask.answer_unavailable").replace(
                  "{0}",
                  searchQuery.data.synthesis_error ?? "",
                )}
              </p>
            )}
            {results.length > 0 && (
              <section>
                <p className="mb-1.5 text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
                  {t("ultrawiki.ask.evidence")}
                </p>
                <ul className="space-y-2" data-testid="ultrawiki-ask-results">
                  {results.map((hit, index) => (
                    <ResultRow
                      key={`${hit.item_id}-${hit.permalink}`}
                      hit={hit}
                      citation={index + 1}
                    />
                  ))}
                </ul>
              </section>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function SearchLegsLine({
  legs,
  slots,
}: {
  legs: UltraWikiStatus["search_legs"] | undefined;
  slots?: UltraWikiStatus["slots"];
}): JSX.Element | null {
  const t = useT();
  if (!legs) return null;
  // "via <credential path>" — which of the user's own keys/endpoints is
  // actually carrying this leg. Never a key value, only where it came from.
  const withVia = (text: string, via: string | undefined): string =>
    via ? `${text} — ${t("ultrawiki.ask.via").replace("{0}", via)}` : text;
  const parts: Array<{ key: string; text: string; degraded: boolean }> = [];
  if (legs.keyword?.available) {
    parts.push({
      key: "keyword",
      text: t("ultrawiki.ask.leg_keyword_on"),
      degraded: false,
    });
  }
  if (legs.vector) {
    parts.push(
      legs.vector.available
        ? {
            key: "vector",
            text: withVia(
              t("ultrawiki.ask.leg_vector_on"),
              slots?.embedding?.via,
            ),
            degraded: false,
          }
        : {
            key: "vector",
            text: t("ultrawiki.ask.leg_vector_off").replace(
              "{0}",
              legs.vector.reason ?? "",
            ),
            degraded: true,
          },
    );
  }
  if (legs.rerank) {
    parts.push({
      key: "rerank",
      text: legs.rerank.available
        ? withVia(t("ultrawiki.ask.leg_rerank_on"), slots?.rerank?.via)
        : t("ultrawiki.ask.leg_rerank_off"),
      degraded: false,
    });
  }
  if (parts.length === 0) return null;
  return (
    <p
      className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-muted-foreground"
      data-testid="ultrawiki-search-legs"
    >
      {parts.map((part) => (
        <span
          key={part.key}
          className={cn(part.degraded && "text-[#ffb84d]")}
          data-testid={`ultrawiki-leg-${part.key}`}
        >
          {part.text}
        </span>
      ))}
    </p>
  );
}

const MATCHED_BY_LABEL_KEY: Record<string, string> = {
  keyword: "ultrawiki.ask.matched_keyword",
  vector: "ultrawiki.ask.matched_vector",
};

function ResultRow({
  hit,
  citation,
}: {
  hit: UltraWikiSearchHit;
  citation: number;
}): JSX.Element {
  const t = useT();
  return (
    <li
      className="rounded-xl border border-border bg-card/40 p-3"
      data-testid={`ultrawiki-hit-${hit.item_id}`}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-sm font-medium text-foreground">
          <span className="mr-1.5 font-mono text-primary">[{citation}]</span>
          {hit.title || hit.snippet.slice(0, 60)}
        </span>
        <span className="flex items-center gap-1.5">
          {/* The absolute 0-10 grade, when the rerank stage actually ran.
              Unlike the fused score it means something on its own, so it is
              the one number worth showing. */}
          {typeof hit.rerank_score === "number" && (
            <span
              className={cn(
                "rounded-full border px-1.5 py-0.5 text-[10px] tabular-nums",
                hit.rerank_score >= 7
                  ? "border-[#5bd4a4]/40 bg-[#5bd4a4]/10 text-[#5bd4a4]"
                  : hit.rerank_score >= 4
                    ? "border-border text-muted-foreground"
                    : "border-[#ffb84d]/40 bg-[#ffb84d]/10 text-[#ffb84d]",
              )}
              title={t("ultrawiki.ask.grade_hint")}
              data-testid={`ultrawiki-grade-${hit.item_id}`}
            >
              {hit.rerank_score.toFixed(1)}/10
            </span>
          )}
          {hit.matched_by.map((leg) => (
            <span
              key={leg}
              className={cn(
                "rounded-full border px-1.5 py-0.5 text-[10px]",
                leg === "vector"
                  ? "border-primary/40 bg-primary/10 text-primary"
                  : "border-border text-muted-foreground",
              )}
              data-testid={`ultrawiki-matched-${hit.item_id}-${leg}`}
            >
              {t(MATCHED_BY_LABEL_KEY[leg] ?? "") || leg}
            </span>
          ))}
        </span>
      </div>
      {hit.snippet && (
        <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
          {hit.snippet}
        </p>
      )}
      {/* Context expansion: the neighbouring messages/sections, so the hit
          reads as evidence instead of an orphaned fragment. */}
      {hit.context?.length > 0 && (
        <details className="mt-1.5" data-testid={`ultrawiki-context-${hit.item_id}`}>
          <summary className="cursor-pointer text-[11px] text-muted-foreground hover:text-foreground">
            {t("ultrawiki.ask.context_toggle").replace(
              "{0}",
              String(hit.context.length),
            )}
          </summary>
          <ul className="mt-1 space-y-1 border-l border-border/60 pl-2">
            {hit.context.map((line, index) => (
              <li
                key={index}
                className="text-[11px] leading-relaxed text-muted-foreground/80"
              >
                {line}
              </li>
            ))}
          </ul>
        </details>
      )}
      <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-muted-foreground">
        <span className="font-mono">{hit.source_id}</span>
        {hit.timestamp_utc && <span>{hit.timestamp_utc}</span>}
        {/* The citation: a permalink is mandatory from item one (design doc 02). */}
        <a
          href={hit.permalink}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1 text-foreground underline-offset-2 hover:underline"
          data-testid={`ultrawiki-permalink-${hit.item_id}`}
        >
          <ExternalLink className="h-3 w-3" aria-hidden />
          {t("ultrawiki.ask.open_source")}
        </a>
      </div>
    </li>
  );
}
