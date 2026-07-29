import { useState } from "react";
import { Check, Copy, RotateCcw, Trash2, Volume2 } from "lucide-react";

import {
  cleanupReasonLabel,
  DICTATION_OUTCOMES,
  polishStatusLabel,
  STT_FAILURE_REASONS,
  type DictationEntry,
} from "@/hooks/useDictation";
import { useT } from "@/i18n";

/**
 * One day's worth of dictations — a sticky date header plus its rows.
 *
 * Grouping by day is what turns a flat list into something you can actually
 * read back: "what did I dictate this morning" is a question about a day, not
 * about entry number 34.
 */
export interface DictationHistoryGroupProps {
  /** Already-localized day label — "Today", "Yesterday", or a formatted date. */
  label: string;
  entries: DictationEntry[];
  onCopy: (entry: DictationEntry) => void;
  onDiscard: (entry: DictationEntry) => void;
  onRestore: (entry: DictationEntry) => void;
  onDelete: (entry: DictationEntry) => void;
  /** Ids currently waiting on a request, so the row can disable its buttons. */
  busyIds: ReadonlySet<string>;
  /** Id whose copy just succeeded, so the button can confirm it briefly. */
  copiedId: string | null;
}

export function DictationHistoryGroup({
  label,
  entries,
  onCopy,
  onDiscard,
  onRestore,
  onDelete,
  busyIds,
  copiedId,
}: DictationHistoryGroupProps) {
  return (
    <section className="mt-4 first:mt-0" data-testid="dictation-history-group">
      <h5
        className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground"
        data-testid="dictation-history-group-label"
      >
        {label}
      </h5>
      <ul className="mt-1 divide-y divide-border/60">
        {entries.map((entry) => (
          <HistoryRow
            key={entry.id}
            entry={entry}
            busy={busyIds.has(entry.id)}
            copied={copiedId === entry.id}
            onCopy={() => onCopy(entry)}
            onDiscard={() => onDiscard(entry)}
            onRestore={() => onRestore(entry)}
            onDelete={() => onDelete(entry)}
          />
        ))}
      </ul>
    </section>
  );
}

function HistoryRow({
  entry,
  busy,
  copied,
  onCopy,
  onDiscard,
  onRestore,
  onDelete,
}: {
  entry: DictationEntry;
  busy: boolean;
  copied: boolean;
  onCopy: () => void;
  onDiscard: () => void;
  onRestore: () => void;
  onDelete: () => void;
}) {
  const t = useT();
  // Permanent deletion is a second, deliberate step: the trash icon only
  // discards, and this flag is what turns the discarded row's follow-up button
  // into the one that really removes the entry and its audio.
  const [confirmDelete, setConfirmDelete] = useState(false);

  const cleaned = Boolean(entry.raw_text) && entry.text !== entry.raw_text;
  // Both badges are computed here rather than inline so the render stays a
  // list of chips. A row from before either field existed carries neither.
  const polishBadge =
    entry.polish_status && entry.polish_status !== "off"
      ? entry.polish_status
      : "";
  const polishTitle = [
    entry.polish_provider || "",
    entry.polish_latency_ms ? `${Math.round(entry.polish_latency_ms)} ms` : "",
  ]
    .filter(Boolean)
    .join(" · ");
  const cleanupBadge =
    entry.cleanup_reason && entry.cleanup_reason !== "disabled"
      ? entry.cleanup_reason
      : "";
  // Restore is offered whenever there is something to win back: a soft-deleted
  // entry, a transcription that failed or only partly arrived, or kept audio
  // that can be run again. The two outcomes are named as well as the audio
  // flag because the flag says the sidecar was WRITTEN — a write that itself
  // failed would otherwise hide the button on exactly the rows that need it.
  const canRestore =
    entry.discarded ||
    entry.audio_available ||
    entry.outcome === "failed" ||
    entry.outcome === "partial";

  return (
    <li
      className="group flex items-start gap-2 py-2.5"
      data-testid="dictation-history-row"
      data-entry-id={entry.id}
    >
      <div className="min-w-0 flex-1">
        <p
          className={`break-words text-sm ${
            entry.discarded ? "text-muted-foreground line-through" : ""
          }`}
        >
          {entry.text || entry.raw_text}
        </p>
        {cleaned && (
          <p className="mt-0.5 break-words text-[11px] text-muted-foreground">
            {t("dictation.raw_prefix")} {entry.raw_text}
          </p>
        )}
        {entry.error && (
          <p
            className="mt-0.5 break-words text-[11px] text-destructive"
            data-testid="dictation-failure-reason"
          >
            {failureLabel(t, entry.error)}
          </p>
        )}
        <div className="mt-1 flex flex-wrap items-center gap-2 text-[10px] text-muted-foreground">
          <span>{new Date(entry.created_at).toLocaleTimeString()}</span>
          {entry.outcome && (
            <span
              className={`rounded-full border px-1.5 py-0.5 ${
                entry.outcome === "failed"
                  ? "border-destructive/40 bg-destructive/10 text-destructive"
                  : "border-border bg-muted/60"
              }`}
              data-testid="dictation-outcome-badge"
            >
              {outcomeLabel(t, entry.outcome)}
            </span>
          )}
          {entry.discarded && (
            <span
              className="rounded-full border border-border bg-muted/60 px-1.5 py-0.5"
              data-testid="dictation-discarded-badge"
            >
              {t("dictation.discarded_badge")}
            </span>
          )}
          {entry.audio_available && (
            <span className="flex items-center gap-1">
              <Volume2 className="h-3 w-3" />
              {t("dictation.audio_kept")}
            </span>
          )}
          {entry.removed_words > 0 && (
            <span>
              {t("dictation.removed_words").replace(
                "{0}",
                String(entry.removed_words),
              )}
            </span>
          )}
          {/* What the wording pass did to this row. "off" is the one value
              worth hiding — the feature being switched off is not an event,
              and a badge on every single row would be noise. Everything else
              is either "a model rewrote this" or "it did not, and here is
              why", and both are things the person who spoke deserves to see
              next to their own words. */}
          {polishBadge && (
            <span
              className="rounded-full border border-border bg-muted/60 px-1.5 py-0.5"
              data-testid="dictation-polish-badge"
              title={polishTitle || undefined}
            >
              {polishStatusLabel(t, polishBadge)}
            </span>
          )}
          {/* The filler cleanup's own verdict, and the reason this badge
              exists at all: outside its three rule languages the cleanup is a
              silent no-op, so a user dictating in Japanese or Polish saw the
              switch sitting ON while nothing ever happened. "disabled" is
              skipped — that one the user did themselves. */}
          {cleanupBadge && (
            <span
              className="rounded-full border border-border bg-muted/60 px-1.5 py-0.5"
              data-testid="dictation-cleanup-reason-badge"
            >
              {cleanupReasonLabel(t, cleanupBadge)}
            </span>
          )}
        </div>
        {entry.discarded && (
          <div className="mt-1.5">
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                if (!confirmDelete) {
                  setConfirmDelete(true);
                  return;
                }
                onDelete();
              }}
              data-testid="dictation-delete-permanently"
              className="rounded px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground transition hover:text-destructive disabled:opacity-50"
            >
              {confirmDelete
                ? `${t("dictation.delete_permanently")} ?`
                : t("dictation.delete_permanently")}
            </button>
          </div>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-0.5">
        <button
          type="button"
          onClick={onCopy}
          aria-label={copied ? t("dictation.copied") : t("dictation.copy")}
          title={copied ? t("dictation.copied") : t("dictation.copy")}
          data-testid="dictation-copy-entry"
          className="rounded p-1 text-muted-foreground transition hover:text-foreground"
        >
          {copied ? (
            <Check className="h-3.5 w-3.5 text-emerald-500" />
          ) : (
            <Copy className="h-3.5 w-3.5" />
          )}
        </button>
        {canRestore && (
          <button
            type="button"
            disabled={busy}
            onClick={onRestore}
            aria-label={t("dictation.restore")}
            title={t("dictation.restore_hint")}
            data-testid="dictation-restore-entry"
            className="rounded p-1 text-muted-foreground transition hover:text-primary disabled:opacity-50"
          >
            <RotateCcw className="h-3.5 w-3.5" />
          </button>
        )}
        {!entry.discarded && (
          <button
            type="button"
            disabled={busy}
            onClick={onDiscard}
            aria-label={t("dictation.discard")}
            title={t("dictation.discard")}
            data-testid="dictation-discard-entry"
            className="rounded p-1 text-muted-foreground opacity-0 transition group-hover:opacity-100 hover:text-destructive disabled:opacity-50"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        )}
      </div>
    </li>
  );
}

const KNOWN_OUTCOMES: ReadonlySet<string> = new Set(DICTATION_OUTCOMES);

const KNOWN_FAILURES: ReadonlySet<string> = new Set(STT_FAILURE_REASONS);

/**
 * Translates an outcome through its i18n key. A value this bundle does not
 * know (newer backend, older frontend) falls back to the raw string rather
 * than rendering a missing-key placeholder.
 */
function outcomeLabel(t: (key: string) => string, outcome: string): string {
  return KNOWN_OUTCOMES.has(outcome) ? t(`dictation.outcome.${outcome}`) : outcome;
}

/**
 * Translates a transcription failure through its i18n key.
 *
 * Unlike `outcomeLabel`, an unknown value does NOT fall through to the raw
 * string. This line exists to tell a person why their words did not arrive, and
 * the raw value here is either a stack-trace fragment stored by an older
 * version — the exact thing this replaced — or a reason code from a newer
 * backend, which is an identifier and explains nothing either. Both are better
 * served by the honest generic sentence; the technical detail is in the log,
 * where whoever needs it is already looking.
 */
function failureLabel(t: (key: string) => string, reason: string): string {
  return KNOWN_FAILURES.has(reason)
    ? t(`dictation.failure.${reason}`)
    : t("dictation.failure.unknown");
}
