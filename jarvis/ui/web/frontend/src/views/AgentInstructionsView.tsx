import { useEffect, useRef, useState } from "react";
import { FilePlus2, PenLine, RotateCcw, Save, ScrollText } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Textarea } from "@/components/ui/textarea";
import { PageHeader } from "@/components/layout/PageHeader";
import { useAgentInstructions } from "@/hooks/useAgentInstructions";
import { useEventStore } from "@/store/events";
import { useT } from "@/i18n";

/**
 * The assistant's standing instructions — one markdown file, edited in place.
 *
 * A PageHeader named after the file, the content capped at the reading
 * measure, and the editor as ONE card: the textarea, a character count, and
 * a sticky action row at its foot. Empty, the card opens with an EmptyState
 * offering the template or a blank page; the textarea stays mounted so the
 * page never swaps its editor out from under a keyboard.
 */
export function AgentInstructionsView() {
  const t = useT();
  const { config, loading, error, save } = useAgentInstructions();
  const pushToast = useEventStore((s) => s.pushToast);
  const editorRef = useRef<HTMLTextAreaElement>(null);

  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  // "Write from scratch" hides the empty state for this visit even though the
  // draft is still blank — the user asked for the blank page.
  const [writing, setWriting] = useState(false);

  useEffect(() => {
    if (config) setDraft(config.content ?? "");
  }, [config]);

  const filename = config?.filename ?? "…";
  const exists = !!config?.exists;
  const dirty = !!config && draft !== (config.content ?? "");
  const canRevert = dirty;
  const trimmedEmpty = draft.trim().length === 0;
  // Also while loading: the template button must be the same element before
  // and after the config lands, so a click queued on it is never lost.
  const showEmpty = trimmedEmpty && !writing;

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

  function onWriteFromScratch() {
    setWriting(true);
    requestAnimationFrame(() => editorRef.current?.focus());
  }

  return (
    <div className="flex h-full flex-col overflow-y-auto px-8 pb-6">
      <div className="mx-auto w-full max-w-3xl">
        <PageHeader
          icon={<ScrollText />}
          title={filename}
          description={t("agent_instructions.subtitle")}
          actions={
            !exists ? (
              <Badge variant="secondary">{t("agent_instructions.empty_badge")}</Badge>
            ) : undefined
          }
        />

        {error && <p className="mb-4 text-sm text-destructive">{error}</p>}

        <Card className="flex flex-col overflow-hidden">
          <label className="sr-only" htmlFor="agent-instructions-editor">
            {t("agent_instructions.editor_label")}
          </label>
          {showEmpty && (
            <EmptyState
              icon={<ScrollText />}
              title={t("agent_instructions.empty_badge")}
              description={t("agent_instructions.empty_hint")}
              actions={
                <>
                  <Button onClick={onLoadTemplate} disabled={!config?.template}>
                    <FilePlus2 />
                    {t("agent_instructions.load_template")}
                  </Button>
                  <Button variant="outline" onClick={onWriteFromScratch}>
                    <PenLine />
                    {t("agent_instructions.write_from_scratch")}
                  </Button>
                </>
              }
            />
          )}
          <Textarea
            id="agent-instructions-editor"
            ref={editorRef}
            data-testid="agent-instructions-editor"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onFocus={() => setWriting(true)}
            disabled={loading}
            spellCheck={false}
            placeholder={t("agent_instructions.placeholder")}
            className={
              showEmpty
                ? "sr-only"
                : "min-h-[60vh] resize-y rounded-none border-0 p-5 text-base leading-7 focus-visible:ring-0 focus-visible:ring-offset-0"
            }
          />
          {/* Sticky action row: count left, Save + Revert right. */}
          <div className="sticky bottom-0 flex items-center justify-between gap-3 border-t border-border bg-card px-5 py-3">
            <span className="text-sm tabular-nums text-foreground-faint">
              {t("agent_instructions.chars").replace("{0}", String(draft.length))}
            </span>
            <div className="flex items-center gap-2">
              {!showEmpty && (
                <Button
                  variant="ghost"
                  onClick={onLoadTemplate}
                  disabled={loading || !config?.template}
                >
                  <FilePlus2 />
                  {t("agent_instructions.load_template")}
                </Button>
              )}
              <Button variant="ghost" onClick={onRevert} disabled={loading || !canRevert}>
                <RotateCcw />
                {t("agent_instructions.revert")}
              </Button>
              <Button onClick={onSave} disabled={saving || loading || !dirty}>
                <Save />
                {saving ? t("agent_instructions.saving") : t("agent_instructions.save")}
              </Button>
            </div>
          </div>
        </Card>

        <p className="mt-3 text-sm text-muted-foreground">
          {t("agent_instructions.applies_next_turn")}
        </p>
      </div>
    </div>
  );
}
