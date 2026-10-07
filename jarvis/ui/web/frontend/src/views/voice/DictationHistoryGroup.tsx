import { useState, type ReactNode } from "react";
import { Check, ChevronDown, Copy, RotateCcw, Trash2, Volume2 } from "lucide-react";

import {
  cleanupReasonLabel,
  DICTATION_OUTCOMES,
  polishStatusLabel,
  STT_FAILURE_REASONS,
  type DictationEntry,
} from "@/hooks/useDictation";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { VoiceGroup, VoiceTag } from "@/views/voice/voiceUi";

/**
 * One day's worth of dictations — a quiet date label over one grouped list.
 *
 * Grouping by day turns a flat list into something you can read back: "what
 * did I dictate this morning" is a question about a day, not about entry
 * number 34.
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
    <section className="flex flex-col gap-2" data-testid="dictation-history-group">
      <h3
        className="px-1 text-xs font-medium text-muted-foreground"
        data-testid="dictation-history-group-label"
      >
        {label}
      </h3>
      <VoiceGroup divided={false}>
        <ul className="divide-y divide-border">
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
      </VoiceGroup>
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
function outcomeTone(outcome: string): "error" | "warning" | null {
  if (outcome === "failed") return "error";
  if (outcome === "unavailable" || outcome === "partial" || outcome === "empty") {
    return "warning";
  }
  return null;
}

/** Past this many characters a transcript folds to four lines with a toggle. */
const LONG_TEXT = 280;

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
  // discards, and arming this is what lets the discarded row's follow-up
  // button remove the entry and its audio for good.
  const [confirmDelete, setConfirmDelete] = useState(false);
  // What the recognizer actually heard stays one click away instead of being
  // printed under every row — it is there to check a cleanup, not to be read
  // twice.
  const [showRaw, setShowRaw] = useState(false);
  const [expanded, setExpanded] = useState(false);

  const text = entry.text || entry.raw_text;
  const long = text.length > LONG_TEXT;
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
  const tone = entry.outcome ? outcomeTone(entry.outcome) : null;

  return (
    <li
      className="group grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 px-4 py-3.5 sm:grid-cols-[4.5rem_minmax(0,1fr)_auto] sm:gap-x-4 sm:px-5"
      data-testid="dictation-history-row"
      data-entry-id={entry.id}
    >
      {/* Its own column from `sm` up; above the text on a phone-width pane so
          the transcript keeps the width. */}
      <time
        dateTime={entry.created_at}
        className="col-span-2 mb-1 text-sm tabular-nums text-muted-foreground sm:col-span-1 sm:mb-0 sm:pt-px"
      >
        {formatTime(entry.created_at)}
      </time>

      <div className="min-w-0">
        <p
          className={cn(
            "whitespace-pre-wrap break-words text-base text-foreground",
            entry.discarded && "text-muted-foreground line-through",
            long && !expanded && "line-clamp-4",
          )}
          data-testid="dictation-entry-text"
        >
          {text}
        </p>
        {long && (
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            aria-expanded={expanded}
            data-testid="dictation-toggle-more"
            className="mt-1 rounded-sm text-sm font-medium text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {expanded ? t("dictation.show_less") : t("dictation.show_more")}
          </button>
        )}
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
            className="mt-2 break-words rounded-md bg-secondary px-3 py-2 text-sm text-muted-foreground"
            data-testid="dictation-raw-text"
          >
            <span className="font-medium text-foreground">{t("dictation.raw_prefix")}</span>{" "}
            {entry.raw_text}
          </p>
        )}

        <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-xs text-muted-foreground">
          {entry.outcome &&
            (tone ? (
              <VoiceTag tone={tone}>
                <span data-testid="dictation-outcome-badge">{outcomeLabel(t, entry.outcome)}</span>
              </VoiceTag>
            ) : (
              <span data-testid="dictation-outcome-badge">{outcomeLabel(t, entry.outcome)}</span>
            ))}
          {entry.discarded && (
            <VoiceTag>
              <span data-testid="dictation-discarded-badge">{t("dictation.discarded_badge")}</span>
            </VoiceTag>
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
            <MetaButton
              onClick={() => setShowRaw((v) => !v)}
              ariaExpanded={showRaw}
              testId="dictation-toggle-raw"
            >
              {showRaw ? t("dictation.hide_original") : t("dictation.show_original")}
              <ChevronDown
                aria-hidden="true"
                className={cn(
                  "h-3 w-3 transition-transform motion-reduce:transition-none",
                  showRaw && "rotate-180",
                )}
              />
            </MetaButton>
          )}
          {entry.discarded &&
            (confirmDelete ? (
              <span className="inline-flex items-center gap-2">
                <MetaButton
                  disabled={busy}
                  onClick={onDelete}
                  testId="dictation-delete-permanently"
                  destructive
                >
                  {t("dictation.delete_permanently_confirm")}
                </MetaButton>
                <MetaButton onClick={() => setConfirmDelete(false)} testId="dictation-delete-cancel">
                  {t("dictation.cancel")}
                </MetaButton>
              </span>
            ) : (
              <MetaButton
                disabled={busy}
                onClick={() => setConfirmDelete(true)}
                testId="dictation-delete-permanently"
              >
                {t("dictation.delete_permanently")}
              </MetaButton>
            ))}
        </div>
      </div>

      {/* The row's actions come up with the pointer or with keyboard focus
          anywhere in the row; they always hold their place, so nothing shifts,
          and stay visible on a touch screen that cannot hover. */}
      <div className="-my-1 flex items-start gap-0.5 opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100 group-focus-within:opacity-100 motion-reduce:transition-none [@media(hover:none)]:opacity-100">
        <RowAction
          onClick={onCopy}
          label={copied ? t("dictation.copied") : t("dictation.copy")}
          testId="dictation-copy-entry"
        >
          {copied ? <Check aria-hidden="true" /> : <Copy aria-hidden="true" />}
        </RowAction>
        {canRestore && (
          <RowAction
            disabled={busy}
            onClick={onRestore}
            label={t("dictation.restore")}
            title={t("dictation.restore_hint")}
            testId="dictation-restore-entry"
          >
            <RotateCcw aria-hidden="true" />
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
            <Trash2 aria-hidden="true" />
          </RowAction>
        )}
      </div>
    </li>
  );
}

/** A quiet label in the row's meta line. */
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
    <span className="inline-flex items-center gap-1" data-testid={testId} title={title}>
      {children}
    </span>
  );
}

/** A text button in the meta line, at the meta line's size. */
function MetaButton({
  children,
  onClick,
  testId,
  ariaExpanded,
  disabled,
  destructive,
}: {
  children: ReactNode;
  onClick: () => void;
  testId: string;
  ariaExpanded?: boolean;
  disabled?: boolean;
  destructive?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-expanded={ariaExpanded}
      data-testid={testId}
      className={cn(
        "inline-flex min-h-6 items-center gap-0.5 rounded-sm text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50",
        destructive
          ? "font-medium text-destructive hover:text-destructive/80"
          : "text-muted-foreground hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}

/** One 32 px icon button in a row's action strip. Only discarding keeps a hue. */
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
        "inline-flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50 [&>svg]:h-4 [&>svg]:w-4",
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
  return date.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
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
