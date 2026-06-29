/**
 * SkillCreateDialog — create a new user skill from the desktop app.
 *
 * Two ways in, one robust outcome:
 *  - "Write the whole skill with AI": you describe what the skill should do and
 *    the AI writes the COMPLETE skill (name, instructions, voice trigger). It
 *    lands fully filled in for a quick review, then one click creates it. If a
 *    brain is reachable it fills rich content; if not it returns a deterministic
 *    starter template and the form stays editable (headless-VPS safe).
 *  - Fill the form yourself and hit Create — the deterministic POST /api/skills
 *    path, no brain needed.
 * Either way Create persists exactly what you see.
 */
import { useEffect, useState } from "react";
import { X, Sparkles, Loader2, Plus, AlertTriangle, Check } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useT } from "@/i18n";
import { useCreateSkill, useDraftSkill } from "@/hooks/useSkills";

/**
 * A body carries real instructions only if it has at least one non-heading,
 * non-blank line. Mirrors the backend guard (`body_has_instructions`) so the
 * form refuses a functionless skill before the round trip — the root cause of
 * the empty "Hallo Hallo Hallo" skill.
 */
function bodyHasInstructions(body: string): boolean {
  return body
    .split("\n")
    .some((line) => line.trim() !== "" && !line.trim().startsWith("#"));
}

export function SkillCreateDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated?: (name: string) => void;
}) {
  const t = useT();
  const draftSkill = useDraftSkill();
  const createSkill = useCreateSkill();

  const [intent, setIntent] = useState("");
  const [name, setName] = useState("");
  const [category, setCategory] = useState("general");
  const [description, setDescription] = useState("");
  const [body, setBody] = useState("");
  const [trigger, setTrigger] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [aiNote, setAiNote] = useState<string | null>(null);
  // True once the AI has written the whole skill into the fields — drives the
  // "review then create" affordance (success banner + highlighted Create).
  const [aiDrafted, setAiDrafted] = useState(false);
  const [aiOk, setAiOk] = useState(false);

  // Reset all state whenever the dialog (re)opens, and wire Escape-to-close.
  useEffect(() => {
    if (!open) return;
    setIntent("");
    setName("");
    setCategory("general");
    setDescription("");
    setBody("");
    setTrigger("");
    setError(null);
    setAiNote(null);
    setAiDrafted(false);
    setAiOk(false);
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const runDraft = async () => {
    setError(null);
    setAiNote(null);
    setAiDrafted(false);
    try {
      const res = await draftSkill.mutateAsync({
        intent,
        name_hint: name,
        category,
        trigger_hint: trigger,
      });
      const d = res.draft;
      setName(d.name ?? name);
      setDescription(d.description ?? "");
      setCategory(d.category ?? category);
      setBody(d.body ?? "");
      const firstVoice = (d.triggers ?? []).find((tr) => tr.type === "voice");
      if (firstVoice?.pattern) setTrigger(firstVoice.pattern);
      setAiOk(res.brain_used);
      setAiDrafted(true);
      setAiNote(
        res.brain_used
          ? t("skill_create_dialog.ai_done_review")
          : t("skill_create_dialog.brain_unavailable"),
      );
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const runCreate = async () => {
    setError(null);
    if (name.trim().length < 3) {
      setError(t("skill_create_dialog.name_required"));
      return;
    }
    // The intent describes what the skill should do. If the user typed it but
    // never drafted/filled the instructions, fold it into the body instead of
    // losing it — that was the empty-skill trap. Then require real instructions
    // so a functionless skill can never be created.
    let finalBody = body.trim();
    if (!bodyHasInstructions(finalBody) && intent.trim()) {
      finalBody = `## ${name.trim()}\n\n${intent.trim()}\n`;
    }
    if (!bodyHasInstructions(finalBody)) {
      setError(t("skill_create_dialog.needs_instructions"));
      return;
    }
    // Backfill the description from the intent so the brain can discover the
    // skill (an empty description makes it effectively invisible in the
    // AVAILABLE SKILLS listing).
    const finalDescription =
      description.trim() || intent.trim().slice(0, 200);
    try {
      const detail = await createSkill.mutateAsync({
        name: name.trim(),
        description: finalDescription,
        category: category.trim() || "general",
        body: finalBody,
        triggers: trigger.trim()
          ? [{ type: "voice", pattern: trigger.trim() }]
          : [],
      });
      onCreated?.(detail.name);
      onClose();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const busy = draftSkill.isPending || createSkill.isPending;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="flex max-h-[88vh] w-[640px] max-w-full flex-col overflow-hidden rounded-xl border border-border bg-card shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-border px-6 py-4">
          <div className="flex items-center gap-3">
            <div className="rounded-md bg-primary/10 p-2">
              <Plus className="h-5 w-5 text-primary" />
            </div>
            <div>
              <h2 className="text-lg font-semibold">
                {t("skill_create_dialog.title")}
              </h2>
              <p className="text-xs text-muted-foreground">
                {t("skill_create_dialog.subtitle")}
              </p>
            </div>
          </div>
          <Button size="icon" variant="ghost" onClick={onClose}>
            <X className="h-4 w-4" />
          </Button>
        </div>

        <ScrollArea className="flex-1">
          <div className="space-y-4 px-6 py-5">
            {/* Describe → let the AI write the whole skill */}
            <div className="rounded-lg border border-primary/30 bg-primary/[0.04] p-4">
              <label className="mb-1.5 block text-xs font-medium text-foreground">
                {t("skill_create_dialog.intent_label")}
              </label>
              <textarea
                value={intent}
                onChange={(e) => setIntent(e.target.value)}
                rows={2}
                placeholder={t("skill_create_dialog.intent_placeholder")}
                className="w-full resize-y rounded-md border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
              />
              <Button
                onClick={() => void runDraft()}
                disabled={busy || intent.trim().length < 3}
                className="mt-2.5 w-full gap-2"
              >
                {draftSkill.isPending ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" />
                    {t("skill_create_dialog.writing")}
                  </>
                ) : (
                  <>
                    <Sparkles className="h-4 w-4" />
                    {t("skill_create_dialog.write_with_ai")}
                  </>
                )}
              </Button>
            </div>

            {/* AI result banner */}
            {aiNote && (
              <div
                className={
                  aiOk
                    ? "flex items-start gap-2 rounded-md border border-emerald-500/40 bg-emerald-500/10 p-3 text-xs text-emerald-300"
                    : "flex items-start gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-amber-300"
                }
              >
                {aiOk ? (
                  <Check className="mt-0.5 h-3.5 w-3.5 flex-shrink-0" />
                ) : (
                  <AlertTriangle className="mt-0.5 h-3.5 w-3.5 flex-shrink-0" />
                )}
                <span>{aiNote}</span>
              </div>
            )}

            <div className="flex items-center gap-2 text-[11px] uppercase tracking-wider text-muted-foreground">
              <div className="h-px flex-1 bg-border" />
              {t("skill_create_dialog.or_review_edit")}
              <div className="h-px flex-1 bg-border" />
            </div>

            {/* Name + Category */}
            <div className="grid grid-cols-[1fr_180px] gap-3">
              <div>
                <label className="mb-1.5 block text-xs font-medium text-muted-foreground">
                  {t("skill_create_dialog.name_label")}
                </label>
                <input
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder={t("skill_create_dialog.name_placeholder")}
                  className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
                />
              </div>
              <div>
                <label className="mb-1.5 block text-xs font-medium text-muted-foreground">
                  {t("skill_create_dialog.category_label")}
                </label>
                <input
                  type="text"
                  value={category}
                  onChange={(e) => setCategory(e.target.value)}
                  className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
                />
              </div>
            </div>

            {/* Description */}
            <div>
              <label className="mb-1.5 block text-xs font-medium text-muted-foreground">
                {t("skill_create_dialog.description_label")}
              </label>
              <input
                type="text"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder={t("skill_create_dialog.description_placeholder")}
                className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
              />
            </div>

            {/* Body */}
            <div>
              <label className="mb-1.5 block text-xs font-medium text-muted-foreground">
                {t("skill_create_dialog.body_label")}
              </label>
              <textarea
                value={body}
                onChange={(e) => setBody(e.target.value)}
                rows={7}
                placeholder={t("skill_create_dialog.body_placeholder")}
                className="w-full resize-y rounded-md border border-border bg-background px-3 py-2 font-mono text-xs focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
              />
            </div>

            {/* Voice trigger */}
            <div>
              <label className="mb-1.5 block text-xs font-medium text-muted-foreground">
                {t("skill_create_dialog.trigger_label")}
              </label>
              <input
                type="text"
                value={trigger}
                onChange={(e) => setTrigger(e.target.value)}
                placeholder={t("skill_create_dialog.trigger_placeholder")}
                className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
              />
            </div>

            {error && (
              <div className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/10 p-3 text-xs text-destructive">
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 flex-shrink-0" />
                <span>
                  {t("skill_create_dialog.create_error")}: {error}
                </span>
              </div>
            )}
          </div>
        </ScrollArea>

        {/* Footer */}
        <div className="flex items-center justify-end gap-2 border-t border-border px-6 py-4">
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            {t("common.close")}
          </Button>
          <Button
            onClick={() => void runCreate()}
            disabled={busy}
            className={
              aiDrafted
                ? "gap-1.5 ring-2 ring-primary ring-offset-2 ring-offset-card"
                : "gap-1.5"
            }
          >
            {createSkill.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Plus className="h-3.5 w-3.5" />
            )}
            {t("skill_create_dialog.create")}
          </Button>
        </div>
      </div>
    </div>
  );
}
