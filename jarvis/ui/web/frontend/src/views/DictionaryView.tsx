import { useMemo, useState } from "react";
import {
  ArrowRight,
  BookA,
  Loader2,
  Pencil,
  Plus,
  Search,
  Trash2,
  X,
} from "lucide-react";
import { ViewHeader } from "@/views/ChatsView";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import {
  useDictionary,
  type DictionaryEntry,
} from "@/hooks/useDictionary";
import { useEventStore } from "@/store/events";
import { useT } from "@/i18n";

/**
 * "Dictionary" section — the Wispr-Flow-style custom vocabulary for speech
 * recognition, as its own sidebar destination. Users add words the STT keeps
 * getting wrong (proper nouns, brand names, e-mail addresses) either as a
 * plain vocabulary word or as an explicit "misheard → correct" fix. Entries
 * apply to the NEXT utterance (the backend corrector live-reloads) — no
 * restart.
 *
 * Backed by /api/dictionary (GET/POST/PATCH/DELETE) via useDictionary.
 */
export function DictionaryView() {
  const t = useT();
  const { entries, loading, error, createEntry, updateEntry, removeEntry } =
    useDictionary();
  const pushToast = useEventStore((s) => s.pushToast);

  const [query, setQuery] = useState("");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<DictionaryEntry | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return entries;
    return entries.filter(
      (e) =>
        e.word.toLowerCase().includes(q) ||
        e.misheard.some((m) => m.toLowerCase().includes(q)),
    );
  }, [entries, query]);

  async function onDelete(entry: DictionaryEntry) {
    setDeletingId(entry.id);
    try {
      await removeEntry(entry.id);
      pushToast("success", t("dictionary.deleted").replace("{0}", entry.word));
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <div className="flex h-full flex-col">
      <ViewHeader
        icon={<BookA className="h-4 w-4 text-primary" />}
        title={t("dictionary.title")}
        titleBadge={
          <span className="shrink-0 rounded-full border border-primary/30 bg-primary/10 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-primary">
            {t("dictionary.research_preview")}
          </span>
        }
        subtitle={t("dictionary.description")}
      />
      <div className="flex-1 overflow-y-auto scrollbar-jarvis p-6">
        <div className="mx-auto max-w-3xl">
          <div className="flex items-center gap-3">
            <div className="relative flex-1">
              <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={t("dictionary.search")}
                data-testid="dictionary-search"
                className="w-full rounded-md border border-input bg-background py-1.5 pl-8 pr-3 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
              />
            </div>
            {entries.length > 0 && (
              <span className="rounded-full border border-border bg-muted/60 px-2 py-0.5 text-[10px] font-medium text-muted-foreground">
                {entries.length}
              </span>
            )}
            <Button
              size="sm"
              className="gap-1.5"
              data-testid="dictionary-add"
              onClick={() => {
                setEditing(null);
                setDialogOpen(true);
              }}
            >
              <Plus className="h-3.5 w-3.5" />
              {t("dictionary.add")}
            </Button>
          </div>

          {error && <p className="mt-3 text-xs text-destructive">{error}</p>}

          <div className="mt-4 rounded-lg border border-border bg-card/60 px-4 py-2">
            {loading ? (
              <div className="flex items-center gap-2 py-3 text-xs text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                {t("dictionary.loading")}
              </div>
            ) : entries.length === 0 ? (
              <p className="py-3 text-xs text-muted-foreground">
                {t("dictionary.empty")}
              </p>
            ) : filtered.length === 0 ? (
              <p className="py-3 text-xs text-muted-foreground">
                {t("dictionary.no_matches")}
              </p>
            ) : (
              <ul className="divide-y divide-border/60" data-testid="dictionary-list">
                {filtered.map((entry) => (
                  <li
                    key={entry.id}
                    className="group flex items-center gap-2 py-2.5"
                  >
                    <div className="min-w-0 flex-1 text-sm">
                      {entry.misheard.length > 0 ? (
                        <span className="flex flex-wrap items-center gap-1.5">
                          <span className="text-muted-foreground">
                            {entry.misheard.join(", ")}
                          </span>
                          <ArrowRight className="h-3 w-3 shrink-0 text-muted-foreground" />
                          <span className="font-medium">{entry.word}</span>
                        </span>
                      ) : (
                        <span className="font-medium">{entry.word}</span>
                      )}
                    </div>
                    <button
                      type="button"
                      aria-label={t("dictionary.edit")}
                      data-testid={`dictionary-edit-${entry.id}`}
                      onClick={() => {
                        setEditing(entry);
                        setDialogOpen(true);
                      }}
                      className="text-muted-foreground opacity-0 transition-opacity hover:text-foreground focus:opacity-100 group-hover:opacity-100"
                    >
                      <Pencil className="h-3.5 w-3.5" />
                    </button>
                    <button
                      type="button"
                      aria-label={t("dictionary.delete")}
                      data-testid={`dictionary-delete-${entry.id}`}
                      disabled={deletingId === entry.id}
                      onClick={() => void onDelete(entry)}
                      className="text-muted-foreground opacity-0 transition-opacity hover:text-destructive focus:opacity-100 group-hover:opacity-100 disabled:opacity-50"
                    >
                      {deletingId === entry.id ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <Trash2 className="h-3.5 w-3.5" />
                      )}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <p className="mt-3 text-[11px] text-muted-foreground">
            {t("dictionary.applies_note")}
          </p>
        </div>
      </div>

      {dialogOpen && (
        <DictionaryEntryDialog
          initial={editing}
          onClose={() => setDialogOpen(false)}
          onSave={async (payload) => {
            if (editing) {
              await updateEntry(editing.id, payload);
              pushToast("success", t("dictionary.saved"));
            } else {
              await createEntry(payload);
              pushToast(
                "success",
                t("dictionary.added").replace("{0}", payload.word),
              );
            }
            setDialogOpen(false);
          }}
        />
      )}
    </div>
  );
}

const inputClass =
  "w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary";

/**
 * Add/edit dialog mirroring Wispr Flow: the "correct a misrecognition"
 * toggle switches between a single new-word input and a
 * "misheard → correct spelling" pair.
 */
function DictionaryEntryDialog({
  initial,
  onClose,
  onSave,
}: {
  initial: DictionaryEntry | null;
  onClose: () => void;
  onSave: (payload: { word: string; misheard: string[] }) => Promise<void>;
}) {
  const t = useT();
  const [word, setWord] = useState(initial?.word ?? "");
  const [misheardText, setMisheardText] = useState(
    initial?.misheard.join(", ") ?? "",
  );
  const [isCorrection, setIsCorrection] = useState(
    (initial?.misheard.length ?? 0) > 0,
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const misheard = isCorrection
    ? misheardText
        .split(",")
        .map((m) => m.trim())
        .filter(Boolean)
    : [];
  const valid =
    word.trim().length > 0 && (!isCorrection || misheard.length > 0);

  async function submit() {
    if (!valid || saving) return;
    setSaving(true);
    setError(null);
    try {
      await onSave({ word: word.trim(), misheard });
    } catch (e) {
      setError((e as Error).message);
      setSaving(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-xl border border-border bg-card p-6 shadow-xl"
        onClick={(e) => e.stopPropagation()}
        data-testid="dictionary-dialog"
      >
        <header className="mb-4 flex items-center gap-3">
          <h3 className="flex-1 font-display text-base font-semibold">
            {initial
              ? t("dictionary.dialog_edit_title")
              : t("dictionary.dialog_add_title")}
          </h3>
          <button
            type="button"
            onClick={onClose}
            aria-label={t("dictionary.cancel")}
            className="text-muted-foreground hover:text-foreground"
          >
            <X className="h-4 w-4" />
          </button>
        </header>

        <div className="space-y-4">
          <label className="flex items-center justify-between gap-3">
            <span className="text-sm">{t("dictionary.correction_toggle")}</span>
            <Switch
              checked={isCorrection}
              onCheckedChange={setIsCorrection}
              data-testid="dictionary-correction-toggle"
            />
          </label>
          <p className="-mt-2 text-xs text-muted-foreground">
            {isCorrection
              ? t("dictionary.correction_hint_on")
              : t("dictionary.correction_hint_off")}
          </p>

          {isCorrection ? (
            <div className="flex items-center gap-2">
              <input
                value={misheardText}
                onChange={(e) => setMisheardText(e.target.value)}
                placeholder={t("dictionary.misheard_placeholder")}
                data-testid="dictionary-misheard-input"
                className={inputClass}
                autoFocus
              />
              <ArrowRight className="h-4 w-4 shrink-0 text-muted-foreground" />
              <input
                value={word}
                onChange={(e) => setWord(e.target.value)}
                placeholder={t("dictionary.word_correct_placeholder")}
                data-testid="dictionary-word-input"
                className={inputClass}
                onKeyDown={(e) => e.key === "Enter" && void submit()}
              />
            </div>
          ) : (
            <input
              value={word}
              onChange={(e) => setWord(e.target.value)}
              placeholder={t("dictionary.word_placeholder")}
              data-testid="dictionary-word-input"
              className={inputClass}
              autoFocus
              onKeyDown={(e) => e.key === "Enter" && void submit()}
            />
          )}
          {isCorrection && (
            <p className="-mt-2 text-[11px] text-muted-foreground">
              {t("dictionary.misheard_comma_hint")}
            </p>
          )}

          {error && <p className="text-xs text-destructive">{error}</p>}
        </div>

        <div className="mt-6 flex justify-end gap-3">
          <Button size="sm" variant="outline" onClick={onClose} disabled={saving}>
            {t("dictionary.cancel")}
          </Button>
          <Button
            size="sm"
            onClick={() => void submit()}
            disabled={!valid || saving}
            data-testid="dictionary-save"
            className="gap-1.5"
          >
            {saving && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            {initial ? t("dictionary.save") : t("dictionary.add_confirm")}
          </Button>
        </div>
      </div>
    </div>
  );
}
