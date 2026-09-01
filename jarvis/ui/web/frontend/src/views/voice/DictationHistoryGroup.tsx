import { useState, type ReactNode } from "react";
import { Check, Copy, RotateCcw, Trash2, Volume2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import {
  cleanupReasonLabel,
  DICTATION_OUTCOMES,
  polishStatusLabel,
  STT_FAILURE_REASONS,
  type DictationEntry,
} from "@/hooks/useDictation";
import { useT } from "@/i18n";

/**
 * One day's worth of dictations — a date header plus its rows.
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
    <section className="mt-group first:mt-0" data-testid="dictation-history-group">
      {/* A quiet date divider, not a heading. It used to be an 11px uppercase
          label with letter-spacing — the one construction that makes a screen
          read as an admin panel, and it competed with the card title two lines
          above it for no reason. */}
      <h5
        className="text-meta text-muted-foreground"
        data-testid="dictation-history-group-label"
      >
        {label}
      </h5>
      {/* No dividers. Separation between rows is the hover fill and the
          padding, the way both reference apps do it; a rule under every row
          drew a table over what is meant to read as a transcript. */}
      <ul className="mt-1">
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
function outcomeVariant(outcome: string): "fault" | "degraded" | "secondary" {
  if (outcome === "failed") return "fault";
  if (outcome === "unavailable" || outcome === "partial" || outcome === "empty") {
    return "degraded";
  }
  return "secondary";
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
      // The hover fill is drawn on the WHOLE row, inset from the card's own
      // padding rather than on any one control inside it.
      className="group -mx-2 flex items-start gap-2 rounded-md px-3 py-3 transition-colors hover:bg-secondary"
      data-testid="dictation-history-row"
      data-entry-id={entry.id}
    >
      <div className="min-w-0 flex-1">
        {/* The transcript is the reason this screen exists, so it is set as
            prose — 15/1.6 in body ink — instead of as another 14px interface
            label. Its measure is bounded by the column, not by the window. */}
        <p
          className={`break-words text-reading ${
            entry.discarded
              ? "text-muted-foreground line-through"
              : "text-foreground"
          }`}
        >
          {entry.text || entry.raw_text}
        </p>
        {cleaned && (
          <p className="mt-1 break-words text-meta text-muted-foreground">
            {t("dictation.raw_prefix")} {entry.raw_text}
          </p>
        )}
        {entry.error && (
          <p
            className="mt-1 break-words text-meta text-destructive"
            data-testid="dictation-failure-reason"
          >
            {failureLabel(t, entry.error)}
          </p>
        )}
        <div className="mt-2 flex flex-wrap items-center gap-2 text-meta text-muted-foreground">
          <span>{new Date(entry.created_at).toLocaleTimeString()}</span>
          {entry.outcome && (
            <Badge
              variant={outcomeVariant(entry.outcome)}
              data-testid="dictation-outcome-badge"
            >
              {outcomeLabel(t, entry.outcome)}
            </Badge>
          )}
          {entry.discarded && (
            <Badge variant="secondary" data-testid="dictation-discarded-badge">
              {t("dictation.discarded_badge")}
            </Badge>
          )}
          {entry.audio_available && (
            <Badge variant="secondary" className="gap-1">
              <Volume2 aria-hidden="true" className="h-3 w-3" />
              {t("dictation.audio_kept")}
            </Badge>
          )}
          {/* What the wording pass did to this row. "off" is the one value
              worth hiding — the feature being switched off is not an event,
              and a badge on every single row would be noise. Everything else
              is either "a model rewrote this" or "it did not, and here is
              why", and both are things the person who spoke deserves to see
              next to their own words. */}
          {polishBadge && (
            <Badge
              variant="secondary"
              data-testid="dictation-polish-badge"
              title={polishTitle || undefined}
            >
              {polishStatusLabel(t, polishBadge)}
            </Badge>
          )}
          {/* The filler cleanup's own verdict, and the reason this badge
              exists at all: outside its three rule languages the cleanup is a
              silent no-op, so a user dictating in Japanese or Polish saw the
              switch sitting ON while nothing ever happened. "disabled" is
              skipped — that one the user did themselves. */}
          {cleanupBadge && (
            <Badge
              variant="secondary"
              data-testid="dictation-cleanup-reason-badge"
            >
              {cleanupReasonLabel(t, cleanupBadge)}
            </Badge>
          )}
        </div>
        {entry.discarded && (
          <div className="mt-2">
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
              className="rounded-md text-meta text-muted-foreground transition-colors hover:text-destructive focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-border-strong disabled:opacity-50"
            >
              {confirmDelete
                ? `${t("dictation.delete_permanently")} ?`
                : t("dictation.delete_permanently")}
            </button>
          </div>
        )}
      </div>
      {/* Row actions appear when the row does. They are still in the document
          and still reachable by keyboard — focus inside the row reveals them
          the same way the pointer does. */}
      <div className="flex shrink-0 items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
        <RowAction
          onClick={onCopy}
          label={copied ? t("dictation.copied") : t("dictation.copy")}
          testId="dictation-copy-entry"
        >
          {copied ? (
            <Check aria-hidden="true" className="h-3.5 w-3.5" />
          ) : (
            <Copy aria-hidden="true" className="h-3.5 w-3.5" />
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
            <RotateCcw aria-hidden="true" className="h-3.5 w-3.5" />
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
            <Trash2 aria-hidden="true" className="h-3.5 w-3.5" />
          </RowAction>
        )}
      </div>
    </li>
  );
}

/**
 * One icon button in a row's action strip.
 *
 * The three used to hover to three different inks — foreground, --primary and
 * --destructive — which made the same gesture mean three things. Now they all
 * answer the pointer the way every other control in the app does: one step up
 * the surface ladder. Only discarding, which changes something, keeps a hue.
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
      className={`rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-popover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-border-strong disabled:opacity-50 ${
        destructive ? "hover:text-destructive" : "hover:text-foreground"
      }`}
    >
      {children}
    </button>
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
