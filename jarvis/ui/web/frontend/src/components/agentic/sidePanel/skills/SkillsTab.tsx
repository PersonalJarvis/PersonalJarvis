import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent } from "react";
import {
  ArrowDownAZ,
  ArrowUpDown,
  Check,
  Clock3,
  FileUp,
  GripVertical,
  Loader2,
  MousePointerClick,
  Plus,
  RotateCcw,
  Search,
  TrendingUp,
  X,
  type LucideIcon,
} from "lucide-react";
import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { robustCopy, robustPaste } from "@/lib/clipboard";
import { deriveIdeSkillFields, type IdeSkill, type IdeSkillDraft } from "@/lib/ideSkillsApi";
import { useEventStore } from "@/store/events";
import { useIdeSkillsStore } from "@/store/ideSkills";
import { pasteSkillIntoPane } from "@/components/agentic/skillDrag";
import { SkillCard, type DropSide } from "./SkillCard";
import { SkillEditor, type SkillEditorSeed } from "./SkillEditor";
import { SkillMenu, type SkillMenuItem } from "./SkillMenu";
import { SkillsHero } from "./SkillsHero";
import { MARKDOWN_ACCEPT, isMarkdownFile, readMarkdownFile, suggestSkillTitle } from "./skillImport";
import { STARTER_SKILLS } from "./starterSkills";

type SortKey = "manual" | "used" | "recent" | "name";
const SORT_KEY = "jarvis.agenticIde.skillsSort.v1";
const SORTS: readonly { key: SortKey; icon: LucideIcon }[] = [
  { key: "manual", icon: GripVertical },
  { key: "used", icon: TrendingUp },
  { key: "recent", icon: Clock3 },
  { key: "name", icon: ArrowDownAZ },
];

function storedSort(): SortKey {
  try {
    const value = localStorage.getItem(SORT_KEY);
    return SORTS.some((sort) => sort.key === value) ? (value as SortKey) : "manual";
  } catch {
    return "manual";
  }
}

function sortSkills(skills: IdeSkill[], key: SortKey): IdeSkill[] {
  if (key === "manual") return skills;
  const sorted = [...skills];
  if (key === "used") sorted.sort((a, b) => b.use_count - a.use_count || (b.last_used_at ?? "").localeCompare(a.last_used_at ?? ""));
  if (key === "recent") sorted.sort((a, b) => (b.last_used_at ?? b.updated_at).localeCompare(a.last_used_at ?? a.updated_at));
  if (key === "name") sorted.sort((a, b) => a.title.localeCompare(b.title, undefined, { sensitivity: "base" }));
  return sorted;
}

function matches(skill: IdeSkill, query: string): boolean {
  const needle = query.trim().toLocaleLowerCase();
  if (!needle) return true;
  return [skill.title, skill.description, skill.content].some((field) => field.toLocaleLowerCase().includes(needle));
}

function slugify(title: string): string {
  return title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 60) || "skill";
}

/**
 * The side panel's Skills tab: a library of saved Markdown prompts.
 *
 * A skill is pasted, Markdown and all, into a coding agent's prompt by
 * dragging its card onto a terminal pane — or with its "Paste into …" button,
 * which goes to the pane selected in the grid. Text comes in three ways: typed
 * or pasted in the editor, a Markdown file picked or dropped on the tab
 * (several at once are saved straight away), or the three examples an empty
 * library offers.
 */
export function SkillsTab() {
  const t = useT();
  const skills = useIdeSkillsStore((state) => state.skills);
  const status = useIdeSkillsStore((state) => state.status);
  const loadError = useIdeSkillsStore((state) => state.error);
  const load = useIdeSkillsStore((state) => state.load);
  const create = useIdeSkillsStore((state) => state.create);
  const update = useIdeSkillsStore((state) => state.update);
  const remove = useIdeSkillsStore((state) => state.remove);
  const move = useIdeSkillsStore((state) => state.move);
  const target = useIdeSkillsStore((state) => state.target);
  const carrying = useIdeSkillsStore((state) => state.dragging);
  const pushToast = useEventStore((state) => state.pushToast);

  const [query, setQuery] = useState("");
  const [sort, setSortState] = useState<SortKey>(storedSort);
  const [sortOpen, setSortOpen] = useState(false);
  const [editor, setEditor] = useState<SkillEditorSeed | null>(null);
  const [fileOver, setFileOver] = useState(false);
  const [importing, setImporting] = useState(false);
  const [addingExamples, setAddingExamples] = useState(false);
  const [dropMark, setDropMark] = useState<{ id: string; side: DropSide } | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const sortButton = useRef<HTMLButtonElement>(null);
  const searchInput = useRef<HTMLInputElement>(null);
  const fileDepth = useRef(0);

  useEffect(() => {
    void load();
  }, [load]);

  const setSort = (key: SortKey) => {
    setSortState(key);
    try {
      localStorage.setItem(SORT_KEY, key);
    } catch {
      /* a convenience only: the order still applies for this session */
    }
  };

  const visible = useMemo(() => sortSkills(skills.filter((skill) => matches(skill, query)), sort), [skills, query, sort]);
  const reorderable = sort === "manual" && !query.trim();
  const fail = useCallback((error: unknown) => pushToast("error", error instanceof Error ? error.message : String(error)), [pushToast]);

  const paste = useCallback((skill: IdeSkill) => {
    if (!target) return;
    pasteSkillIntoPane({ ...target, skill: { id: skill.id, title: skill.title, content: skill.content, hue: skill.hue } });
  }, [target]);

  const openEditor = useCallback((skill: IdeSkill, mode: "preview" | "edit") => {
    setEditor({ skill, mode: mode === "edit" ? "write" : "preview" });
  }, []);

  const duplicate = useCallback((skill: IdeSkill) => {
    create({
      title: fill(t("ide_side_panel.skills.copy_title"), { title: skill.title }).slice(0, 120),
      content: skill.content,
      hue: skill.hue,
      icon: skill.icon,
    }).catch(fail);
  }, [create, fail, t]);

  const deleteSkill = useCallback((skill: IdeSkill) => {
    remove(skill.id)
      .then(() => pushToast("info", fill(t("ide_side_panel.skills.deleted"), { title: skill.title })))
      .catch(fail);
  }, [remove, pushToast, fail, t]);

  const copy = useCallback((skill: IdeSkill) => {
    void robustCopy(skill.content).then((ok) =>
      pushToast(ok ? "success" : "error", ok ? fill(t("ide_side_panel.skills.copied"), { title: skill.title }) : t("ide_side_panel.skills.copy_failed")),
    );
  }, [pushToast, t]);

  const exportSkill = useCallback((skill: IdeSkill) => {
    const url = URL.createObjectURL(new Blob([skill.content], { type: "text/markdown;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `${slugify(skill.title)}.md`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1_000);
  }, []);

  const reorderOver = useCallback((skill: IdeSkill, side: DropSide) => {
    setDropMark((current) => (side ? { id: skill.id, side } : current?.id === skill.id ? null : current));
  }, []);

  const reorderDrop = useCallback((skill: IdeSkill) => {
    const moving = useIdeSkillsStore.getState().dragging;
    const mark = dropMark;
    setDropMark(null);
    if (!moving || moving.id === skill.id) return;
    let beforeId: string | null = skill.id;
    if (mark?.side === "after") {
      const index = skills.findIndex((entry) => entry.id === skill.id);
      beforeId = skills.slice(index + 1).find((entry) => entry.id !== moving.id)?.id ?? null;
    }
    move(moving.id, beforeId).catch(fail);
  }, [dropMark, skills, move, fail]);

  // Several files are saved as they are; one opens in the editor to be named.
  const importFiles = useCallback(async (files: File[]) => {
    const markdown = files.filter(isMarkdownFile);
    if (markdown.length === 0) {
      if (files.length) pushToast("warning", t("ide_side_panel.skills.import_not_markdown"));
      return;
    }
    if (markdown.length === 1) {
      try {
        const text = await readMarkdownFile(markdown[0]);
        setEditor({ draft: { title: suggestSkillTitle(text, markdown[0].name), content: text }, mode: "split" });
      } catch (error) {
        fail(error);
      }
      return;
    }
    setImporting(true);
    let saved = 0;
    for (const file of [...markdown].reverse()) {
      try {
        const text = await readMarkdownFile(file);
        const derived = await deriveIdeSkillFields(text, file.name);
        await create({ title: derived.title || suggestSkillTitle(text, file.name) || file.name, content: text });
        saved += 1;
      } catch (error) {
        fail(error);
      }
    }
    setImporting(false);
    if (saved) pushToast("success", fill(t("ide_side_panel.skills.imported"), { n: saved }));
  }, [create, fail, pushToast, t]);

  const pasteFromClipboard = useCallback(async () => {
    const text = await robustPaste();
    if (!text?.trim()) {
      pushToast("info", t("ide_side_panel.skills.clipboard_empty"));
      setEditor({ mode: "write" });
      return;
    }
    setEditor({ draft: { title: suggestSkillTitle(text), content: text }, mode: "split" });
  }, [pushToast, t]);

  const addExamples = useCallback(async () => {
    setAddingExamples(true);
    try {
      for (const starter of [...STARTER_SKILLS].reverse()) await create(starter);
    } catch (error) {
      fail(error);
    } finally {
      setAddingExamples(false);
    }
  }, [create, fail]);

  const save = useCallback(async (draft: IdeSkillDraft, id?: string) => {
    if (id) await update(id, { ...draft, description: draft.description ?? "" });
    else await create(draft);
  }, [create, update]);

  // A Markdown file from the desktop, dropped anywhere on the tab.
  const fileDrag = {
    onDragEnter: (event: DragEvent<HTMLDivElement>) => {
      if (!Array.from(event.dataTransfer.types).includes("Files")) return;
      event.preventDefault();
      fileDepth.current += 1;
      setFileOver(true);
    },
    onDragOver: (event: DragEvent<HTMLDivElement>) => {
      if (!Array.from(event.dataTransfer.types).includes("Files")) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = "copy";
    },
    onDragLeave: () => {
      fileDepth.current = Math.max(0, fileDepth.current - 1);
      if (fileDepth.current === 0) setFileOver(false);
    },
    onDrop: (event: DragEvent<HTMLDivElement>) => {
      if (!Array.from(event.dataTransfer.types).includes("Files")) return;
      event.preventDefault();
      fileDepth.current = 0;
      setFileOver(false);
      void importFiles(Array.from(event.dataTransfer.files ?? []));
    },
  };

  const sortItems: SkillMenuItem[] = SORTS.map(({ key, icon }) => ({
    id: `sort-${key}`,
    label: t(`ide_side_panel.skills.sort_${key}`),
    icon,
    hint: sort === key ? <Check className="h-3.5 w-3.5 text-accent" aria-hidden /> : undefined,
    onSelect: () => setSort(key),
  }));

  const empty = status === "ready" && skills.length === 0;

  return (
    <div data-testid="ide-skills-tab" className="relative flex h-full min-h-0 flex-col" {...fileDrag}>
      <div className="shrink-0 space-y-3 px-3 pb-3 pt-3.5">
        <div className="flex items-start gap-2.5">
          <div className="min-w-0 flex-1">
            <h2 className="flex items-baseline gap-2 font-display text-base text-foreground-strong">
              {t("ide_side_panel.tabs.skills")}
              {skills.length > 0 && <span className="text-xs font-normal tabular-nums text-muted-foreground">{skills.length}</span>}
            </h2>
            <p className="truncate text-xs text-muted-foreground">{t("ide_side_panel.skills.subtitle")}</p>
          </div>
          {!empty && (
            <button
              type="button"
              data-testid="skills-new"
              onClick={() => setEditor({ mode: "write" })}
              className="inline-flex h-8 shrink-0 items-center gap-1.5 rounded-md bg-primary px-2.5 text-sm font-medium text-primary-foreground transition-opacity hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
            >
              <Plus className="h-4 w-4" aria-hidden />
              {t("ide_side_panel.skills.new_short")}
            </button>
          )}
        </div>

        {!empty && (
          <div className="flex items-center gap-1.5">
            <label className="group/search relative flex h-8 min-w-0 flex-1 items-center rounded-md border border-border bg-input/60 transition-colors focus-within:border-accent focus-within:ring-2 focus-within:ring-ring/40">
              <Search className="pointer-events-none absolute left-2.5 h-3.5 w-3.5 text-muted-foreground" aria-hidden />
              <input
                ref={searchInput}
                data-testid="skills-search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={(event) => { if (event.key === "Escape" && query) { event.stopPropagation(); setQuery(""); } }}
                placeholder={t("ide_side_panel.skills.search_placeholder")}
                aria-label={t("ide_side_panel.skills.search_label")}
                className="h-full w-full bg-transparent pl-8 pr-7 text-sm text-foreground outline-none placeholder:text-foreground-faint"
              />
              {query && (
                <button
                  type="button"
                  aria-label={t("ide_side_panel.skills.search_clear")}
                  onClick={() => { setQuery(""); searchInput.current?.focus(); }}
                  className="absolute right-1.5 inline-flex h-5 w-5 items-center justify-center rounded text-muted-foreground hover:bg-secondary hover:text-foreground"
                >
                  <X className="h-3.5 w-3.5" aria-hidden />
                </button>
              )}
            </label>
            <button
              ref={sortButton}
              type="button"
              aria-haspopup="menu"
              aria-expanded={sortOpen}
              aria-label={fill(t("ide_side_panel.skills.sort_label"), { order: t(`ide_side_panel.skills.sort_${sort}`) })}
              title={fill(t("ide_side_panel.skills.sort_label"), { order: t(`ide_side_panel.skills.sort_${sort}`) })}
              onClick={() => setSortOpen((value) => !value)}
              className={cn(
                "inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-border transition-colors hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                sort === "manual" ? "text-muted-foreground" : "text-accent",
              )}
            >
              <ArrowUpDown className="h-3.5 w-3.5" aria-hidden />
            </button>
            <button
              type="button"
              data-testid="skills-import"
              disabled={importing}
              onClick={() => fileInput.current?.click()}
              aria-label={t("ide_side_panel.skills.import_file")}
              title={t("ide_side_panel.skills.import_file")}
              className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-border text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
            >
              {importing ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> : <FileUp className="h-3.5 w-3.5" aria-hidden />}
            </button>
            <SkillMenu anchor={sortButton} open={sortOpen} onClose={() => setSortOpen(false)} items={sortItems} label={t("ide_side_panel.skills.sort_menu")} />
          </div>
        )}
        <input
          ref={fileInput}
          type="file"
          multiple
          accept={MARKDOWN_ACCEPT}
          className="hidden"
          data-testid="skills-file-input"
          onChange={(event) => { void importFiles(Array.from(event.target.files ?? [])); event.target.value = ""; }}
        />
      </div>

      <div className="relative min-h-0 flex-1 overflow-y-auto overscroll-contain px-3 pb-3">
        {status === "loading" && skills.length === 0 && (
          <div className="space-y-2" aria-busy="true" aria-label={t("ide_side_panel.skills.loading")}>
            {[0, 1, 2].map((index) => (
              <div key={index} className="flex gap-3 rounded-xl border border-border bg-card/40 p-3">
                <div className="h-9 w-9 animate-pulse rounded-[10px] bg-secondary" />
                <div className="flex-1 space-y-2 pt-1">
                  <div className="h-3 w-1/2 animate-pulse rounded bg-secondary" />
                  <div className="h-2.5 w-4/5 animate-pulse rounded bg-secondary" />
                </div>
              </div>
            ))}
          </div>
        )}
        {status === "error" && skills.length === 0 && (
          <div className="flex flex-col items-center gap-3 px-4 py-10 text-center">
            <p className="text-sm text-muted-foreground">{fill(t("ide_side_panel.skills.load_failed"), { error: loadError ?? "" })}</p>
            <button
              type="button"
              onClick={() => void load(true)}
              className="inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1 text-sm text-foreground hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <RotateCcw className="h-3.5 w-3.5" aria-hidden />
              {t("ide_side_panel.skills.retry")}
            </button>
          </div>
        )}
        {empty && (
          <SkillsHero
            onNew={() => setEditor({ mode: "write" })}
            onPaste={() => void pasteFromClipboard()}
            onImport={() => fileInput.current?.click()}
            onExamples={() => void addExamples()}
            addingExamples={addingExamples}
          />
        )}
        {skills.length > 0 && visible.length === 0 && (
          <p className="px-4 py-10 text-center text-sm text-muted-foreground">{fill(t("ide_side_panel.skills.no_match"), { query: query.trim() })}</p>
        )}
        {visible.length > 0 && (
          <div role="list" aria-label={t("ide_side_panel.skills.list_label")} className="space-y-2" onDragLeave={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDropMark(null);
          }}>
            {visible.map((skill) => (
              <SkillCard
                key={skill.id}
                skill={skill}
                targetPane={target?.pane ?? null}
                reorderable={reorderable}
                dropSide={dropMark?.id === skill.id && carrying?.id !== skill.id ? dropMark.side : null}
                onPaste={paste}
                onOpen={openEditor}
                onDuplicate={duplicate}
                onDelete={deleteSkill}
                onCopy={copy}
                onExport={exportSkill}
                onReorderOver={reorderOver}
                onReorderDrop={reorderDrop}
              />
            ))}
          </div>
        )}
      </div>

      {/* Where a click pastes, or — while a card is carried — where to drop it. */}
      {skills.length > 0 && (
        <div className="shrink-0 border-t border-border/60 px-3 py-2">
          {carrying ? (
            <p className="flex items-center gap-1.5 text-xs font-medium text-foreground animate-in fade-in-0 duration-150">
              <MousePointerClick className="h-3.5 w-3.5 text-accent" aria-hidden />
              {t("ide_side_panel.skills.hint_dragging")}
            </p>
          ) : target ? (
            <p className="flex min-w-0 items-center gap-1.5 text-xs text-muted-foreground">
              <span aria-hidden className="relative flex h-2 w-2 shrink-0">
                <span className="absolute inline-flex h-full w-full rounded-full bg-success/60 motion-safe:animate-ping" />
                <span className="relative inline-flex h-2 w-2 rounded-full bg-success" />
              </span>
              <span className="truncate">{fill(t("ide_side_panel.skills.hint_target"), { pane: target.pane })}</span>
            </p>
          ) : (
            <p className="text-xs text-muted-foreground">{t("ide_side_panel.skills.hint_no_target")}</p>
          )}
        </div>
      )}

      {fileOver && (
        <div className="pointer-events-none absolute inset-2 z-20 flex flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed border-accent/60 bg-card/90 text-center backdrop-blur-[2px] animate-in fade-in-0 duration-100">
          <FileUp className="h-6 w-6 text-accent" aria-hidden />
          <p className="text-sm font-medium text-foreground">{t("ide_side_panel.skills.drop_files_title")}</p>
          <p className="max-w-[16rem] text-xs text-muted-foreground">{t("ide_side_panel.skills.drop_files_body")}</p>
        </div>
      )}

      {editor && (
        <SkillEditor
          seed={editor}
          onClose={() => setEditor(null)}
          onSave={save}
          onPaste={paste}
          targetPane={target?.pane ?? null}
        />
      )}
    </div>
  );
}
