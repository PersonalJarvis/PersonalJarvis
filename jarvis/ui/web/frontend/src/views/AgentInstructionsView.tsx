import { useEffect, useState } from "react";
import { FilePlus2, RotateCcw, Save, ScrollText } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useAgentInstructions } from "@/hooks/useAgentInstructions";
import { useEventStore } from "@/store/events";
import { useT } from "@/i18n";

/**
 * AgentInstructionsView — full-page editor for the user's personal
 * standing-instructions file (an AGENTS.md / CLAUDE.md equivalent). The file is
 * named after the assistant (the heading shows the dynamic `<Name>.md`, e.g.
 * "Alex.md"). It is distinct from the System Prompt: here the user writes their
 * own preferences for how the assistant works with them. Changes apply on the
 * assistant's next message — no restart.
 *
 * Backed by /api/settings/agent-instructions (GET/PUT/DELETE) via
 * useAgentInstructions.
 */
export function AgentInstructionsView() {
  const t = useT();
  const { config, loading, error, save } = useAgentInstructions();
  const pushToast = useEventStore((s) => s.pushToast);

  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);

  // Reflect the server value on load and after every save. Coalesce a
  // missing `content` to "" so a malformed response degrades instead of crashing.
  useEffect(() => {
    if (config) setDraft(config.content ?? "");
  }, [config]);

  const filename = config?.filename ?? "…";
  const exists = !!config?.exists;
  const dirty = !!config && draft !== (config.content ?? "");
  const canRevert = dirty;
  const trimmedEmpty = draft.trim().length === 0;

  async function onSave() {
    if (!dirty) return;
    setSaving(true);
    try {
      await save(draft);
      pushToast("success", t("agent_instructions.saved"));
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  function onRevert() {
    if (config) setDraft(config.content ?? "");
  }

  function onLoadTemplate() {
    if (config?.template) setDraft(config.template);
  }

  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto p-6">
      <div className="flex items-start gap-3">
        <ScrollText className="mt-1 h-5 w-5 shrink-0 text-primary" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h1 className="font-display text-xl font-semibold">{filename}</h1>
            <span
              className={`rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide ${
                exists
                  ? "border border-primary/40 bg-primary/10 text-primary"
                  : "border border-border bg-muted/60 text-muted-foreground"
              }`}
            >
              {exists
                ? t("agent_instructions.active_badge")
                : t("agent_instructions.empty_badge")}
            </span>
          </div>
          <p className="mt-1 max-w-prose text-sm text-muted-foreground">
            {t("agent_instructions.subtitle")}
          </p>
        </div>
      </div>

      {error && <p className="text-xs text-destructive">{error}</p>}

      <div>
        <label className="block text-xs font-medium text-muted-foreground">
          {t("agent_instructions.editor_label")}
        </label>
        <textarea
          data-testid="agent-instructions-editor"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          disabled={loading}
          spellCheck={false}
          rows={18}
          placeholder={t("agent_instructions.placeholder")}
          className="mt-1 w-full resize-y rounded-md border border-input bg-background px-3 py-2 font-mono text-xs leading-relaxed focus:outline-none focus:ring-1 focus:ring-primary disabled:opacity-50"
        />
        <div className="mt-1.5 flex items-center justify-between">
          <span className="font-mono text-[11px] text-muted-foreground">
            {t("agent_instructions.chars").replace("{0}", String(draft.length))}
          </span>
          {trimmedEmpty && (
            <span className="text-[11px] text-amber-500">
              {t("agent_instructions.empty_hint")}
            </span>
          )}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <Button
          size="sm"
          onClick={onSave}
          disabled={saving || loading || !dirty}
          className="gap-1.5"
        >
          <Save className="h-3.5 w-3.5" />
          {saving ? t("agent_instructions.saving") : t("agent_instructions.save")}
        </Button>
        <Button
          size="sm"
          variant="outline"
          onClick={onLoadTemplate}
          disabled={loading || !config?.template}
          className="gap-1.5"
        >
          <FilePlus2 className="h-3.5 w-3.5" />
          {t("agent_instructions.load_template")}
        </Button>
        <Button
          size="sm"
          variant="outline"
          onClick={onRevert}
          disabled={loading || !canRevert}
          className="gap-1.5"
        >
          <RotateCcw className="h-3.5 w-3.5" />
          {t("agent_instructions.revert")}
        </Button>
      </div>

      <p className="text-[11px] text-muted-foreground">
        {t("agent_instructions.applies_next_turn")}
      </p>
    </div>
  );
}
