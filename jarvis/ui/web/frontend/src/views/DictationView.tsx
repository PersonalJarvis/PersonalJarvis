import { useState } from "react";
import {
  AlertTriangle,
  Keyboard,
  Loader2,
  Mic,
  Square,
  Trash2,
} from "lucide-react";
import { ViewHeader } from "@/views/ChatsView";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { useDictation, type DictationEntry } from "@/hooks/useDictation";
import { useEventStore } from "@/store/events";
import { useT } from "@/i18n";

/**
 * "Dictation" section — hold a key, speak, and the text lands in whatever text
 * field currently has focus.
 *
 * Two things this view deliberately makes visible rather than hiding:
 *
 * 1. **Whether insertion can work at all.** On Wayland, on a headless host, or
 *    while an elevated window is in front, the OS blocks one program from
 *    typing into another — and does so silently. The banner says it up front so
 *    "nothing happened" is never a mystery.
 * 2. **What the filler cleanup changed.** Every entry keeps the raw transcript
 *    next to the inserted one, so a wrong rule is findable instead of merely
 *    suspected.
 *
 * Backed by /api/dictation (status/start/stop/settings/history) via useDictation.
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
    entries,
    loading,
    error,
    start,
    stop,
    saveSettings,
    deleteEntry,
    clearHistory,
  } = useDictation();
  const pushToast = useEventStore((s) => s.pushToast);
  const [busy, setBusy] = useState(false);

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

  const blocked = status?.insertion && !status.insertion.can_insert;

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

          {/* --- Settings. --- */}
          {settings && choices && (
            <div className="rounded-lg border border-border bg-card/60 p-4">
              <h4 className="font-display text-sm font-semibold">
                {t("dictation.settings_title")}
              </h4>
              <div className="mt-3 grid gap-3 sm:grid-cols-2">
                <SelectRow
                  label={t("dictation.mode_label")}
                  hint={t("dictation.mode_hint")}
                  value={settings.mode}
                  options={choices.mode}
                  testId="dictation-mode"
                  onChange={(v) => void onSave({ mode: v })}
                />
                <SelectRow
                  label={t("dictation.paste_chord_label")}
                  hint={t("dictation.paste_chord_hint")}
                  value={settings.paste_chord}
                  options={choices.paste_chord}
                  testId="dictation-paste-chord"
                  onChange={(v) => void onSave({ paste_chord: v })}
                />
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
            {entries.length === 0 ? (
              <p className="mt-3 text-xs text-muted-foreground">
                {t("dictation.history_empty")}
              </p>
            ) : (
              <ul
                className="mt-3 divide-y divide-border/60"
                data-testid="dictation-history"
              >
                {entries.map((entry) => (
                  <HistoryRow
                    key={entry.id}
                    entry={entry}
                    onDelete={() => {
                      void deleteEntry(entry.id).catch((e) =>
                        pushToast("error", (e as Error).message),
                      );
                    }}
                  />
                ))}
              </ul>
            )}
          </div>
        </div>
      </div>
    </div>
  );
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

function HistoryRow({
  entry,
  onDelete,
}: {
  entry: DictationEntry;
  onDelete: () => void;
}) {
  const t = useT();
  const cleaned = entry.text !== entry.raw_text;
  return (
    <li className="group flex items-start gap-2 py-2.5">
      <div className="min-w-0 flex-1">
        <p className="break-words text-sm">{entry.text || entry.raw_text}</p>
        {cleaned && (
          <p className="mt-0.5 break-words text-[11px] text-muted-foreground">
            {t("dictation.raw_prefix")} {entry.raw_text}
          </p>
        )}
        <div className="mt-1 flex flex-wrap items-center gap-2 text-[10px] text-muted-foreground">
          <span>{new Date(entry.created_at).toLocaleString()}</span>
          {entry.outcome && (
            <span className="rounded-full border border-border bg-muted/60 px-1.5 py-0.5">
              {entry.outcome}
            </span>
          )}
          {entry.removed_words > 0 && (
            <span>
              {t("dictation.removed_words").replace(
                "{0}",
                String(entry.removed_words),
              )}
            </span>
          )}
        </div>
      </div>
      <button
        type="button"
        onClick={onDelete}
        aria-label={t("dictation.delete_entry")}
        className="rounded p-1 text-muted-foreground opacity-0 transition group-hover:opacity-100 hover:text-destructive"
      >
        <Trash2 className="h-3.5 w-3.5" />
      </button>
    </li>
  );
}
