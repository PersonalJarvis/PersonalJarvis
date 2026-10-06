import { useEffect, useState } from "react";
import { RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useSystemPrompt } from "@/hooks/useSystemPrompt";
import { useEventStore } from "@/store/events";
import { useT } from "@/i18n";
import { SettingsCard, SettingsSection } from "@/views/settings/SettingsLayout";

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
    <SettingsSection
      title={t("settings_view.system_prompt.title")}
      description={t("settings_view.system_prompt.description")}
      actions={
        <span
          className={`rounded-md border px-2 py-0.5 text-xs font-medium ${
            isCustom
              ? "border-accent/40 text-accent"
              : "border-border text-muted-foreground"
          }`}
        >
          {isCustom
            ? t("settings_view.system_prompt.custom_badge")
            : t("settings_view.system_prompt.default_badge")}
        </span>
      }
    >
      <SettingsCard className="p-4">
        {error && <p className="mb-3 text-sm text-destructive">{error}</p>}

        <label htmlFor="system-prompt-editor" className="sr-only">
          {t("settings_view.system_prompt.editor_label")}
        </label>
        <textarea
          id="system-prompt-editor"
          data-testid="system-prompt-editor"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          disabled={loading}
          spellCheck={false}
          rows={14}
          placeholder={t("settings_view.system_prompt.placeholder")}
          className="block w-full resize-y rounded-md border border-border bg-background px-3 py-2.5 font-mono text-sm leading-relaxed text-foreground placeholder:text-foreground-faint focus:border-accent focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
        />

        <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
          <div className="min-w-0 text-xs text-muted-foreground">
            <span className="font-mono tabular-nums">
              {t("settings_view.system_prompt.chars").replace("{0}", String(draft.length))}
            </span>
            <span aria-hidden> · </span>
            {trimmedEmpty ? (
              <span className="text-foreground">{t("settings_view.system_prompt.empty_hint")}</span>
            ) : (
              <span>{t("settings_view.system_prompt.applies_next_turn")}</span>
            )}
          </div>
          <div className="ml-auto flex items-center gap-2">
            <Button
              size="sm"
              variant="ghost"
              onClick={onReset}
              disabled={resetting || loading || !canReset}
            >
              <RotateCcw aria-hidden />
              {t("settings_view.system_prompt.reset")}
            </Button>
            <Button
              size="sm"
              onClick={onSave}
              disabled={saving || loading || !dirty || trimmedEmpty}
            >
              {saving
                ? t("settings_view.saving")
                : t("settings_view.system_prompt.save")}
            </Button>
          </div>
        </div>
      </SettingsCard>
    </SettingsSection>
  );
}
