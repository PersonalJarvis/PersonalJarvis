/**
 * "Show me what is actually stored" — one item, opened.
 *
 * The inventory answers WHICH items exist. The maintainer's next question,
 * looking at a list of 4 689 rows, was the obvious one: *is the real content
 * in there, or just a title and a link?* A stage badge saying "distilled"
 * cannot answer that — it is a claim about a row you cannot see.
 *
 * So this shows both halves, and keeps them visibly separate:
 *
 * - **As captured** — the raw text exactly as it was read from the source, so
 *   nothing has to be taken on trust. If a connector mangled an encoding or
 *   truncated a body, it is visible here and nowhere else.
 * - **What Jarvis made of it** — the derived documents: the normalised text,
 *   the distillation (question / summary / resolution / entities), and whether
 *   a vector exists. That last flag is what turns the "embedded" badge from a
 *   promise into a fact.
 *
 * Nothing is summarised or prettified on the way to the screen: the point of
 * the view is that it is the database, not a story about the database.
 */
import { useEffect, useState } from "react";
import { ExternalLink, Loader2, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";
import {
  fetchUltraWikiItemDetail,
  type UltraWikiItemDetail,
  type UltraWikiItemDocument,
} from "@/lib/ultrawikiApi";

export function ItemDetailDialog({
  itemId,
  onClose,
}: {
  itemId: number;
  onClose: () => void;
}): JSX.Element {
  const t = useT();
  const [detail, setDetail] = useState<UltraWikiItemDetail | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setDetail(null);
    setError("");
    fetchUltraWikiItemDetail(itemId)
      .then((data) => {
        if (!cancelled) setDetail(data);
      })
      .catch((e: Error) => {
        if (!cancelled) setError(e.message);
      });
    return () => {
      cancelled = true;
    };
  }, [itemId]);

  // Escape closes: this is a read-only inspector, so there is nothing to lose
  // by leaving it and no reason to make the user aim for the X.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center bg-background/80 p-4 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-label={t("ultrawiki.item.title")}
      data-testid="ultrawiki-item-dialog"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="flex max-h-[86vh] w-full max-w-3xl flex-col overflow-hidden rounded-2xl border border-border bg-card shadow-2xl">
        <div className="flex items-start justify-between gap-3 border-b border-border px-5 py-3">
          <div className="min-w-0">
            <h2 className="truncate text-sm font-semibold text-foreground">
              {detail?.title || t("ultrawiki.item.title")}
            </h2>
            {detail && (
              <p className="mt-0.5 truncate text-[11px] text-muted-foreground">
                {detail.source_id}
                {" · "}
                <code className="font-mono">{detail.external_id}</code>
                {detail.timestamp_utc ? ` · ${detail.timestamp_utc}` : ""}
              </p>
            )}
          </div>
          <Button
            size="sm"
            variant="ghost"
            onClick={onClose}
            aria-label={t("common.close")}
            data-testid="ultrawiki-item-close"
          >
            <X className="h-4 w-4" aria-hidden />
          </Button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          {error && (
            <p
              role="alert"
              className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive"
              data-testid="ultrawiki-item-error"
            >
              {error}
            </p>
          )}

          {!detail && !error && (
            <p className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
              {t("ultrawiki.item.loading")}
            </p>
          )}

          {detail && (
            <div className="space-y-5">
              <div className="flex flex-wrap items-center gap-2">
                <StageBadge state={detail.state} />
                {detail.author && (
                  <span className="text-[11px] text-muted-foreground">
                    {detail.author}
                  </span>
                )}
                {detail.permalink && (
                  <a
                    href={detail.permalink}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:text-primary"
                  >
                    <ExternalLink className="h-3 w-3" aria-hidden />
                    {t("ultrawiki.item.open_source")}
                  </a>
                )}
              </div>

              {detail.last_error && (
                <p className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-[11px] text-destructive">
                  {detail.last_error}
                </p>
              )}

              <Section title={t("ultrawiki.item.as_captured")}>
                <pre
                  className="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-border/60 bg-muted/20 p-3 font-mono text-[11px] leading-relaxed text-foreground"
                  data-testid="ultrawiki-item-body"
                >
                  {detail.body || t("ultrawiki.item.empty_body")}
                </pre>
              </Section>

              <Section
                title={t("ultrawiki.item.derived")}
                hint={
                  // "Nothing derived yet" and "we could not read the
                  // derivations" look identical on screen and mean opposite
                  // things — one is normal progress, the other is a defect.
                  // Saying which is what caught a swallowed AttributeError
                  // that had turned every item into a silent "nothing yet".
                  detail.documents_error
                    ? t("ultrawiki.item.derived_error").replace(
                        "{0}",
                        detail.documents_error,
                      )
                    : detail.documents.length === 0
                      ? t("ultrawiki.item.derived_none")
                      : undefined
                }
              >
                <div className="space-y-3">
                  {detail.documents.map((doc) => (
                    <DocumentBlock key={doc.id} doc={doc} />
                  ))}
                </div>
              </Section>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function Section({
  title,
  hint,
  children,
}: {
  title: string;
  hint?: string;
  children: React.ReactNode;
}): JSX.Element {
  return (
    <section className="space-y-1.5">
      <h3 className="text-[10px] uppercase tracking-wider text-muted-foreground">
        {title}
      </h3>
      {hint ? (
        <p className="text-[11px] leading-relaxed text-muted-foreground">{hint}</p>
      ) : (
        children
      )}
    </section>
  );
}

function DocumentBlock({ doc }: { doc: UltraWikiItemDocument }): JSX.Element {
  const t = useT();
  const distill = doc.distill ?? null;
  return (
    <div
      className="rounded-lg border border-border/60 bg-muted/10 p-3"
      data-testid={`ultrawiki-item-doc-${doc.id}`}
    >
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="rounded-full border border-border bg-muted px-2 py-0.5 text-[10px] text-muted-foreground">
          {doc.doc_type}
        </span>
        <span
          className={cn(
            "rounded-full border px-2 py-0.5 text-[10px]",
            doc.has_vector
              ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-600"
              : "border-border bg-muted text-muted-foreground",
          )}
          data-testid={`ultrawiki-item-vector-${doc.id}`}
        >
          {t(doc.has_vector ? "ultrawiki.item.has_vector" : "ultrawiki.item.no_vector")}
        </span>
      </div>

      {/* The distillation, field by field. Rendered as labelled rows rather
          than dumped JSON: this is the part a person reads to decide whether
          the summary actually captured the item. */}
      {distill && typeof distill === "object" && (
        <dl className="mb-2 space-y-1">
          {Object.entries(distill)
            .filter(([, value]) => value !== null && value !== "" )
            .map(([key, value]) => (
              <div key={key} className="grid grid-cols-[7rem_1fr] gap-2">
                <dt className="text-[10px] uppercase tracking-wide text-muted-foreground">
                  {key}
                </dt>
                <dd className="min-w-0 break-words text-[11px] leading-relaxed text-foreground">
                  {Array.isArray(value) ? value.join(", ") : String(value)}
                </dd>
              </div>
            ))}
        </dl>
      )}

      <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words rounded border border-border/40 bg-background/60 p-2 font-mono text-[10px] leading-relaxed text-muted-foreground">
        {doc.text}
      </pre>
    </div>
  );
}

function StageBadge({ state }: { state: string }): JSX.Element {
  const t = useT();
  return (
    <span
      className={cn(
        "rounded-full border px-2 py-0.5 text-[10px]",
        state === "failed"
          ? "border-destructive/40 bg-destructive/10 text-destructive"
          : state === "distilled" || state === "embedded"
            ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-600"
            : "border-border bg-muted text-muted-foreground",
      )}
      data-testid="ultrawiki-item-state"
    >
      {t(`ultrawiki.progress.stage_${state}`)}
    </span>
  );
}
