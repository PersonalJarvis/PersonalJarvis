import { useEffect, useState } from "react";
import { FileText, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useSystemPrompt } from "@/hooks/useSystemPrompt";
import { useEventStore } from "@/store/events";
import { useT } from "@/i18n";

/**
 * "System Prompt" panel inside the Settings view. Shows the persona that
 * defines how the assistant thinks and speaks as editable Markdown, lets the
 * user replace it with their own, and reset back to the packaged default with
 * one click. The override applies on the assistant's next message — no restart.
 *
 * Backed by /api/settings/system-prompt (GET/PUT/DELETE) via useSystemPrompt.
 */
export function SystemPromptGroup() {
  const t = useT();
  const { config, loading, error, savePrompt, resetPrompt } = useSystemPrompt();
  const pushToast = useEventStore((s) => s.pushToast);

  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [resetting, setResetting] = useState(false);

  // Reflect the server value on load and after every save/reset. Coalesce a
  // missing `content` to "" so a partial/foreign response never makes `draft`
  // undefined (SettingsView shares one fetch mock across panels in tests, and a
  // real malformed response should degrade, not crash the whole view).
  useEffect(() => {
    if (config) setDraft(config.content ?? "");
  }, [config]);

  const dirty = !!config && draft !== (config.content ?? "");
  const isCustom = !!config?.is_custom;
  const canReset = isCustom || dirty;
  const trimmedEmpty = draft.trim().length === 0;

  async function onSave() {
    if (trimmedEmpty || !dirty) return;
    setSaving(true);
    try {
      await savePrompt(draft);
      pushToast("success", t("settings_view.system_prompt.saved"));
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  async function onReset() {
    setResetting(true);
    try {
      await resetPrompt();
      pushToast("success", t("settings_view.system_prompt.reset_done"));
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setResetting(false);
    }
  }

  return (
    <div className="mt-2 rounded-lg border border-border bg-card/60 p-4">
      <div className="flex items-start gap-3">
        <FileText className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h4 className="font-display text-sm font-semibold">
              {t("settings_view.system_prompt.title")}
            </h4>
            <span
              className={`rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide ${
                isCustom
                  ? "border border-primary/40 bg-primary/10 text-primary"
                  : "border border-border bg-muted/60 text-muted-foreground"
              }`}
            >
              {isCustom
                ? t("settings_view.system_prompt.custom_badge")
                : t("settings_view.system_prompt.default_badge")}
            </span>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            {t("settings_view.system_prompt.description")}
          </p>

          {error && <p className="mt-3 text-xs text-destructive">{error}</p>}

          <label className="mt-4 block text-xs font-medium text-muted-foreground">
            {t("settings_view.system_prompt.editor_label")}
          </label>
          <textarea
            data-testid="system-prompt-editor"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            disabled={loading}
            spellCheck={false}
            rows={14}
            placeholder={t("settings_view.system_prompt.placeholder")}
            className="mt-1 w-full resize-y rounded-md border border-input bg-background px-3 py-2 font-mono text-xs leading-relaxed focus:outline-none focus:ring-1 focus:ring-primary disabled:opacity-50"
          />

          <div className="mt-1.5 flex items-center justify-between">
            <span className="font-mono text-[11px] text-muted-foreground">
              {t("settings_view.system_prompt.chars").replace("{0}", String(draft.length))}
            </span>
            {trimmedEmpty && (
              <span className="text-[11px] text-amber-500">
                {t("settings_view.system_prompt.empty_hint")}
              </span>
            )}
          </div>

          <div className="mt-4 flex flex-wrap items-center gap-3">
            <Button
              size="sm"
              onClick={onSave}
              disabled={saving || loading || !dirty || trimmedEmpty}
            >
              {saving
                ? t("settings_view.saving")
                : t("settings_view.system_prompt.save")}
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={onReset}
              disabled={resetting || loading || !canReset}
              className="gap-1.5"
            >
              <RotateCcw className="h-3.5 w-3.5" />
              {t("settings_view.system_prompt.reset")}
            </Button>
          </div>

          <p className="mt-3 text-[11px] text-muted-foreground">
            {t("settings_view.system_prompt.applies_next_turn")}
          </p>
        </div>
      </div>
    </div>
  );
}
