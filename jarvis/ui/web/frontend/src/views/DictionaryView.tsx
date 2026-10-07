import { useMemo, useRef, useState, type ReactNode } from "react";
import {
  AlertTriangle,
  ArrowRight,
  BookA,
  Info,
  Loader2,
  Pencil,
  Plus,
  Search,
  Trash2,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { PageHeader } from "@/components/layout/PageHeader";
import { SkeletonBar } from "@/components/layout/PanelSkeleton";
import { useDictionary, type DictionaryEntry } from "@/hooks/useDictionary";
import { useEventStore } from "@/store/events";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { DictionaryEntryDialog } from "@/views/voice/DictionaryEntryDialog";
import { VoiceGroup, VoiceNote, VoicePage, VoiceSection, VoiceTag } from "@/views/voice/voiceUi";

/**
 * "Dictionary" — the custom vocabulary for speech recognition. Users add words
 * the recognizer keeps getting wrong (proper nouns, brand names, e-mail
 * addresses) either as a plain word or as an explicit "heard as → write it
 * as" correction. Entries apply to the NEXT utterance (the backend corrector
 * live-reloads) — no restart.
 *
 * Deleting is immediate and undoable from the toast: the undo re-adds the same
 * word and variants, which is all an entry holds.
 *
 * Backed by /api/dictionary (GET/POST/PATCH/DELETE) via useDictionary.
 */
export interface DictionaryViewProps {
  /**
   * Suppress this view's own header.
   *
   * Set by the merged voice section, which renders one "{name} Voice" header
   * above the tab bar — a second header right below it reads as a rendering
   * fault. Standalone rendering keeps its own header.
   */
  hideHeader?: boolean;
}

export function DictionaryView({ hideHeader = false }: DictionaryViewProps = {}) {
  const t = useT();
  const { entries, loading, error, createEntry, updateEntry, removeEntry } = useDictionary();
  const pushToast = useEventStore((s) => s.pushToast);
  const addButtonRef = useRef<HTMLButtonElement>(null);

  const [query, setQuery] = useState("");
  /** `undefined` while closed; `null` adds a new entry; an entry edits it. */
  const [editing, setEditing] = useState<DictionaryEntry | null | undefined>(undefined);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return entries;
    return entries.filter(
      (e) =>
        e.word.toLowerCase().includes(q) || e.misheard.some((m) => m.toLowerCase().includes(q)),
    );
  }, [entries, query]);

  async function onDelete(entry: DictionaryEntry) {
    setDeletingId(entry.id);
    try {
      await removeEntry(entry.id);
      // The row and its focused button are gone; land on the one control
      // that is always there instead of dropping focus to the page.
      addButtonRef.current?.focus();
      pushToast("success", t("dictionary.deleted").replace("{0}", entry.word), {
        action: {
          label: t("dictionary.undo"),
          onAction: async () => {
            try {
              await createEntry({ word: entry.word, misheard: entry.misheard });
            } catch (e) {
              pushToast("error", (e as Error).message);
            }
          },
        },
      });
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setDeletingId(null);
    }
  }

  const openAdd = () => setEditing(null);

  return (
    <VoicePage testId="dictionary-page">
      {!hideHeader && (
        <PageHeader
          className="py-0"
          icon={<BookA />}
          title={t("dictionary.title")}
          actions={<VoiceTag>{t("dictionary.research_preview")}</VoiceTag>}
        />
      )}

      <VoiceSection
        title={
          <span className="inline-flex items-center gap-2">
            {t("dictionary.title")}
            {!loading && entries.length > 0 && (
              <VoiceTag>
                <span className="tabular-nums" data-testid="dictionary-count">
                  {entries.length.toLocaleString()}
                </span>
              </VoiceTag>
            )}
          </span>
        }
        description={t("dictionary.description")}
        actions={
          <>
            {!loading && entries.length > 0 && (
              <div className="relative w-48 sm:w-56">
                <Search
                  aria-hidden="true"
                  className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
                />
                <Input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder={t("dictionary.search")}
                  aria-label={t("dictionary.search")}
                  data-testid="dictionary-search"
                  className="h-8 pl-8 text-sm"
                />
              </div>
            )}
            <Button ref={addButtonRef} size="sm" onClick={openAdd} data-testid="dictionary-add">
              <Plus aria-hidden="true" />
              {t("dictionary.add")}
            </Button>
          </>
        }
      >
        {error && (
          <VoiceNote tone="error" icon={<AlertTriangle />} testId="dictionary-error">
            {error}
          </VoiceNote>
        )}

        {loading ? (
          <div aria-busy="true" aria-label={t("dictionary.loading")}>
            <VoiceGroup>
              {[0, 1, 2].map((i) => (
                <div key={i} className="flex items-center gap-3 px-4 py-3.5 sm:px-5">
                  <SkeletonBar className="h-4 w-24" />
                  <SkeletonBar className="h-4 w-4" />
                  <SkeletonBar className="h-4 w-32" />
                </div>
              ))}
            </VoiceGroup>
          </div>
        ) : entries.length === 0 ? (
          !error && (
            <VoiceGroup testId="dictionary-empty">
              <EmptyState
                icon={<BookA />}
                title={t("dictionary.empty_title")}
                description={t("dictionary.empty_body")}
                actions={
                  <Button size="sm" onClick={openAdd} data-testid="dictionary-empty-add">
                    <Plus aria-hidden="true" />
                    {t("dictionary.add")}
                  </Button>
                }
              />
            </VoiceGroup>
          )
        ) : filtered.length === 0 ? (
          <VoiceGroup>
            <p
              className="px-5 py-10 text-center text-sm text-muted-foreground"
              data-testid="dictionary-no-matches"
            >
              {t("dictionary.no_matches")}
            </p>
          </VoiceGroup>
        ) : (
          <VoiceGroup divided={false}>
            <ul className="divide-y divide-border" data-testid="dictionary-list">
              {filtered.map((entry) => (
                <DictionaryRow
                  key={entry.id}
                  entry={entry}
                  deleting={deletingId === entry.id}
                  onEdit={() => setEditing(entry)}
                  onDelete={() => void onDelete(entry)}
                />
              ))}
            </ul>
          </VoiceGroup>
        )}

        {/* The corrections already reach dictation — the pipeline wraps the
            same speech-to-text handle a dictation uses — but nothing said so,
            and a vocabulary you do not know applies is a vocabulary you do not
            use. */}
        <VoiceNote icon={<Info />} testId="dictionary-notes">
          <p>{t("dictionary.applies_note")}</p>
          <p className="mt-1 text-muted-foreground">{t("dictionary.applies_to_dictation")}</p>
        </VoiceNote>
      </VoiceSection>

      {editing !== undefined && (
        <DictionaryEntryDialog
          initial={editing}
          onClose={() => setEditing(undefined)}
          onSave={async (payload) => {
            if (editing) {
              await updateEntry(editing.id, payload);
              pushToast("success", t("dictionary.saved"));
            } else {
              await createEntry(payload);
              pushToast("success", t("dictionary.added").replace("{0}", payload.word));
            }
            setEditing(undefined);
          }}
        />
      )}
    </VoicePage>
  );
}

/** One entry: "heard as → written" for a correction, the word alone otherwise. */
function DictionaryRow({
  entry,
  deleting,
  onEdit,
  onDelete,
}: {
  entry: DictionaryEntry;
  deleting: boolean;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const t = useT();
  const correction = entry.misheard.length > 0;
  return (
    <li
      className="group flex items-center gap-3 px-4 py-2.5 sm:px-5"
      data-testid="dictionary-row"
      data-entry-id={entry.id}
    >
      <div className="min-w-0 flex-1 text-sm">
        {correction ? (
          <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="break-words text-muted-foreground">{entry.misheard.join(", ")}</span>
            <ArrowRight aria-hidden="true" className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
            <span className="sr-only">→</span>
            <span className="break-words font-medium text-foreground-strong">{entry.word}</span>
          </span>
        ) : (
          <span className="break-words font-medium text-foreground-strong">{entry.word}</span>
        )}
      </div>
      <VoiceTag className="hidden sm:inline-flex">
        {correction ? t("dictionary.kind_correction") : t("dictionary.kind_word")}
      </VoiceTag>
      {/* Hold their place at rest so nothing shifts; shown with the pointer,
          with keyboard focus in the row, and always on a touch screen. */}
      <div className="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100 group-focus-within:opacity-100 motion-reduce:transition-none [@media(hover:none)]:opacity-100">
        <RowButton
          label={`${t("dictionary.edit")}: ${entry.word}`}
          onClick={onEdit}
          testId={`dictionary-edit-${entry.id}`}
        >
          <Pencil aria-hidden="true" />
        </RowButton>
        <RowButton
          label={`${t("dictionary.delete")}: ${entry.word}`}
          onClick={onDelete}
          disabled={deleting}
          testId={`dictionary-delete-${entry.id}`}
          destructive
        >
          {deleting ? (
            <Loader2 aria-hidden="true" className="animate-spin motion-reduce:animate-none" />
          ) : (
            <Trash2 aria-hidden="true" />
          )}
        </RowButton>
      </div>
    </li>
  );
}

function RowButton({
  children,
  label,
  onClick,
  testId,
  disabled,
  destructive,
}: {
  children: ReactNode;
  label: string;
  onClick: () => void;
  testId: string;
  disabled?: boolean;
  destructive?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      title={label}
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
