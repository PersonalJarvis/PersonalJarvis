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
import { Card } from "@/components/ui/card";
import { EmptyState } from "@/components/layout/EmptyState";
import { PanelSkeleton, SkeletonBar } from "@/components/layout/PanelSkeleton";
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
 *    typing into another — and does so silently. The notice says it up front so
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
 * The screen is three groups — state, numbers, history — separated by 32px and
 * bounded at the reading measure, because everything on it is either a card or
 * a transcript and neither earns the full width of a desktop window.
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

  // Life / neutral / fault, and nothing in between. A recording session is the
  // one thing on this screen that is actually running, so it is the one thing
  // wearing --success; "ready" is a neutral idle, and a machine that cannot
  // dictate at all is a fault rather than a dimmer shade of ready.
  const dotFill = status?.active
    ? "bg-success motion-safe:animate-pulse"
    : status?.available
      ? "bg-muted-foreground"
      : "bg-destructive";

  return (
    <div className="flex h-full flex-col">
      {!hideHeader && (
        <ViewHeader
          icon={<Mic className="h-4 w-4 text-foreground" />}
          title={t("dictation.title")}
          subtitle={t("dictation.description")}
        />
      )}
      <div className="flex-1 overflow-y-auto scrollbar-jarvis p-6">
        <div className="mx-auto flex max-w-reading flex-col gap-group">
          {error && <p className="text-meta text-destructive">{error}</p>}

          {/* --- Insertion notice: the silent-failure paths, made loud. ---
              Degraded, not broken — the words still reach the clipboard — so
              it is a --warning glyph on an ordinary card, never a washed
              panel that claims the whole screen has failed. */}
          {blocked && (
            <Card
              className="flex items-start gap-3 p-5"
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
            </Card>
          )}

          {/* --- State, and the one primary action on the screen. --- */}
          <Card className="p-5">
            {loading ? (
              // The real card at its real height with bars where the state
              // line and the button will be — never a centred grey word, which
              // is indistinguishable from a section that failed.
              <div
                role="status"
                aria-busy="true"
                aria-label={t("dictation.loading")}
                className="flex items-center justify-between gap-4"
              >
                <div className="flex min-w-0 flex-1 flex-col gap-2">
                  <SkeletonBar className="h-4 w-32" />
                  <SkeletonBar className="h-3 w-3/5" />
                </div>
                <SkeletonBar className="h-9 w-32 shrink-0" />
              </div>
            ) : (
              <div className="flex flex-wrap items-center justify-between gap-4">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span
                      className={`h-2 w-2 shrink-0 rounded-full ${dotFill}`}
                    />
                    <span className="text-title font-semibold text-foreground-strong">
                      {status?.active
                        ? t("dictation.state_active")
                        : status?.available
                          ? t("dictation.state_ready")
                          : t("dictation.state_unavailable")}
                    </span>
                  </div>
                  <p className="mt-1 text-meta text-muted-foreground">
                    {status?.available
                      ? status.hotkey
                        ? t("dictation.shortcut_set").replace("{0}", status.hotkey)
                        : t("dictation.shortcut_unset")
                      : status?.reason || t("dictation.state_unavailable")}
                  </p>
                  {/* Which recognizer really answers the next press (P-41):
                      the settings name one, the lane may hold another. */}
                  {status?.available && status.engine?.provider && (
                    <p className="mt-1 text-meta text-muted-foreground">
                      {status.engine.local
                        ? t("dictation.engine_local")
                            .replace("{0}", status.engine.model || status.engine.provider)
                            .replace("{1}", status.engine.fallback || "—")
                        : t("dictation.engine_cloud").replace("{0}", status.engine.provider)}
                      {!status.engine.local && status.engine.detail
                        ? ` ${t("dictation.engine_local_off").replace("{0}", status.engine.detail)}`
                        : ""}
                    </p>
                  )}
                </div>
                <Button
                  className="gap-2"
                  disabled={busy || !status?.available}
                  data-testid="dictation-toggle"
                  onClick={() => void onToggle()}
                >
                  {busy ? (
                    <Loader2
                      aria-hidden="true"
                      className="h-4 w-4 animate-spin motion-reduce:animate-none"
                    />
                  ) : status?.active ? (
                    <Square aria-hidden="true" className="h-4 w-4" />
                  ) : (
                    <Mic aria-hidden="true" className="h-4 w-4" />
                  )}
                  {status?.active ? t("dictation.stop") : t("dictation.start")}
                </Button>
              </div>
            )}
            {!status?.hotkey && status?.available && (
              <p className="mt-block flex items-center gap-2 text-meta text-muted-foreground">
                <Keyboard aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />
                {t("dictation.assign_hint")}
              </p>
            )}
          </Card>

          {/* --- How much you dictate, honestly windowed. ---
              While the request is in flight the three tiles stand at their
              real size as empty bars. Rendering the card with zeros in it
              would not read as loading; it would read as a person who has
              never dictated anything. */}
          {loading ? (
            <Card className="p-5">
              <SkeletonBar className="h-3 w-24" />
              <div className="mt-stack grid gap-stack sm:grid-cols-3">
                <SkeletonBar className="h-[84px]" />
                <SkeletonBar className="h-[84px]" />
                <SkeletonBar className="h-[84px]" />
              </div>
            </Card>
          ) : (
            stats && <DictationStatsBar stats={stats} />
          )}

          {/* --- History. ---
              With nothing in it there is no list to frame, so the card, its
              title, its hint and its search field all go and the designed
              empty surface stands alone. */}
          {loading ? (
            <Card className="p-5">
              <SkeletonBar className="h-4 w-40" />
              <SkeletonBar className="mt-block h-9 w-full" />
              <PanelSkeleton
                className="mt-block"
                rows={4}
                rowHeight={56}
                label={t("dictation.loading")}
              />
            </Card>
          ) : entries.length === 0 ? (
            <EmptyState icon={<Mic />} body={t("dictation.history_empty")} />
          ) : (
            <Card className="p-5">
              <div className="flex items-center justify-between gap-3">
                <h4 className="text-title font-semibold text-foreground-strong">
                  {t("dictation.history_title")}
                </h4>
                <Button
                  size="sm"
                  variant="ghost"
                  className="gap-2 text-muted-foreground"
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
              <p className="mt-1 text-meta text-muted-foreground">
                {t("dictation.clear_history_hint")}
              </p>
              {/* A field is a lift surface, not a hairline outline: it now
                  answers with a fill the way every other input in the app
                  does, and the focus ring is a rim rather than a third fill. */}
              <div className="relative mt-block">
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
                  className="h-9 w-full rounded-md bg-input pl-9 pr-3 text-body text-foreground placeholder:text-faint-foreground focus:outline-none focus:ring-2 focus:ring-border-strong"
                />
              </div>
              {filtered.length === 0 ? (
                /* Shared "nothing matched your search" string — the Dictionary
                   tab owns it and it is already localized everywhere. A lift
                   panel at the height the list would have had, so clearing the
                   query does not make the card jump. */
                <div
                  className="mt-block rounded-md bg-secondary px-block py-10 text-center text-body text-muted-foreground"
                  data-testid="dictation-no-matches"
                >
                  {t("dictionary.no_matches")}
                </div>
              ) : (
                <div className="mt-block" data-testid="dictation-history">
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
            </Card>
          )}
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
