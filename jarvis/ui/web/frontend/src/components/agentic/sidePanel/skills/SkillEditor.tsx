import { useEffect, useMemo, useRef, useState, type DragEvent } from "react";
import { createPortal } from "react-dom";
import { ClipboardPaste, CornerDownLeft, FileUp, Loader2, Sparkles, X } from "lucide-react";
import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { robustPaste } from "@/lib/clipboard";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { SKILL_HUES, SKILL_ICONS, type IdeSkill, type IdeSkillDraft, type SkillHue, type SkillIcon } from "@/lib/ideSkillsApi";
import {
  SKILL_ICON,
  SkillSeal,
  approxTokens,
  compactCount,
  lineCount,
  skillIconFor,
  skillStyle,
  splitSkillFrontmatter,
} from "./skillVisuals";
import { MARKDOWN_ACCEPT, deriveSkillDescription, readMarkdownFile, suggestSkillTitle } from "./skillImport";

export type EditorMode = "write" | "preview" | "split";

export interface SkillEditorSeed {
  /** The skill being edited; absent for a new one. */
  skill?: IdeSkill;
  /** Prefill for a new one (a pasted text, an imported file). */
  draft?: Partial<IdeSkillDraft>;
  mode?: EditorMode;
}

/**
 * Writing, pasting or importing a skill — and reading one at full size.
 *
 * A dialog over the whole IDE rather than a form inside the side panel: a
 * Markdown prompt wants room, and the panel can be as narrow as 260 px. The
 * seal in the header is the card's own and follows every change, so the
 * colour and glyph are chosen by looking at the result. Ctrl+Enter saves;
 * a Markdown file dropped anywhere on the dialog replaces the text.
 */
export function SkillEditor({ seed, onClose, onSave, onPaste, targetPane }: {
  seed: SkillEditorSeed;
  onClose: () => void;
  onSave: (draft: IdeSkillDraft, id?: string) => Promise<void>;
  /** Paste the skill as it is saved into the selected pane. */
  onPaste?: (skill: IdeSkill) => void;
  targetPane: string | null;
}) {
  const t = useT();
  const editing = seed.skill;
  const [title, setTitle] = useState(editing?.title ?? seed.draft?.title ?? "");
  const [content, setContent] = useState(editing?.content ?? seed.draft?.content ?? "");
  // A summary the library derived stays empty here, so it follows the text;
  // only one the user wrote themselves shows as their own.
  const [description, setDescription] = useState(() =>
    editing
      ? editing.description === deriveSkillDescription(editing.content) ? "" : editing.description
      : seed.draft?.description ?? "",
  );
  const [hue, setHue] = useState<SkillHue>(editing?.hue ?? seed.draft?.hue ?? SKILL_HUES[Math.floor(Math.random() * SKILL_HUES.length)]);
  const [icon, setIcon] = useState<SkillIcon>(editing?.icon ?? seed.draft?.icon ?? "auto");
  const [mode, setMode] = useState<EditorMode>(seed.mode ?? (editing ? "preview" : "write"));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const [fileOver, setFileOver] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const textArea = useRef<HTMLTextAreaElement>(null);
  const titleInput = useRef<HTMLInputElement>(null);
  const dialog = useRef<HTMLElement>(null);

  const suggested = useMemo(() => suggestSkillTitle(content), [content]);
  const { fields, body } = useMemo(() => splitSkillFrontmatter(content), [content]);
  const derivedDescription = useMemo(() => deriveSkillDescription(content), [content]);
  const effectiveTitle = title.trim() || suggested;
  const dirty =
    title !== (editing?.title ?? seed.draft?.title ?? "") ||
    content !== (editing?.content ?? seed.draft?.content ?? "") ||
    hue !== (editing?.hue ?? hue) ||
    icon !== (editing?.icon ?? seed.draft?.icon ?? "auto");
  const canSave = Boolean(effectiveTitle && content.trim()) && !saving;

  useEffect(() => {
    // A new skill starts in the text; an existing one opens to be read.
    const frame = requestAnimationFrame(() => {
      if (mode !== "preview") textArea.current?.focus();
      else dialog.current?.focus();
    });
    return () => cancelAnimationFrame(frame);
    // Only on open: later mode switches keep focus where the user put it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const close = () => {
    if (dirty && !confirmDiscard) {
      setConfirmDiscard(true);
      return;
    }
    onClose();
  };

  const save = async (alsoPaste = false) => {
    if (!canSave) return;
    setSaving(true);
    setError(null);
    try {
      await onSave(
        {
          title: effectiveTitle,
          content,
          // Empty means "derive it": the backend reads the text again.
          description: description.trim() || undefined,
          hue,
          icon,
        },
        editing?.id,
      );
      if (alsoPaste && editing && onPaste) onPaste({ ...editing, title: effectiveTitle, content, hue, icon });
      onClose();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
      setSaving(false);
    }
  };

  const takeText = (text: string, filename = "") => {
    setContent(text.replace(/\r\n?/g, "\n"));
    if (!title.trim()) setTitle(suggestSkillTitle(text, filename));
    setConfirmDiscard(false);
  };

  const importFile = async (file: File | undefined) => {
    if (!file) return;
    try {
      takeText(await readMarkdownFile(file), file.name);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    }
  };

  const pasteClipboard = async () => {
    const text = await robustPaste();
    if (text) takeText(text);
    else setError(t("ide_side_panel.skills.clipboard_empty"));
  };

  const dropFile = (event: DragEvent<HTMLElement>) => {
    if (!Array.from(event.dataTransfer.types).includes("Files")) return;
    event.preventDefault();
    setFileOver(false);
    void importFile(event.dataTransfer.files?.[0]);
  };

  const words = content.trim() ? content.trim().split(/\s+/).length : 0;
  const AutoIcon = skillIconFor("auto", effectiveTitle, description || derivedDescription);

  return createPortal(
    <div
      className="fixed inset-0 z-[90] flex items-center justify-center bg-background/70 p-3 backdrop-blur-sm animate-in fade-in-0 duration-150 motion-reduce:animate-none sm:p-6"
      role="presentation"
      onMouseDown={(event) => { if (event.target === event.currentTarget) close(); }}
    >
      <section
        ref={dialog}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label={editing ? fill(t("ide_side_panel.skills.editor_edit_aria"), { title: editing.title }) : t("ide_side_panel.skills.editor_new_aria")}
        data-testid="skill-editor"
        data-ide-dialog
        style={skillStyle(hue)}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.stopPropagation();
            close();
          } else if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
            event.preventDefault();
            void save(event.shiftKey);
          }
        }}
        onDragOver={(event) => {
          if (!Array.from(event.dataTransfer.types).includes("Files")) return;
          event.preventDefault();
          event.dataTransfer.dropEffect = "copy";
          setFileOver(true);
        }}
        onDragLeave={(event) => { if (event.currentTarget === event.target) setFileOver(false); }}
        onDrop={dropFile}
        className="relative flex h-[min(820px,100%)] w-full max-w-5xl flex-col overflow-hidden rounded-2xl border border-border-strong bg-card shadow-float outline-none animate-in zoom-in-[0.98] duration-200 motion-reduce:animate-none"
      >
        {/* The hue's light across the top edge: the dialog wears the skill. */}
        <div aria-hidden className="pointer-events-none absolute inset-x-0 top-0 h-40 bg-[radial-gradient(120%_100%_at_0%_0%,hsl(var(--skill)/0.16),transparent_60%)]" />
        <div aria-hidden className="pointer-events-none absolute inset-x-0 top-0 h-px bg-[linear-gradient(90deg,transparent,hsl(var(--skill)/0.7),transparent)]" />

        <header className="relative flex items-start gap-4 px-5 pb-4 pt-5">
          <SkillSeal hue={hue} icon={icon} title={effectiveTitle} description={description || derivedDescription} size="lg" />
          <div className="min-w-0 flex-1">
            <label htmlFor="skill-title" className="text-xs font-medium uppercase tracking-wider text-[hsl(var(--skill))]">
              {editing ? t("ide_side_panel.skills.editor_eyebrow_edit") : t("ide_side_panel.skills.editor_eyebrow_new")}
            </label>
            <input
              id="skill-title"
              ref={titleInput}
              data-testid="skill-editor-title"
              value={title}
              maxLength={120}
              onChange={(event) => { setTitle(event.target.value); setConfirmDiscard(false); }}
              placeholder={suggested || t("ide_side_panel.skills.title_placeholder")}
              className="mt-0.5 w-full bg-transparent font-display text-xl text-foreground-strong outline-none placeholder:text-foreground-faint"
            />
            <input
              aria-label={t("ide_side_panel.skills.description_label")}
              value={description}
              maxLength={280}
              onChange={(event) => setDescription(event.target.value)}
              placeholder={derivedDescription || t("ide_side_panel.skills.description_placeholder")}
              className="mt-1 w-full bg-transparent text-sm text-foreground-secondary outline-none placeholder:text-foreground-faint"
            />
          </div>
          <button
            type="button"
            aria-label={t("ide_side_panel.skills.close")}
            onClick={close}
            className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <X className="h-4 w-4" aria-hidden />
          </button>
        </header>

        {/* Look: colour and glyph, picked by watching the seal above. */}
        <div className="relative flex flex-wrap items-center gap-x-6 gap-y-3 border-y border-border/70 bg-background/30 px-5 py-2.5">
          <div role="radiogroup" aria-label={t("ide_side_panel.skills.colour_label")} className="flex items-center gap-1.5">
            <span className="mr-1 text-xs text-muted-foreground">{t("ide_side_panel.skills.colour_label")}</span>
            {SKILL_HUES.map((entry) => (
              <button
                key={entry}
                type="button"
                role="radio"
                aria-checked={hue === entry}
                aria-label={t(`ide_side_panel.skills.hue.${entry}`)}
                title={t(`ide_side_panel.skills.hue.${entry}`)}
                onClick={() => setHue(entry)}
                style={skillStyle(entry)}
                className={cn(
                  "h-5 w-5 rounded-full bg-[hsl(var(--skill))] transition-transform focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  hue === entry ? "scale-110 ring-2 ring-[hsl(var(--skill)/0.45)] ring-offset-2 ring-offset-card" : "opacity-70 hover:scale-110 hover:opacity-100",
                )}
              />
            ))}
          </div>
          <div role="radiogroup" aria-label={t("ide_side_panel.skills.icon_label")} className="flex flex-wrap items-center gap-0.5">
            <span className="mr-1.5 text-xs text-muted-foreground">{t("ide_side_panel.skills.icon_label")}</span>
            {SKILL_ICONS.map((entry) => {
              const Glyph = entry === "auto" ? AutoIcon : SKILL_ICON[entry];
              const selected = icon === entry;
              return (
                <button
                  key={entry}
                  type="button"
                  role="radio"
                  aria-checked={selected}
                  aria-label={t(`ide_side_panel.skills.icon.${entry}`)}
                  title={t(`ide_side_panel.skills.icon.${entry}`)}
                  onClick={() => setIcon(entry)}
                  className={cn(
                    "relative inline-flex h-7 w-7 items-center justify-center rounded-md transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                    selected ? "bg-[hsl(var(--skill)/0.16)] text-[hsl(var(--skill))]" : "text-muted-foreground hover:bg-secondary hover:text-foreground",
                  )}
                >
                  <Glyph className="h-4 w-4" aria-hidden />
                  {entry === "auto" && <Sparkles className="absolute -right-0.5 -top-0.5 h-2.5 w-2.5 text-[hsl(var(--skill))]" aria-hidden />}
                </button>
              );
            })}
          </div>
        </div>

        {/* Write / Preview / Side by side, plus the two ways text arrives. */}
        <div className="relative flex items-center gap-1 px-5">
          <div role="tablist" aria-label={t("ide_side_panel.skills.view_label")} className="flex items-center gap-4">
            {(["write", "preview", "split"] as const).map((entry) => (
              <button
                key={entry}
                type="button"
                role="tab"
                aria-selected={mode === entry}
                data-testid={`skill-editor-mode-${entry}`}
                onClick={() => setMode(entry)}
                className={cn(
                  "relative py-2.5 text-sm transition-colors focus-visible:outline-none",
                  entry === "split" && "hidden md:block",
                  mode === entry ? "font-medium text-foreground" : "text-muted-foreground hover:text-foreground",
                )}
              >
                {t(`ide_side_panel.skills.view_${entry}`)}
                {mode === entry && <span aria-hidden className="absolute inset-x-0 -bottom-px h-0.5 rounded-full bg-[hsl(var(--skill))]" />}
              </button>
            ))}
          </div>
          <div className="ml-auto flex items-center gap-1">
            <button
              type="button"
              onClick={() => void pasteClipboard()}
              className="inline-flex h-8 items-center gap-1.5 rounded-md px-2.5 text-sm text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <ClipboardPaste className="h-4 w-4" aria-hidden />
              <span className="hidden sm:inline">{t("ide_side_panel.skills.paste_clipboard")}</span>
            </button>
            <button
              type="button"
              onClick={() => fileInput.current?.click()}
              className="inline-flex h-8 items-center gap-1.5 rounded-md px-2.5 text-sm text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <FileUp className="h-4 w-4" aria-hidden />
              <span className="hidden sm:inline">{t("ide_side_panel.skills.import_file")}</span>
            </button>
            <input
              ref={fileInput}
              type="file"
              accept={MARKDOWN_ACCEPT}
              className="hidden"
              onChange={(event) => { void importFile(event.target.files?.[0]); event.target.value = ""; }}
            />
          </div>
        </div>

        <div className={cn("relative grid min-h-0 flex-1 border-t border-border/70", mode === "split" ? "md:grid-cols-2" : "grid-cols-1")}>
          {mode !== "preview" && (
            <div className={cn("relative min-h-0", mode === "split" && "md:border-r md:border-border/70")}>
              <textarea
                ref={textArea}
                data-testid="skill-editor-content"
                value={content}
                spellCheck={false}
                onChange={(event) => { setContent(event.target.value); setConfirmDiscard(false); }}
                onPaste={(event) => {
                  // A fresh skill takes its title from the first paste.
                  if (title.trim() || content.trim()) return;
                  const text = event.clipboardData.getData("text/plain");
                  if (text) setTitle(suggestSkillTitle(text));
                }}
                placeholder={t("ide_side_panel.skills.content_placeholder")}
                className="h-full w-full resize-none bg-transparent px-5 py-4 font-mono text-sm leading-6 text-foreground outline-none placeholder:text-foreground-faint"
              />
            </div>
          )}
          {mode !== "write" && (
            <div className="min-h-0 overflow-y-auto px-6 py-5" data-testid="skill-editor-preview">
              {fields.length > 0 && (
                <dl className="mb-4 flex flex-wrap gap-1.5">
                  {fields.map(([key, value]) => (
                    <div key={key} className="flex max-w-full items-center gap-1.5 rounded-md border border-border bg-background/40 px-2 py-0.5 text-xs">
                      <dt className="font-mono text-[hsl(var(--skill))]">{key}</dt>
                      <dd className="truncate text-muted-foreground">{value}</dd>
                    </div>
                  ))}
                </dl>
              )}
              {body.trim() ? (
                <div className="prose prose-sm prose-neutral max-w-none text-foreground dark:prose-invert prose-headings:font-display prose-headings:tracking-tight prose-h1:text-xl prose-h2:text-base prose-a:text-accent prose-code:text-foreground prose-code:before:content-none prose-code:after:content-none prose-pre:border prose-pre:border-border prose-pre:bg-background/60 prose-li:my-0.5 prose-strong:text-foreground-strong [overflow-wrap:anywhere]">
                  {/* Links stay text here: a preview is read, never navigated away from. */}
                  <ReactMarkdown remarkPlugins={[remarkGfm]} components={{ a: ({ children }) => <span className="text-accent underline underline-offset-2">{children}</span> }}>
                    {body}
                  </ReactMarkdown>
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">{t("ide_side_panel.skills.preview_empty")}</p>
              )}
            </div>
          )}
        </div>

        <footer className="relative flex flex-wrap items-center gap-3 border-t border-border/70 bg-background/30 px-5 py-3">
          <p className="flex items-center gap-3 text-xs tabular-nums text-muted-foreground">
            <span>{fill(t("ide_side_panel.skills.stat_lines"), { n: compactCount(lineCount(content)) })}</span>
            <span aria-hidden className="h-3 w-px bg-border" />
            <span>{fill(t("ide_side_panel.skills.stat_words"), { n: compactCount(words) })}</span>
            <span aria-hidden className="h-3 w-px bg-border" />
            <span>{fill(t("ide_side_panel.skills.stat_tokens"), { n: compactCount(content ? approxTokens(content) : 0) })}</span>
          </p>
          <div className="ml-auto flex items-center gap-2">
            {error && <p role="alert" className="max-w-xs truncate text-xs text-destructive" title={error}>{error}</p>}
            {confirmDiscard ? (
              <>
                <span className="text-xs text-muted-foreground">{t("ide_side_panel.skills.discard_question")}</span>
                <button type="button" onClick={onClose} className="h-8 rounded-md px-3 text-sm text-destructive transition-colors hover:bg-destructive/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                  {t("ide_side_panel.skills.discard_yes")}
                </button>
                <button type="button" onClick={() => setConfirmDiscard(false)} className="h-8 rounded-md px-3 text-sm text-foreground transition-colors hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                  {t("ide_side_panel.skills.discard_keep")}
                </button>
              </>
            ) : (
              <button type="button" onClick={close} className="h-8 rounded-md px-3 text-sm text-foreground transition-colors hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                {t("ide_side_panel.skills.cancel")}
              </button>
            )}
            {editing && onPaste && targetPane && (
              <button
                type="button"
                disabled={!canSave}
                onClick={() => void save(true)}
                title={`${fill(t("ide_side_panel.skills.save_and_paste"), { pane: targetPane })} (Ctrl+Shift+Enter)`}
                className="hidden h-8 items-center gap-1.5 rounded-md border border-border-strong px-3 text-sm text-foreground transition-colors hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-40 sm:inline-flex"
              >
                <CornerDownLeft className="h-3.5 w-3.5" aria-hidden />
                <span className="max-w-[12rem] truncate">{fill(t("ide_side_panel.skills.save_and_paste"), { pane: targetPane })}</span>
              </button>
            )}
            <button
              type="button"
              data-testid="skill-editor-save"
              disabled={!canSave}
              onClick={() => void save()}
              title="Ctrl+Enter"
              className="inline-flex h-8 items-center gap-1.5 rounded-md bg-[hsl(var(--skill))] px-3.5 text-sm font-medium text-background shadow-[0_6px_18px_-8px_hsl(var(--skill)/0.8)] transition-[filter,opacity] hover:brightness-110 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-card disabled:opacity-40 disabled:shadow-none"
            >
              {saving && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />}
              {editing ? t("ide_side_panel.skills.save_changes") : t("ide_side_panel.skills.save_new")}
            </button>
          </div>
        </footer>

        {fileOver && (
          <div className="pointer-events-none absolute inset-2 z-10 flex items-center justify-center rounded-xl border-2 border-dashed border-[hsl(var(--skill)/0.6)] bg-card/90 animate-in fade-in-0 duration-100">
            <p className="flex items-center gap-2 text-sm font-medium text-foreground">
              <FileUp className="h-4 w-4 text-[hsl(var(--skill))]" aria-hidden />
              {t("ide_side_panel.skills.drop_file_editor")}
            </p>
          </div>
        )}
      </section>
    </div>,
    document.body,
  );
}
