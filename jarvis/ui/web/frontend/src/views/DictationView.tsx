import { useMemo, useState } from "react";
import { AlertTriangle, Mic, Search, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { PageHeader } from "@/components/layout/PageHeader";
import { SkeletonBar } from "@/components/layout/PanelSkeleton";
import { useDictation, type DictationEntry } from "@/hooks/useDictation";
import { useDictionary } from "@/hooks/useDictionary";
import { useUserName } from "@/hooks/useUserName";
import { useEventStore } from "@/store/events";
import { fill, useT } from "@/i18n";
import { DictationClearDialog } from "@/views/voice/DictationClearDialog";
import { DictationControlPanel } from "@/views/voice/DictationControlPanel";
import { DictationHistoryGroup } from "@/views/voice/DictationHistoryGroup";
import { DictationSetupGroup } from "@/views/voice/DictationSetupGroup";
import { DictationStatsSection, DictationStatsSkeleton } from "@/views/voice/DictationStats";
import { VoiceGroup, VoiceNote, VoicePage, VoiceSection } from "@/views/voice/voiceUi";

/**
 * "Dictation" — the first tab of the voice section: hold a key, speak, and the
 * text lands in whatever field has focus, in any app.
 *
 * One column, read top to bottom:
 *
 *   ┌ Welcome back, Ada ─────────────── ● Ready ┐
 *   │ Hold [Ctrl] + [AltGr] + [J] to dictate    │   the control panel
 *   │ [Start dictating]  [Change shortcut]      │
 *   └───────────────────────────────────────────┘
 *   Words │ wpm │ Day streak │ Today   ▁▃▅▂▇      the numbers
 *   Recognition · Teach it your words →          what answers, and the dictionary
 *   Recent dictations           [search] [Delete all]
 *   Today
 *   4:12 PM  transcript …                ⧉ ↺ 🗑
 *
 * Four things this view deliberately keeps visible:
 *
 * 1. **Whether insertion can work at all.** On Wayland, on a headless host, or
 *    while an elevated window is in front, the OS blocks one program from
 *    typing into another — silently. The notice says so above everything.
 * 2. **What the cleanup changed.** Every row keeps the raw transcript one click
 *    away, so a wrong rule is findable instead of merely suspected.
 * 3. **What became of each dictation.** The outcome is translated from a fixed
 *    vocabulary; only faults and partial deliveries get a coloured tag.
 * 4. **That deleting is recoverable.** The trash icon discards; the entry stays
 *    listed, restorable, until the explicit "Delete permanently" step. Only
 *    "Delete all" removes everything at once, behind a confirmation.
 *
 * Deliberately NOT here: the "How dictation behaves" settings block. Every one
 * of its controls shipped a sensible default, and a wall of switches in front
 * of the feature made a thing that "just works" look like something to
 * configure first. The `[dictation]` config keys and `PUT
 * /api/dictation/settings` (hence `jarvis api dictation ...`) are untouched.
 *
 * Backed by /api/dictation (status/start/stop/history/stats) via useDictation;
 * the dictionary row's count comes from /api/dictionary.
 */
export interface DictationViewProps {
  /**
   * Suppress this view's own header.
   *
   * Set by the merged voice section, which renders one "{name} Voice" header
   * above the tab bar — a second header right below it reads as a rendering
   * fault. Standalone rendering keeps its own header.
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
  const dictionary = useDictionary();
  const pushToast = useEventStore((s) => s.pushToast);
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const [busy, setBusy] = useState(false);
  const [query, setQuery] = useState("");
  const [busyIds, setBusyIds] = useState<ReadonlySet<string>>(new Set());
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [confirmClear, setConfirmClear] = useState(false);
  const [clearing, setClearing] = useState(false);

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

  async function onConfirmClear() {
    setClearing(true);
    try {
      await clearHistory();
      setQuery("");
      setConfirmClear(false);
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setClearing(false);
    }
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
    <VoicePage testId="dictation-page">
      {!hideHeader && (
        <PageHeader
          className="py-0"
          icon={<Mic />}
          title={t("dictation.title")}
          description={t("dictation.description")}
        />
      )}

      <div className="flex flex-col gap-4">
        {error && (
          <VoiceNote tone="error" icon={<AlertTriangle />}>
            {error}
          </VoiceNote>
        )}

        {/* The silent-failure path, made loud. Degraded, not broken — the
            words still reach the clipboard — so a warning glyph on the quiet
            note surface, never a washed panel. */}
        {blocked && (
          <VoiceNote tone="warning" icon={<AlertTriangle />} testId="dictation-insert-warning">
            <p className="font-medium text-foreground-strong">
              {t("dictation.cannot_insert_title")}
            </p>
            <p className="mt-0.5 text-muted-foreground">
              {status?.insertion.detail || t("dictation.cannot_insert_generic")}
            </p>
          </VoiceNote>
        )}

        <DictationControlPanel
          status={status}
          loading={loading}
          busy={busy}
          greeting={greeting}
          onToggle={() => void onToggle()}
          onOpenShortcuts={() => setActiveSection("voice-shortcuts")}
        />
      </div>

      {loading ? <DictationStatsSkeleton /> : stats && <DictationStatsSection stats={stats} />}

      <DictationSetupGroup
        engine={!loading && status?.available && status.engine?.provider ? status.engine : null}
        dictionaryCount={
          dictionary.loading || dictionary.error ? null : dictionary.entries.length
        }
        onOpenDictionary={() => setActiveSection("dictionary")}
      />

      <VoiceSection
        title={t("dictation.history_title")}
        testId="dictation-history-section"
        actions={
          !loading &&
          entries.length > 0 && (
            <>
              <div className="relative w-48 sm:w-56">
                <Search
                  aria-hidden="true"
                  className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
                />
                <Input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder={t("dictation.search_placeholder")}
                  aria-label={t("dictation.search_placeholder")}
                  data-testid="dictation-search"
                  className="h-8 pl-8 text-sm"
                />
              </div>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setConfirmClear(true)}
                data-testid="dictation-clear-history"
              >
                <Trash2 aria-hidden="true" />
                {t("dictation.clear_history")}
              </Button>
            </>
          )
        }
      >
        {loading ? (
          <div className="flex flex-col gap-2" aria-busy="true">
            <SkeletonBar className="h-3 w-16" />
            <SkeletonBar className="h-48 w-full rounded-xl" />
          </div>
        ) : entries.length === 0 ? (
          <VoiceGroup testId="dictation-empty">
            <EmptyState
              icon={<Mic />}
              title={t("dictation.empty_title")}
              description={t("dictation.empty_body")}
            />
          </VoiceGroup>
        ) : filtered.length === 0 ? (
          /* Shared "nothing matched your search" string — the Dictionary tab
             owns it and it is localized everywhere. */
          <VoiceGroup>
            <p
              className="px-5 py-10 text-center text-sm text-muted-foreground"
              data-testid="dictation-no-matches"
            >
              {t("dictionary.no_matches")}
            </p>
          </VoiceGroup>
        ) : (
          <div className="flex flex-col gap-6" data-testid="dictation-history">
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
      </VoiceSection>

      <DictationClearDialog
        open={confirmClear}
        busy={clearing}
        onCancel={() => setConfirmClear(false)}
        onConfirm={() => void onConfirmClear()}
      />
    </VoicePage>
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
