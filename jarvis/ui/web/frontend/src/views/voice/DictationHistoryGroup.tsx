import { useState, type ReactNode } from "react";
import { Check, ChevronDown, Copy, RotateCcw, Trash2, Volume2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import {
  cleanupReasonLabel,
  DICTATION_OUTCOMES,
  polishStatusLabel,
  STT_FAILURE_REASONS,
  type DictationEntry,
} from "@/hooks/useDictation";
import { useI18nStore, useT } from "@/i18n";
import { localeForUiLanguage } from "@/components/runs/format";
import { cn } from "@/lib/utils";

/**
 * One day's worth of dictations — a small date label and one framed list.
 *
 * Grouping by day is what turns a flat list into something you can actually
 * read back: "what did I dictate this morning" is a question about a day, not
 * about entry number 34. Each day is one bordered surface with hairlines
 * between its rows, so a day reads as a single page of a journal rather than
 * as a pile of separate cards.
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
    <section data-testid="dictation-history-group">
      <h5
        className="px-1 text-xs font-semibold uppercase tracking-[0.08em] text-muted-foreground"
        data-testid="dictation-history-group-label"
      >
        {label}
      </h5>
      <ul className="mt-2 divide-y divide-border overflow-hidden rounded-xl border border-border bg-card shadow-rim">
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

/**
 * Which status hue an outcome earns.
 *
 * Only two outcomes carry colour at all. A failure is a fault; the three that
 * mean "some of this did not arrive" are degraded. Everything that worked
 * stays neutral — a green chip on every successful row would put hue on 90 %
 * of the list and leave the two rows that need looking at with nowhere louder
 * to go. "Cancelled" in particular stays quiet: a bright chip on the one
 * outcome the user caused themselves inverts the whole ramp.
 */
function outcomeVariant(outcome: string): "fault" | "degraded" | null {
  if (outcome === "failed") return "fault";
  if (outcome === "unavailable" || outcome === "partial" || outcome === "empty") {
    return "degraded";
  }
  return null;
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
  // What the recognizer actually heard stays one click away instead of being
  // printed under every row — it is there to check a cleanup, not to be read
  // twice.
  const [showRaw, setShowRaw] = useState(false);

  const cleaned = Boolean(entry.raw_text) && entry.text !== entry.raw_text;
  // A row from before either field existed carries neither badge. "off" is the
  // one polish value worth hiding — the feature being switched off is not an
  // event; everything else is either "a model rewrote this" or "it did not,
  // and here is why", and the person who spoke deserves to see both.
  const polishBadge =
    entry.polish_status && entry.polish_status !== "off" ? entry.polish_status : "";
  const polishTitle = [
    entry.polish_provider || "",
    entry.polish_latency_ms ? `${Math.round(entry.polish_latency_ms)} ms` : "",
  ]
    .filter(Boolean)
    .join(" · ");
  // The filler cleanup's own verdict: outside its rule languages the cleanup
  // is a silent no-op, so a user dictating in Japanese or Polish saw the switch
  // sitting ON while nothing happened. "disabled" is skipped — that one the
  // user did themselves.
  const cleanupBadge =
    entry.cleanup_reason && entry.cleanup_reason !== "disabled" ? entry.cleanup_reason : "";
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
  const variant = entry.outcome ? outcomeVariant(entry.outcome) : null;

  return (
    <li
      className="group grid grid-cols-[64px_minmax(0,1fr)_auto] gap-x-4 px-5 py-4 transition-colors hover:bg-secondary/60 focus-within:bg-secondary/60 sm:grid-cols-[84px_minmax(0,1fr)_auto]"
      data-testid="dictation-history-row"
      data-entry-id={entry.id}
    >
      <time
        dateTime={entry.created_at}
        className="pt-0.5 text-sm tabular-nums text-muted-foreground"
      >
        {formatTime(entry.created_at)}
      </time>

      <div className="min-w-0">
        {/* The transcript is the reason this screen exists, so it is set as
            prose in body ink; everything else on the row is smaller and
            quieter than it. */}
        <p
          className={cn(
            "whitespace-pre-wrap break-words text-base leading-6",
            entry.discarded ? "text-muted-foreground line-through" : "text-foreground",
          )}
        >
          {entry.text || entry.raw_text}
        </p>
        {entry.error && (
          <p
            className="mt-1 break-words text-sm text-destructive"
            data-testid="dictation-failure-reason"
          >
            {failureLabel(t, entry.error)}
          </p>
        )}
        {cleaned && showRaw && (
          <p
            className="mt-2 break-words border-l-2 border-border-strong pl-3 text-sm text-muted-foreground"
            data-testid="dictation-raw-text"
          >
            {t("dictation.raw_prefix")} {entry.raw_text}
          </p>
        )}

        <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
          {entry.outcome &&
            (variant ? (
              <Badge variant={variant} data-testid="dictation-outcome-badge">
                {outcomeLabel(t, entry.outcome)}
              </Badge>
            ) : (
              <span data-testid="dictation-outcome-badge">{outcomeLabel(t, entry.outcome)}</span>
            ))}
          {entry.discarded && (
            <Badge variant="secondary" data-testid="dictation-discarded-badge">
              {t("dictation.discarded_badge")}
            </Badge>
          )}
          {polishBadge && (
            <MetaItem testId="dictation-polish-badge" title={polishTitle || undefined}>
              {polishStatusLabel(t, polishBadge)}
            </MetaItem>
          )}
          {cleanupBadge && (
            <MetaItem testId="dictation-cleanup-reason-badge">
              {cleanupReasonLabel(t, cleanupBadge)}
            </MetaItem>
          )}
          {entry.audio_available && (
            <MetaItem>
              <Volume2 aria-hidden="true" className="h-3 w-3" />
              {t("dictation.audio_kept")}
            </MetaItem>
          )}
          {cleaned && (
            <button
              type="button"
              onClick={() => setShowRaw((v) => !v)}
              aria-expanded={showRaw}
              data-testid="dictation-toggle-raw"
              className="inline-flex items-center gap-0.5 rounded-sm text-xs text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-border-strong"
            >
              <Dot />
              {showRaw ? t("dictation.hide_original") : t("dictation.show_original")}
              <ChevronDown
                aria-hidden="true"
                className={cn("h-3 w-3 transition-transform", showRaw && "rotate-180")}
              />
            </button>
          )}
          {entry.discarded && (
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
              className={cn(
                "rounded-sm text-xs transition-colors hover:text-destructive focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-border-strong disabled:opacity-50",
                confirmDelete ? "font-medium text-destructive" : "text-muted-foreground",
              )}
            >
              {confirmDelete
                ? `${t("dictation.delete_permanently")} ?`
                : t("dictation.delete_permanently")}
            </button>
          )}
        </div>
      </div>

      {/* Row actions appear with the row's hover. They stay in the document
          and reachable by keyboard — focus inside the row reveals them the
          same way the pointer does. */}
      <div className="-my-1 flex shrink-0 items-start gap-0.5 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
        <RowAction
          onClick={onCopy}
          label={copied ? t("dictation.copied") : t("dictation.copy")}
          testId="dictation-copy-entry"
        >
          {copied ? (
            <Check aria-hidden="true" className="h-4 w-4" />
          ) : (
            <Copy aria-hidden="true" className="h-4 w-4" />
          )}
        </RowAction>
        {canRestore && (
          <RowAction
            disabled={busy}
            onClick={onRestore}
            label={t("dictation.restore")}
            title={t("dictation.restore_hint")}
            testId="dictation-restore-entry"
          >
            <RotateCcw aria-hidden="true" className="h-4 w-4" />
          </RowAction>
        )}
        {!entry.discarded && (
          <RowAction
            disabled={busy}
            onClick={onDiscard}
            label={t("dictation.discard")}
            testId="dictation-discard-entry"
            destructive
          >
            <Trash2 aria-hidden="true" className="h-4 w-4" />
          </RowAction>
        )}
      </div>
    </li>
  );
}

/** A quiet "· label" in the row's meta line. */
function MetaItem({
  children,
  testId,
  title,
}: {
  children: ReactNode;
  testId?: string;
  title?: string;
}) {
  return (
    <span className="inline-flex items-center">
      <Dot />
      <span className="inline-flex items-center gap-1" data-testid={testId} title={title}>
        {children}
      </span>
    </span>
  );
}

function Dot() {
  return (
    <span aria-hidden="true" className="mr-1 text-faint-foreground">
      ·
    </span>
  );
}

/**
 * One icon button in a row's action strip. All three answer the pointer the
 * same way — one step up the surface ladder; only discarding, which changes
 * something, keeps a hue.
 */
function RowAction({
  children,
  label,
  title,
  testId,
  onClick,
  disabled,
  destructive,
}: {
  children: ReactNode;
  label: string;
  title?: string;
  testId: string;
  onClick: () => void;
  disabled?: boolean;
  destructive?: boolean;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      aria-label={label}
      title={title ?? label}
      data-testid={testId}
      className={cn(
        "rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-surface-raised focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-border-strong disabled:opacity-50",
        destructive ? "hover:text-destructive" : "hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}

/** "4:12 PM" / "16:12" — the viewer's own clock format, no seconds. */
function formatTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString(localeForUiLanguage(useI18nStore.getState().ui), { hour: "numeric", minute: "2-digit" });
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
 * version or a reason code from a newer backend — an identifier that explains
 * nothing. Both are better served by the honest generic sentence; the
 * technical detail is in the log.
 */
function failureLabel(t: (key: string) => string, reason: string): string {
  return KNOWN_FAILURES.has(reason)
    ? t(`dictation.failure.${reason}`)
    : t("dictation.failure.unknown");
}
