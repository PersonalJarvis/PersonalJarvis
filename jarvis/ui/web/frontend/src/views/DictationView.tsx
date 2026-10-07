import { useMemo, useState } from "react";
import { AlertTriangle, Mic, Search, Trash2 } from "lucide-react";
import { ViewHeader } from "@/views/ChatsView";
import { Button } from "@/components/ui/button";
import { SkeletonBar } from "@/components/layout/PanelSkeleton";
import { useDictation, type DictationEntry } from "@/hooks/useDictation";
import { useUserName } from "@/hooks/useUserName";
import { DictationHero } from "@/views/voice/DictationHero";
import { DictationStatsCard } from "@/views/voice/DictationStatsCard";
import {
  DictationEngineCard,
  DictationVocabularyCard,
} from "@/views/voice/DictationSideCards";
import { DictationHistoryGroup } from "@/views/voice/DictationHistoryGroup";
import { useEventStore } from "@/store/events";
import { fill, useT } from "@/i18n";

/**
 * "Dictation" — the first tab of the voice section: hold a key, speak, and the
 * text lands in whatever field has focus, in any app.
 *
 * The screen is built like a home page for the feature rather than a settings
 * panel:
 *
 *   Welcome back, Ruben
 *   ┌───────────────────────────── hero ─┐ ┌ stats ──────┐
 *   │ Hold [Ctrl + Alt] to dictate   📷  │ │ 241.2K words│
 *   │ [Start dictating] [Change shortcut]│ │ 149 wpm     │
 *   └────────────────────────────────────┘ │ ▁▃▅▂▇ 14 d  │
 *   TODAY                          [🔍 ] │ ├─────────────┤
 *   ┌ 4:12 PM  transcript …   ⧉ ↺ 🗑 ──┐ │ Recognition │
 *   └────────────────────────────────────┘ │ Dictionary →│
 *
 * The one instruction a newcomer needs is the largest thing on the screen,
 * the numbers sit beside it instead of above the history, and the history is
 * a journal of days with a time column — something you read back, not a log.
 * Below `xl` the side column folds in between the hero and the history.
 *
 * Four things this view deliberately keeps visible:
 *
 * 1. **Whether insertion can work at all.** On Wayland, on a headless host, or
 *    while an elevated window is in front, the OS blocks one program from
 *    typing into another — silently. The notice says so up front.
 * 2. **What the cleanup changed.** Every row keeps the raw transcript one click
 *    away, so a wrong rule is findable instead of merely suspected.
 * 3. **What became of each dictation.** The outcome is translated from a fixed
 *    vocabulary; only faults and partial deliveries get a coloured badge.
 * 4. **That deleting is recoverable.** The trash icon discards; the entry stays
 *    listed, restorable, until the explicit "Delete permanently" step.
 *
 * Deliberately NOT here: the "How dictation behaves" settings block. Every one
 * of its controls shipped a sensible default, and a wall of switches in front
 * of the feature made a thing that "just works" look like something to
 * configure first. The `[dictation]` config keys and `PUT
 * /api/dictation/settings` (hence `jarvis api dictation ...`) are untouched.
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
  const userName = useUserName();
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
  const setActiveSection = useEventStore((s) => s.setActiveSection);
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
      (e) => e.text.toLowerCase().includes(q) || e.raw_text.toLowerCase().includes(q),
    );
  }, [entries, query]);

  const groups = useMemo(() => groupByDay(filtered), [filtered]);

  const greeting = fill(t("dictation.welcome"), {
    name_part: userName ? `, ${userName}` : "",
  });

  return (
    <div className="flex h-full flex-col">
      {!hideHeader && (
        <ViewHeader
          icon={<Mic className="h-4 w-4 text-foreground" />}
          title={t("dictation.title")}
          subtitle={t("dictation.description")}
        />
      )}
      <div className="flex-1 overflow-y-auto scrollbar-jarvis">
        <div className="mx-auto w-full max-w-[1120px] px-6 pb-12 pt-8">
          <h1
            className="text-xl font-semibold text-foreground-strong"
            data-testid="dictation-welcome"
          >
            {greeting}
          </h1>

          <div className="mt-5 grid grid-cols-1 gap-6 [grid-template-areas:'hero'_'side'_'list'] xl:grid-cols-[minmax(0,1fr)_300px] xl:grid-rows-[auto_1fr] xl:[grid-template-areas:'hero_side'_'list_side']">
            <div className="flex min-w-0 flex-col gap-4 [grid-area:hero]">
              {error && <p className="text-meta text-destructive">{error}</p>}

              {/* The silent-failure paths, made loud. Degraded, not broken —
                  the words still reach the clipboard — so a --warning glyph
                  on an ordinary card, never a washed panel. */}
              {blocked && (
                <div
                  className="flex items-start gap-3 rounded-xl border border-border bg-card p-5 shadow-rim"
                  data-testid="dictation-insert-warning"
                >
                  <AlertTriangle
                    aria-hidden="true"
                    className="mt-0.5 h-4 w-4 shrink-0 text-warning"
                  />
                  <div className="min-w-0">
                    <h4 className="text-title font-semibold text-foreground-strong">
                      {t("dictation.cannot_insert_title")}
                    </h4>
                    <p className="mt-1 text-meta text-muted-foreground">
                      {status?.insertion.detail || t("dictation.cannot_insert_generic")}
                    </p>
                  </div>
                </div>
              )}

              <DictationHero
                status={status}
                loading={loading}
                busy={busy}
                onToggle={() => void onToggle()}
                onOpenShortcuts={() => setActiveSection("voice-shortcuts")}
              />
            </div>

            {/* The side column. While loading it stands at its real size as
                empty bars — a card with zeros in it would read as a person
                who has never dictated anything. */}
            <aside className="flex min-w-0 flex-col gap-4 [grid-area:side] xl:sticky xl:top-0 xl:self-start">
              {loading ? (
                <div className="rounded-xl border border-border bg-card p-5 shadow-rim">
                  <SkeletonBar className="h-3 w-20" />
                  <div className="mt-4 flex flex-col gap-3">
                    <SkeletonBar className="h-8 w-40" />
                    <SkeletonBar className="h-8 w-32" />
                    <SkeletonBar className="h-8 w-36" />
                  </div>
                  <SkeletonBar className="mt-5 h-14 w-full" />
                </div>
              ) : (
                stats && <DictationStatsCard stats={stats} />
              )}
              {!loading && status?.available && status.engine?.provider && (
                <DictationEngineCard engine={status.engine} />
              )}
              <DictationVocabularyCard onOpen={() => setActiveSection("dictionary")} />
            </aside>

            <div className="min-w-0 [grid-area:list]">
              {loading ? (
                <div className="flex flex-col gap-2" aria-busy="true">
                  <SkeletonBar className="h-3 w-24" />
                  <SkeletonBar className="h-[220px] w-full rounded-xl" />
                </div>
              ) : entries.length === 0 ? (
                <EmptyHistory />
              ) : (
                <>
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <h2 className="text-base font-semibold text-foreground-strong">
                      {t("dictation.history_title")}
                    </h2>
                    <div className="flex items-center gap-1">
                      <div className="relative">
                        <Search
                          aria-hidden="true"
                          className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground"
                        />
                        <input
                          value={query}
                          onChange={(e) => setQuery(e.target.value)}
                          placeholder={t("dictation.search_placeholder")}
                          aria-label={t("dictation.search_placeholder")}
                          data-testid="dictation-search"
                          className="h-8 w-56 rounded-md bg-input pl-8 pr-3 text-sm text-foreground placeholder:text-faint-foreground focus:outline-none focus:ring-2 focus:ring-border-strong"
                        />
                      </div>
                      <Button
                        size="sm"
                        variant="ghost"
                        className="gap-2 text-muted-foreground"
                        title={t("dictation.clear_history_hint")}
                        data-testid="dictation-clear-history"
                        onClick={() => {
                          void clearHistory().catch((e) =>
                            pushToast("error", (e as Error).message),
                          );
                        }}
                      >
                        <Trash2 aria-hidden="true" className="h-3.5 w-3.5" />
                        {t("dictation.clear_history")}
                      </Button>
                    </div>
                  </div>

                  {filtered.length === 0 ? (
                    /* Shared "nothing matched your search" string — the
                       Dictionary tab owns it and it is localized everywhere. */
                    <div
                      className="mt-4 rounded-xl border border-dashed border-border px-block py-12 text-center text-body text-muted-foreground"
                      data-testid="dictation-no-matches"
                    >
                      {t("dictionary.no_matches")}
                    </div>
                  ) : (
                    <div className="mt-4 flex flex-col gap-6" data-testid="dictation-history">
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
                </>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

/** The designed first-run surface: what will appear here, and how. */
function EmptyHistory() {
  const t = useT();
  return (
    <div
      className="flex flex-col items-center rounded-xl border border-dashed border-border px-6 py-12 text-center"
      data-testid="dictation-empty"
    >
      <span className="flex h-10 w-10 items-center justify-center rounded-full bg-secondary text-foreground">
        <Mic aria-hidden="true" className="h-4 w-4" />
      </span>
      <h3 className="mt-3 text-base font-semibold text-foreground-strong">
        {t("dictation.empty_title")}
      </h3>
      <p className="mt-1 max-w-sm text-sm text-muted-foreground">{t("dictation.empty_body")}</p>
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

/** "Today" / "Yesterday" / a long locale date ("August 23, 2026") otherwise. */
function dayLabel(t: (key: string) => string, key: string): string {
  const now = new Date();
  if (key === dateKey(now)) return t("dictation.group.today");
  const yesterday = new Date(now);
  yesterday.setDate(yesterday.getDate() - 1);
  if (key === dateKey(yesterday)) return t("dictation.group.yesterday");
  const parsed = new Date(`${key}T00:00:00`);
  return Number.isNaN(parsed.getTime())
    ? key
    : parsed.toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" });
}
