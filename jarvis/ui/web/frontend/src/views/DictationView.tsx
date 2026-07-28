import { useMemo, useState } from "react";
import {
  AlertTriangle,
  Keyboard,
  Loader2,
  Mic,
  Search,
  Square,
  Trash2,
} from "lucide-react";
import { ViewHeader } from "@/views/ChatsView";
import { Button } from "@/components/ui/button";
import { useDictation, type DictationEntry } from "@/hooks/useDictation";
import { DictationStatsBar } from "@/views/voice/DictationStatsBar";
import { DictationHistoryGroup } from "@/views/voice/DictationHistoryGroup";
import { useEventStore } from "@/store/events";
import { useT } from "@/i18n";

/**
 * "Dictation" section — hold a key, speak, and the text lands in whatever text
 * field currently has focus.
 *
 * Four things this view deliberately makes visible rather than hiding:
 *
 * 1. **Whether insertion can work at all.** On Wayland, on a headless host, or
 *    while an elevated window is in front, the OS blocks one program from
 *    typing into another — and does so silently. The banner says it up front so
 *    "nothing happened" is never a mystery.
 * 2. **What the filler cleanup changed.** Every entry keeps the raw transcript
 *    next to the inserted one, so a wrong rule is findable instead of merely
 *    suspected.
 * 3. **What actually became of each dictation.** The outcome badge is
 *    translated from a fixed vocabulary — "Could not insert" is a different
 *    story from "Nothing heard", and the row says which one happened.
 * 4. **That deleting is recoverable.** The trash icon discards; the entry stays
 *    listed, restorable, until the second, explicit "Delete permanently" step.
 *
 * What is deliberately NOT here any more: the "How dictation behaves" settings
 * block — the paste shortcut, the delivery method, the insertion target and the
 * three cleanup/history switches. Every one of them shipped a sensible default,
 * and a wall of six controls in front of the feature made a thing that "just
 * works" look like something to be configured first. The `[dictation]` config
 * keys and their REST route (`PUT /api/dictation/settings`, hence
 * `jarvis api dictation ...`) are untouched, so an install that genuinely needs
 * a different paste shortcut can still set one — it is simply no longer the
 * first thing this screen shows. The "Key behaviour (hold/toggle)" dropdown
 * left earlier for the same reason: the two dictation shortcuts in the voice
 * section's Shortcuts tab are the source of truth for hold-vs-hands-free.
 *
 * Backed by /api/dictation (status/start/stop/history/stats) via useDictation.
 */
export interface DictationViewProps {
  /**
   * Suppress this view's own `ViewHeader`.
   *
   * Set by the merged voice section, which renders one "{name} Voice" header
   * above the tab bar — a second bordered band right below it reads as a
   * rendering fault. Standalone rendering keeps its own header.
   */
  hideHeader?: boolean;
}

export function DictationView({ hideHeader = false }: DictationViewProps = {}) {
  const t = useT();
  const {
    status,
    entries,
    stats,
    loading,
    error,
    start,
    stop,
    copyEntry,
    discardEntry,
    restoreEntry,
    deleteEntry,
    clearHistory,
  } = useDictation();
  const pushToast = useEventStore((s) => s.pushToast);
  const [busy, setBusy] = useState(false);
  const [query, setQuery] = useState("");
  const [busyIds, setBusyIds] = useState<ReadonlySet<string>>(new Set());
  const [copiedId, setCopiedId] = useState<string | null>(null);

  async function onToggle() {
    setBusy(true);
    try {
      if (status?.active) {
        await stop();
      } else {
        // "auto" — the backend decides at delivery time whether the text goes
        // into the app in front or into this app's own input box, so starting
        // here and then switching to the target application works.
        await start("auto");
      }
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  /** Marks one row busy for the duration of its request. */
  async function withRowBusy(id: string, run: () => Promise<void>) {
    setBusyIds((prev) => new Set(prev).add(id));
    try {
      await run();
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setBusyIds((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    }
  }

  async function onCopy(entry: DictationEntry) {
    const ok = await copyEntry(entry.id);
    if (!ok) return;
    setCopiedId(entry.id);
    pushToast("success", t("dictation.copied"));
    window.setTimeout(
      () => setCopiedId((current) => (current === entry.id ? null : current)),
      1500,
    );
  }

  async function onRestore(entry: DictationEntry) {
    await withRowBusy(entry.id, async () => {
      const result = await restoreEntry(entry.id);
      // A restore that could not re-transcribe still un-discards the entry —
      // say which of the two happened instead of claiming the better one.
      if (result.retranscribed || !result.detail) {
        pushToast("success", t("dictation.restored"));
      } else {
        pushToast("warning", result.detail);
      }
    });
  }

  const blocked = status?.insertion && !status.insertion.can_insert;

  // Case-insensitive substring over both the delivered and the raw transcript:
  // a word the cleanup removed is exactly the kind of thing you search for.
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return entries;
    return entries.filter(
      (e) =>
        e.text.toLowerCase().includes(q) || e.raw_text.toLowerCase().includes(q),
    );
  }, [entries, query]);

  const groups = useMemo(() => groupByDay(filtered), [filtered]);

  return (
    <div className="flex h-full flex-col">
      {!hideHeader && (
        <ViewHeader
          icon={<Mic className="h-4 w-4 text-primary" />}
          title={t("dictation.title")}
          subtitle={t("dictation.description")}
        />
      )}
      <div className="flex-1 overflow-y-auto scrollbar-jarvis p-6">
        <div className="mx-auto flex max-w-3xl flex-col gap-4">
          {error && <p className="text-xs text-destructive">{error}</p>}

          {/* --- Insertion warning: the silent-failure paths, made loud. --- */}
          {blocked && (
            <div
              className="flex items-start gap-3 rounded-lg border border-amber-500/40 bg-amber-500/10 p-4"
              data-testid="dictation-insert-warning"
            >
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-500" />
              <div className="min-w-0">
                <h4 className="font-display text-sm font-semibold">
                  {t("dictation.cannot_insert_title")}
                </h4>
                <p className="mt-1 text-xs text-muted-foreground">
                  {status?.insertion.detail || t("dictation.cannot_insert_generic")}
                </p>
              </div>
            </div>
          )}

          {/* --- Status + the big button. --- */}
          <div className="rounded-lg border border-border bg-card/60 p-4">
            {loading ? (
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                {t("dictation.loading")}
              </div>
            ) : (
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span
                      className={`h-2 w-2 shrink-0 rounded-full ${
                        status?.active
                          ? "animate-pulse bg-primary"
                          : status?.available
                            ? "bg-emerald-500"
                            : "bg-muted-foreground/50"
                      }`}
                    />
                    <span className="text-sm font-medium">
                      {status?.active
                        ? t("dictation.state_active")
                        : status?.available
                          ? t("dictation.state_ready")
                          : t("dictation.state_unavailable")}
                    </span>
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {status?.available
                      ? status.hotkey
                        ? t("dictation.shortcut_set").replace("{0}", status.hotkey)
                        : t("dictation.shortcut_unset")
                      : status?.reason || t("dictation.state_unavailable")}
                  </p>
                </div>
                <Button
                  size="sm"
                  className="gap-1.5"
                  disabled={busy || !status?.available}
                  data-testid="dictation-toggle"
                  onClick={() => void onToggle()}
                >
                  {busy ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : status?.active ? (
                    <Square className="h-3.5 w-3.5" />
                  ) : (
                    <Mic className="h-3.5 w-3.5" />
                  )}
                  {status?.active ? t("dictation.stop") : t("dictation.start")}
                </Button>
              </div>
            )}
            {!status?.hotkey && status?.available && (
              <p className="mt-3 flex items-center gap-1.5 text-xs text-muted-foreground">
                <Keyboard className="h-3.5 w-3.5" />
                {t("dictation.assign_hint")}
              </p>
            )}
          </div>

          {/* --- How much you dictate, honestly windowed. --- */}
          {stats && <DictationStatsBar stats={stats} />}

          {/* --- History. --- */}
          <div className="rounded-lg border border-border bg-card/60 p-4">
            <div className="flex items-center justify-between gap-3">
              <h4 className="font-display text-sm font-semibold">
                {t("dictation.history_title")}
              </h4>
              {entries.length > 0 && (
                <Button
                  size="sm"
                  variant="ghost"
                  className="gap-1.5 text-xs"
                  data-testid="dictation-clear-history"
                  onClick={() => {
                    void clearHistory().catch((e) =>
                      pushToast("error", (e as Error).message),
                    );
                  }}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                  {t("dictation.clear_history")}
                </Button>
              )}
            </div>
            {entries.length > 0 && (
              <>
                <p className="mt-1 text-[11px] text-muted-foreground">
                  {t("dictation.clear_history_hint")}
                </p>
                <div className="relative mt-3">
                  <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
                  <input
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder={t("dictation.search_placeholder")}
                    aria-label={t("dictation.search_placeholder")}
                    data-testid="dictation-search"
                    className="w-full rounded-md border border-input bg-background py-1.5 pl-8 pr-3 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
                  />
                </div>
              </>
            )}
            {entries.length === 0 ? (
              <p className="mt-3 text-xs text-muted-foreground">
                {t("dictation.history_empty")}
              </p>
            ) : filtered.length === 0 ? (
              /* Shared "nothing matched your search" string — the Dictionary
                 tab owns it and it is already localized everywhere. */
              <p
                className="mt-3 text-xs text-muted-foreground"
                data-testid="dictation-no-matches"
              >
                {t("dictionary.no_matches")}
              </p>
            ) : (
              <div className="mt-3" data-testid="dictation-history">
                {groups.map((group) => (
                  <DictationHistoryGroup
                    key={group.key}
                    label={dayLabel(t, group.key)}
                    entries={group.entries}
                    busyIds={busyIds}
                    copiedId={copiedId}
                    onCopy={(entry) => void onCopy(entry)}
                    onRestore={(entry) => void onRestore(entry)}
                    onDiscard={(entry) => {
                      void withRowBusy(entry.id, () => discardEntry(entry.id));
                    }}
                    onDelete={(entry) => {
                      void withRowBusy(entry.id, () => deleteEntry(entry.id));
                    }}
                  />
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

interface DayGroup {
  /** Local calendar date, ISO `YYYY-MM-DD`. */
  key: string;
  entries: DictationEntry[];
}

/**
 * Groups entries into local calendar days, newest day first and newest entry
 * first inside each day.
 *
 * Local, not UTC: bucketing by UTC moves an evening dictation into "tomorrow"
 * for everyone east of Greenwich, which makes the day headers read wrong for
 * most of the world.
 */
function groupByDay(entries: DictationEntry[]): DayGroup[] {
  const byKey = new Map<string, DictationEntry[]>();
  for (const entry of [...entries].sort(
    (a, b) => Date.parse(b.created_at) - Date.parse(a.created_at),
  )) {
    const key = localDateKey(entry.created_at);
    const bucket = byKey.get(key);
    if (bucket) bucket.push(entry);
    else byKey.set(key, [entry]);
  }
  return [...byKey.entries()]
    .map(([key, groupEntries]) => ({ key, entries: groupEntries }))
    .sort((a, b) => (a.key < b.key ? 1 : a.key > b.key ? -1 : 0));
}

/** `YYYY-MM-DD` in the viewer's own timezone. */
function dateKey(date: Date): string {
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${date.getFullYear()}-${month}-${day}`;
}

/** Same, from a wire timestamp; an unparsable stamp gets its own bucket. */
function localDateKey(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : dateKey(date);
}

/** "Today" / "Yesterday" / a locale-formatted date for everything older. */
function dayLabel(t: (key: string) => string, key: string): string {
  const now = new Date();
  if (key === dateKey(now)) return t("dictation.group.today");
  const yesterday = new Date(now);
  yesterday.setDate(yesterday.getDate() - 1);
  if (key === dateKey(yesterday)) return t("dictation.group.yesterday");
  const parsed = new Date(`${key}T00:00:00`);
  return Number.isNaN(parsed.getTime()) ? key : parsed.toLocaleDateString();
}
