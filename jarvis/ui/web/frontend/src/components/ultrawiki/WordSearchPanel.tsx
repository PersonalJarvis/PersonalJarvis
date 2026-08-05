/**
 * Words view: type ONE word, see the ~20 terms nearest it by meaning, and the
 * passages that neighbourhood reaches (`GET /api/ultrawiki/word-search`).
 *
 * Why this is its own tab and not a mode of Ask: Ask answers a question and
 * shows an answer. This answers a word and shows a MAP — the neighbourhood
 * first, because half the value is discovering how the corpus actually spells
 * the thing you were hunting, and the passages second, each with the span it
 * came from rather than the head of a file.
 *
 * Honesty rules this screen keeps:
 * - every empty result names its own cause (`status`), never a blank list;
 * - neighbours derived from co-occurrence rather than from meaning say so,
 *   because the two numbers are not comparable;
 * - a chip is clickable and re-runs the search on that word, which is the
 *   whole point of a neighbourhood you can see.
 */
import { useState } from "react";
import { ExternalLink, Loader2, Sparkles, Type } from "lucide-react";
import { useQuery } from "@tanstack/react-query";

import { cn } from "@/lib/utils";
import { useT } from "@/i18n";
import {
  wordSearchUltraWiki,
  type UltraWikiPassage,
  type UltraWikiTermNeighbour,
  type UltraWikiWordHit,
  type UltraWikiWordSearchResponse,
} from "@/lib/ultrawikiApi";

export function WordSearchPanel({
  onOpenSources,
  onOpenSettings,
}: {
  onOpenSources?: () => void;
  onOpenSettings?: () => void;
}): JSX.Element {
  const t = useT();
  const [input, setInput] = useState("");
  const [submitted, setSubmitted] = useState("");

  const query = useQuery({
    queryKey: ["ultrawiki", "word-search", submitted],
    queryFn: () => wordSearchUltraWiki(submitted),
    enabled: submitted.length > 0,
    retry: false,
    staleTime: 5_000,
  });

  const data = query.data ?? null;
  const runWord = (word: string) => {
    setInput(word);
    setSubmitted(word);
  };

  return (
    <div className="flex h-full min-h-0 flex-col p-4" data-testid="ultrawiki-word-panel">
      <form
        className="flex items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          setSubmitted(input.trim());
        }}
      >
        <div className="relative flex-1">
          <Type
            className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
            aria-hidden
          />
          <input
            type="search"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder={t("ultrawiki.words.placeholder")}
            aria-label={t("ultrawiki.words.placeholder")}
            data-testid="ultrawiki-word-input"
            className="w-full rounded-lg border border-border bg-background py-2 pl-9 pr-3 text-sm text-foreground placeholder:text-muted-foreground/70 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/40"
          />
        </div>
        <button
          type="submit"
          disabled={input.trim().length === 0}
          data-testid="ultrawiki-word-submit"
          className="rounded-lg border border-border bg-card px-3 py-2 text-sm text-foreground transition-colors hover:bg-secondary disabled:cursor-not-allowed disabled:opacity-50"
        >
          {t("ultrawiki.words.submit")}
        </button>
      </form>

      <p className="mt-1.5 text-[11px] text-muted-foreground">
        {t("ultrawiki.words.explainer")}
      </p>

      <div className="mt-3 min-h-0 flex-1 overflow-y-auto">
        {!submitted ? (
          <p
            className="py-6 text-sm text-muted-foreground"
            data-testid="ultrawiki-word-idle"
          >
            {t("ultrawiki.words.idle")}
          </p>
        ) : query.isFetching ? (
          <div className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
            {t("ultrawiki.words.searching")}
          </div>
        ) : query.isError ? (
          <p
            className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive"
            role="alert"
            data-testid="ultrawiki-word-error"
          >
            {t("ultrawiki.words.failed").replace(
              "{0}",
              (query.error as Error).message,
            )}
          </p>
        ) : data ? (
          <WordSearchBody
            data={data}
            onPickWord={runWord}
            onOpenSources={onOpenSources}
            onOpenSettings={onOpenSettings}
          />
        ) : null}
      </div>
    </div>
  );
}

function WordSearchBody({
  data,
  onPickWord,
  onOpenSources,
  onOpenSettings,
}: {
  data: UltraWikiWordSearchResponse;
  onPickWord: (word: string) => void;
  onOpenSources?: () => void;
  onOpenSettings?: () => void;
}): JSX.Element {
  const t = useT();
  const byMeaning = data.neighbour_source === "vector";

  return (
    <div className="space-y-4">
      {/* The neighbourhood leads: it is the answer to "how does this corpus
          spell the thing I am after", which is often all the user needed. */}
      {data.neighbours.length > 0 && (
        <section data-testid="ultrawiki-word-neighbours">
          <p className="mb-1.5 flex flex-wrap items-center gap-1.5 text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
            <Sparkles className="h-3 w-3" aria-hidden />
            {(byMeaning
              ? t("ultrawiki.words.neighbours_meaning")
              : t("ultrawiki.words.neighbours_company")
            ).replace("{0}", data.word)}
          </p>
          <ul className="flex flex-wrap gap-1.5">
            {data.neighbours.map((neighbour) => (
              <NeighbourChip
                key={neighbour.term}
                neighbour={neighbour}
                byMeaning={byMeaning}
                onPick={onPickWord}
              />
            ))}
          </ul>
        </section>
      )}

      {data.reason && (
        <p
          className="rounded-md border border-[#ffb84d]/30 bg-[#ffb84d]/10 px-3 py-2 text-xs text-[#ffb84d]"
          data-testid="ultrawiki-word-reason"
        >
          {data.reason}
          {data.status === "empty_index" && onOpenSources && (
            <button
              type="button"
              onClick={onOpenSources}
              className="ml-1.5 underline underline-offset-2 hover:text-foreground"
              data-testid="ultrawiki-word-open-sources"
            >
              {t("ultrawiki.progress.open_sources")}
            </button>
          )}
          {data.status === "neighbours_unavailable" && onOpenSettings && (
            <button
              type="button"
              onClick={onOpenSettings}
              className="ml-1.5 underline underline-offset-2 hover:text-foreground"
              data-testid="ultrawiki-word-open-settings"
            >
              {t("ultrawiki.panel.open_settings")}
            </button>
          )}
        </p>
      )}

      {data.results.length > 0 ? (
        <section>
          <p className="mb-1.5 text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
            {t("ultrawiki.words.passages_heading").replace(
              "{0}",
              String(data.total),
            )}
          </p>
          <ul className="space-y-2" data-testid="ultrawiki-word-results">
            {data.results.map((hit) => (
              <WordHitRow key={`${hit.item_id}-${hit.permalink}`} hit={hit} />
            ))}
          </ul>
        </section>
      ) : (
        <LexiconNote lexicon={data.lexicon} />
      )}
    </div>
  );
}

function NeighbourChip({
  neighbour,
  byMeaning,
  onPick,
}: {
  neighbour: UltraWikiTermNeighbour;
  byMeaning: boolean;
  onPick: (word: string) => void;
}): JSX.Element {
  const t = useT();
  return (
    <li>
      <button
        type="button"
        onClick={() => onPick(neighbour.term)}
        title={(byMeaning
          ? t("ultrawiki.words.chip_hint_meaning")
          : t("ultrawiki.words.chip_hint_company")
        )
          .replace("{0}", neighbour.similarity.toFixed(2))
          .replace("{1}", String(neighbour.doc_freq))}
        data-testid={`ultrawiki-word-chip-${neighbour.term}`}
        className={cn(
          "rounded-full border px-2 py-0.5 text-xs transition-colors",
          byMeaning
            ? "border-primary/40 bg-primary/10 text-primary hover:bg-primary/20"
            : "border-border text-muted-foreground hover:bg-secondary hover:text-foreground",
        )}
      >
        {neighbour.term}
      </button>
    </li>
  );
}

/**
 * The retrieval legs of jarvis/ultrawiki/word_search.py::_retrieve. Looked up
 * by membership rather than by building a key: `t()` echoes an unknown key
 * back verbatim, so a leg added on the server would render as
 * "ultrawiki.words.leg_whatever" instead of as its own name.
 */
const WORD_LEG_LABEL_KEY: Record<string, string> = {
  word: "ultrawiki.words.leg_word",
  semantic: "ultrawiki.words.leg_semantic",
  related: "ultrawiki.words.leg_related",
  neighbourhood: "ultrawiki.words.leg_neighbourhood",
};

function WordHitRow({ hit }: { hit: UltraWikiWordHit }): JSX.Element {
  const t = useT();
  return (
    <li
      className="rounded-xl border border-border bg-card/40 p-3"
      data-testid={`ultrawiki-word-hit-${hit.item_id}`}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-sm font-medium text-foreground">
          {hit.title || hit.snippet.slice(0, 60)}
        </span>
        <span className="flex flex-wrap items-center gap-1.5">
          {hit.matched_by.map((leg) => (
            <span
              key={leg}
              className={cn(
                "rounded-full border px-1.5 py-0.5 text-[10px]",
                leg === "semantic" || leg === "neighbourhood"
                  ? "border-primary/40 bg-primary/10 text-primary"
                  : "border-border text-muted-foreground",
              )}
              data-testid={`ultrawiki-word-leg-${hit.item_id}-${leg}`}
            >
              {leg in WORD_LEG_LABEL_KEY ? t(WORD_LEG_LABEL_KEY[leg]) : leg}
            </span>
          ))}
        </span>
      </div>

      {hit.passages.length > 0 ? (
        <ul className="mt-2 space-y-1.5">
          {hit.passages.map((passage) => (
            <PassageRow
              key={passage.document_id}
              passage={passage}
              itemId={hit.item_id}
            />
          ))}
        </ul>
      ) : (
        // No stored passages for this item (nothing embedded it yet), so the
        // snippet is genuinely all there is. Saying nothing here would read as
        // a rendering bug rather than as an honest limit.
        <p
          className="mt-1 text-xs leading-relaxed text-muted-foreground"
          data-testid={`ultrawiki-word-snippet-${hit.item_id}`}
        >
          {hit.snippet}
        </p>
      )}

      <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-muted-foreground">
        <span className="font-mono">{hit.source_id}</span>
        {hit.timestamp_utc && <span>{hit.timestamp_utc}</span>}
        <a
          href={hit.permalink}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1 text-foreground underline-offset-2 hover:underline"
          data-testid={`ultrawiki-word-permalink-${hit.item_id}`}
        >
          <ExternalLink className="h-3 w-3" aria-hidden />
          {t("ultrawiki.ask.open_source")}
        </a>
      </div>
    </li>
  );
}

function PassageRow({
  passage,
  itemId,
}: {
  passage: UltraWikiPassage;
  itemId: number;
}): JSX.Element {
  const t = useT();
  return (
    <li
      className="rounded-lg border border-border/60 bg-background/40 p-2"
      data-testid={`ultrawiki-word-passage-${itemId}-${passage.document_id}`}
    >
      <p className="text-xs leading-relaxed text-muted-foreground">
        {passage.text}
      </p>
      <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[10px] text-muted-foreground/80">
        {/* The span is the point: it says WHERE in a long file this sits. */}
        <span className="font-mono tabular-nums">
          {t("ultrawiki.words.span")
            .replace("{0}", String(passage.chunk_index + 1))
            .replace("{1}", String(passage.char_start))
            .replace("{2}", String(passage.char_end))}
        </span>
        {passage.terms.length > 0 && (
          <span>
            {t("ultrawiki.words.matched_terms").replace(
              "{0}",
              passage.terms.join(", "),
            )}
          </span>
        )}
      </p>
    </li>
  );
}

function LexiconNote({
  lexicon,
}: {
  lexicon: UltraWikiWordSearchResponse["lexicon"];
}): JSX.Element | null {
  const t = useT();
  const terms = lexicon.terms ?? 0;
  const embedded = lexicon.embedded_terms ?? 0;
  if (terms === 0) return null;
  return (
    <p
      className="text-xs text-muted-foreground"
      data-testid="ultrawiki-word-lexicon"
    >
      {t("ultrawiki.words.lexicon_state")
        .replace("{0}", String(embedded))
        .replace("{1}", String(terms))}
    </p>
  );
}
