import { useCallback, useEffect, useMemo, useState } from "react";
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
import { Switch } from "@/components/ui/switch";
import {
  useDictation,
  type DictationCustomField,
  type DictationEntry,
} from "@/hooks/useDictation";
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
 * 5. **That a recorded paste shortcut is a request, not a guarantee.** Jarvis
 *    does not paste — it asks the app in front to paste. An app that does not
 *    use that combination ignores it, and there is nothing to read back, so
 *    the caveat is stated where the shortcut is chosen.
 *
 * What is deliberately NOT here any more: the "Key behaviour (hold/toggle)"
 * dropdown. The two dictation shortcuts in the voice section's Shortcuts tab
 * are the source of truth for hold-vs-hands-free — a dropdown that could
 * contradict them was a second answer to one question. The `[dictation].mode`
 * config key still exists and is still honoured by the speech pipeline for the
 * push-to-talk key; the push-to-talk row pins it to "hold" when saved, and
 * flags it when an older install is still on "toggle".
 *
 * Backed by /api/dictation (status/start/stop/settings/history/stats) via
 * useDictation.
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
    settings,
    choices,
    custom,
    entries,
    stats,
    loading,
    error,
    start,
    stop,
    saveSettings,
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

  async function onSave(patch: Record<string, unknown>) {
    try {
      await saveSettings(patch);
      pushToast("success", t("dictation.saved"));
    } catch (e) {
      pushToast("error", (e as Error).message);
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

          {/* --- Settings. --- */}
          {settings && choices && (
            <div className="rounded-lg border border-border bg-card/60 p-4">
              <h4 className="font-display text-sm font-semibold">
                {t("dictation.settings_title")}
              </h4>
              {/* The paste shortcut owns a full row: with "Custom shortcut"
                  picked it grows a recorder and a caveat, and a control that
                  changes height inside a two-column grid drags its neighbour
                  around with it. */}
              <div className="mt-3">
                <PasteChordRow
                  value={settings.paste_chord}
                  options={choices.paste_chord}
                  custom={custom?.paste_chord}
                  onSave={(chord) => onSave({ paste_chord: chord })}
                />
              </div>
              <div className="mt-3 grid gap-3 sm:grid-cols-2">
                <SelectRow
                  label={t("dictation.insert_method_label")}
                  hint={t("dictation.insert_method_hint")}
                  value={settings.insert_method}
                  options={choices.insert_method}
                  testId="dictation-insert-method"
                  onChange={(v) => void onSave({ insert_method: v })}
                />
                <SelectRow
                  label={t("dictation.target_label")}
                  hint={t("dictation.target_hint")}
                  value={settings.target}
                  options={choices.target}
                  testId="dictation-target"
                  onChange={(v) => void onSave({ target: v })}
                />
              </div>
              <div className="mt-4 flex flex-col gap-3">
                <SwitchRow
                  label={t("dictation.remove_fillers_label")}
                  hint={t("dictation.remove_fillers_hint")}
                  checked={settings.remove_fillers}
                  testId="dictation-remove-fillers"
                  onChange={(v) => void onSave({ remove_fillers: v })}
                />
                <SwitchRow
                  label={t("dictation.restore_clipboard_label")}
                  hint={t("dictation.restore_clipboard_hint")}
                  checked={settings.restore_clipboard}
                  testId="dictation-restore-clipboard"
                  onChange={(v) => void onSave({ restore_clipboard: v })}
                />
                <SwitchRow
                  label={t("dictation.history_label")}
                  hint={t("dictation.history_hint")}
                  checked={settings.history_enabled}
                  testId="dictation-history-enabled"
                  onChange={(v) => void onSave({ history_enabled: v })}
                />
              </div>
            </div>
          )}

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

function SelectRow({
  label,
  hint,
  value,
  options,
  testId,
  onChange,
}: {
  label: string;
  hint: string;
  value: string;
  options: string[];
  testId: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-xs font-medium">{label}</span>
      <select
        value={value}
        data-testid={testId}
        onChange={(e) => onChange(e.target.value)}
        className="rounded-md border border-input bg-background px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
      >
        {options.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
      <span className="text-[11px] text-muted-foreground">{hint}</span>
    </label>
  );
}

/**
 * The value the paste-shortcut dropdown uses for "record your own".
 *
 * Deliberately not a value the backend accepts: it only ever exists inside this
 * component, and the combo the user records is what actually gets saved.
 */
const CUSTOM_CHORD_OPTION = "__custom__";

/**
 * Modifier tokens by physical `event.code`, in the spelling the backend's
 * custom-chord vocabulary uses.
 *
 * The Meta key is Command on a Mac and the Windows key on a PC, and the
 * backend keeps those apart on purpose so a Mac user is never shown "Win" for
 * the key they actually pressed. `null` for an unknown code means "keep
 * waiting", never "unsupported".
 */
function chordModifierToken(code: string, mac: boolean): string | null {
  if (code.startsWith("Control")) return "ctrl";
  if (code.startsWith("Shift")) return "shift";
  if (code.startsWith("Alt")) return "alt";
  if (code.startsWith("Meta")) return mac ? "cmd" : "win";
  return null;
}

/**
 * A physical `event.code` as the backend's custom-chord key token, or null when
 * this computer cannot send it.
 *
 * Only layout-independent keys are offered. `event.code` names a POSITION on a
 * US layout, so binding punctuation by position would mean a German keyboard
 * records a key whose cap says something else entirely.
 */
function chordKeyToken(code: string): string | null {
  if (/^Key[A-Z]$/.test(code)) return code.slice(3).toLowerCase();
  if (/^Digit[0-9]$/.test(code)) return code.slice(5);
  if (/^F([1-9]|1[0-2])$/.test(code)) return code.toLowerCase();
  const named: Record<string, string> = {
    Space: "space",
    Enter: "enter",
    NumpadEnter: "enter",
    Tab: "tab",
    Backspace: "backspace",
    Delete: "delete",
    Insert: "insert",
    Home: "home",
    End: "end",
    PageUp: "pageup",
    PageDown: "pagedown",
    ArrowLeft: "left",
    ArrowRight: "right",
    ArrowUp: "up",
    ArrowDown: "down",
  };
  return named[code] ?? null;
}

/** True on a Mac-family browser; decides Command vs the Windows key. */
function isMacBrowser(): boolean {
  if (typeof navigator === "undefined") return false;
  return /Mac|iPhone|iPad|iPod/i.test(navigator.userAgent ?? "");
}

/** Human keycaps for a recorded chord ("ctrl+shift+insert" → "Ctrl + Shift + Insert"). */
function formatChord(chord: string, mac: boolean): string {
  const labels: Record<string, string> = {
    ctrl: "Ctrl",
    alt: mac ? "⌥" : "Alt",
    shift: "Shift",
    win: "Win",
    cmd: "⌘",
    space: "Space",
    enter: "Enter",
    tab: "Tab",
    backspace: "Backspace",
    delete: "Delete",
    insert: "Insert",
    home: "Home",
    end: "End",
    pageup: "PageUp",
    pagedown: "PageDown",
    left: "←",
    right: "→",
    up: "↑",
    down: "↓",
  };
  return (chord ?? "")
    .split("+")
    .filter(Boolean)
    .map((p) => labels[p] ?? p.toUpperCase())
    .join(" + ");
}

/**
 * The paste shortcut: the curated presets, plus one recorded combination.
 *
 * Two things this control refuses to fake:
 *
 * 1. **A key this computer cannot send.** The accepted token vocabulary comes
 *    from the backend (`custom.paste_chord`), which derives it from the
 *    INTERSECTION of both actuator backends — so a combination recorded here
 *    cannot be one that works on Windows and raises on Linux. A key outside it
 *    is refused while recording, with the reason, instead of being saved and
 *    failing silently at paste time. A backend that serves no vocabulary at all
 *    (an older install) is trusted to judge on save rather than second-guessed.
 * 2. **The result.** Jarvis sends the combination to the window in front; it
 *    cannot observe what that window did with it. The caveat says so, and the
 *    history entry for such a dictation reads "Paste sent", not "Inserted".
 */
function PasteChordRow({
  value,
  options,
  custom,
  onSave,
}: {
  value: string;
  options: string[];
  custom?: DictationCustomField;
  onSave: (chord: string) => Promise<void> | void;
}) {
  const t = useT();
  const mac = useMemo(isMacBrowser, []);
  // A stored value the preset list does not contain is, by definition, a
  // recorded one — that is also how the row survives a backend that adds a
  // preset later.
  const storedIsCustom = Boolean(value) && !options.includes(value);
  const [customOpen, setCustomOpen] = useState(storedIsCustom);
  const [draft, setDraft] = useState(storedIsCustom ? value : "");
  const [recording, setRecording] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  // A save (or a reload) that changed the stored value re-seeds the row.
  useEffect(() => {
    setCustomOpen(storedIsCustom);
    setDraft(storedIsCustom ? value : "");
    setProblem(null);
  }, [value, storedIsCustom]);

  const accepts = useCallback(
    (token: string, kind: "modifier" | "key") => {
      const list = kind === "modifier" ? custom?.modifiers : custom?.keys;
      // No vocabulary served → do not invent one. The backend answers with a
      // plain-English 400 on save, which is a better authority than a guess.
      return !list || list.length === 0 || list.includes(token);
    },
    [custom],
  );

  useEffect(() => {
    if (!recording) return;
    function onKeyDown(e: KeyboardEvent) {
      // Capturing and swallowing: the recorded combination is very likely one
      // the app itself binds, and it must not also trigger it.
      e.preventDefault();
      e.stopPropagation();
      if (e.code === "Escape") {
        setRecording(false);
        return;
      }
      // A modifier alone is an interim state, not an answer — keep waiting.
      if (chordModifierToken(e.code, mac) !== null) return;

      const key = chordKeyToken(e.code);
      if (key === null || !accepts(key, "key")) {
        setProblem(t("dictation.paste_chord_unsupported"));
        return;
      }
      const mods: string[] = [];
      if (e.ctrlKey && accepts("ctrl", "modifier")) mods.push("ctrl");
      if (e.altKey && accepts("alt", "modifier")) mods.push("alt");
      if (e.shiftKey && accepts("shift", "modifier")) mods.push("shift");
      const meta = mac ? "cmd" : "win";
      if (e.metaKey && accepts(meta, "modifier")) mods.push(meta);

      setDraft([...mods, key].join(custom?.separator ?? "+"));
      setProblem(null);
      setRecording(false);
    }
    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, [recording, mac, accepts, custom, t]);

  const selectValue = customOpen ? CUSTOM_CHORD_OPTION : value;
  const draftHasKey = draft
    .split("+")
    .some((token) => token && !["ctrl", "alt", "shift", "win", "cmd"].includes(token));

  return (
    <div className="flex flex-col gap-1">
      <span className="text-xs font-medium">{t("dictation.paste_chord_label")}</span>
      <select
        value={selectValue}
        data-testid="dictation-paste-chord"
        onChange={(e) => {
          const next = e.target.value;
          if (next === CUSTOM_CHORD_OPTION) {
            // Opening the recorder saves nothing yet — the user still has to
            // record a combination and confirm it.
            setCustomOpen(true);
            setProblem(null);
            return;
          }
          setCustomOpen(false);
          void onSave(next);
        }}
        className="rounded-md border border-input bg-background px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
      >
        {options.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
        <option value={CUSTOM_CHORD_OPTION}>
          {t("dictation.paste_chord_custom_option")}
        </option>
      </select>
      <span className="text-[11px] text-muted-foreground">
        {t("dictation.paste_chord_hint")}
      </span>

      {customOpen && (
        <div
          className="mt-2 rounded-md border border-border bg-background/60 p-3"
          data-testid="dictation-paste-chord-custom"
        >
          <div className="flex flex-wrap items-center gap-2">
            <span
              className="rounded border border-border bg-muted/70 px-2 py-1 font-mono text-[11px] text-foreground"
              data-testid="dictation-paste-chord-combo"
            >
              {recording
                ? t("dictation.paste_chord_recording")
                : draft
                  ? formatChord(draft, mac)
                  : t("dictation.paste_chord_none")}
            </span>
            <Button
              size="sm"
              variant="outline"
              data-testid="dictation-paste-chord-record"
              onClick={() => {
                setProblem(null);
                setRecording((on) => !on);
              }}
            >
              {recording
                ? t("dictation.paste_chord_cancel")
                : t("dictation.paste_chord_record")}
            </Button>
            <Button
              size="sm"
              disabled={!draftHasKey || draft === value}
              data-testid="dictation-paste-chord-save"
              onClick={() => void onSave(draft)}
            >
              {t("dictation.paste_chord_save")}
            </Button>
          </div>
          {recording && (
            <p className="mt-2 text-[11px] text-muted-foreground">
              {t("dictation.paste_chord_recording_hint")}
            </p>
          )}
          {problem && (
            <p
              className="mt-2 text-[11px] text-amber-500"
              data-testid="dictation-paste-chord-problem"
            >
              {problem}
            </p>
          )}
          {!recording && draft && !draftHasKey && (
            <p className="mt-2 text-[11px] text-amber-500">
              {t("dictation.paste_chord_needs_key")}
            </p>
          )}
          {/* The caveat is never conditional. It is the whole reason a custom
              chord reports "Paste sent" instead of "Inserted", and hiding it
              behind a transient message would hide it exactly when someone is
              busy choosing one. */}
          <p
            className="mt-2 text-[11px] text-muted-foreground"
            data-testid="dictation-paste-chord-caveat"
          >
            {t("dictation.paste_chord_custom_caveat")}
          </p>
        </div>
      )}
    </div>
  );
}

function SwitchRow({
  label,
  hint,
  checked,
  testId,
  onChange,
}: {
  label: string;
  hint: string;
  checked: boolean;
  testId: string;
  onChange: (checked: boolean) => void;
}) {
  return (
    <div className="flex items-start justify-between gap-3">
      <div className="min-w-0">
        <p className="text-xs font-medium">{label}</p>
        <p className="text-[11px] text-muted-foreground">{hint}</p>
      </div>
      <Switch checked={checked} onCheckedChange={onChange} data-testid={testId} />
    </div>
  );
}
