import { memo, useEffect, useRef, useState, type DragEvent, type KeyboardEvent } from "react";
import {
  AlignLeft,
  Check,
  Copy,
  CopyPlus,
  CornerDownLeft,
  Download,
  Eye,
  GripVertical,
  Hash,
  MoreHorizontal,
  Pencil,
  Repeat2,
  Trash2,
} from "lucide-react";
import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";
import type { IdeSkill } from "@/lib/ideSkillsApi";
import { useIdeSkillsStore } from "@/store/ideSkills";
import { SKILL_DRAG_TYPE, loadSkillDrag } from "@/components/agentic/skillDrag";
import { SkillMenu, type SkillMenuItem } from "./SkillMenu";
import { SkillSeal, approxTokens, compactCount, lineCount, skillStyle } from "./skillVisuals";

/** How long a card shows where it just landed. */
const LANDED_MS = 2_600;

export type DropSide = "before" | "after" | null;

interface SkillCardProps {
  skill: IdeSkill;
  /** The pane "Paste into …" goes to; null when no pane is selected. */
  targetPane: string | null;
  /** Manual order: the card can be dragged within the list. */
  reorderable: boolean;
  /** Where a card carried over this one would land, for the insertion line. */
  dropSide: DropSide;
  onPaste: (skill: IdeSkill) => void;
  onOpen: (skill: IdeSkill, mode: "preview" | "edit") => void;
  onDuplicate: (skill: IdeSkill) => void;
  onDelete: (skill: IdeSkill) => void;
  onCopy: (skill: IdeSkill) => void;
  onExport: (skill: IdeSkill) => void;
  onReorderOver: (skill: IdeSkill, side: DropSide) => void;
  onReorderDrop: (skill: IdeSkill) => void;
}

/**
 * One skill in the library: a card the user picks up and drops on a terminal.
 *
 * The whole card is the handle. While it is carried it fades in place, the
 * cursor carries a compact chip of it (`ghost`), and every pane it passes
 * over announces it by name. After a landing the card says where it went.
 */
export const SkillCard = memo(function SkillCard({
  skill,
  targetPane,
  reorderable,
  dropSide,
  onPaste,
  onOpen,
  onDuplicate,
  onDelete,
  onCopy,
  onExport,
  onReorderOver,
  onReorderDrop,
}: SkillCardProps) {
  const t = useT();
  const setDragging = useIdeSkillsStore((state) => state.setDragging);
  const landing = useIdeSkillsStore((state) => (state.landing?.skillId === skill.id ? state.landing : null));
  const [carried, setCarried] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [landed, setLanded] = useState<string | null>(null);
  const ghost = useRef<HTMLDivElement>(null);
  const menuButton = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!landing || Date.now() - landing.at > LANDED_MS) return;
    setLanded(landing.pane);
    const timer = window.setTimeout(() => setLanded(null), LANDED_MS);
    return () => window.clearTimeout(timer);
  }, [landing]);

  const lines = lineCount(skill.content);
  const tokens = approxTokens(skill.content);

  const startDrag = (event: DragEvent<HTMLDivElement>) => {
    loadSkillDrag(event.dataTransfer, { id: skill.id, title: skill.title, content: skill.content, hue: skill.hue });
    if (ghost.current) event.dataTransfer.setDragImage(ghost.current, 22, 22);
    const multiline = skill.content.includes("\n");
    setDragging({ id: skill.id, title: skill.title, hue: skill.hue, icon: skill.icon, multiline });
    setCarried(true);
  };
  const endDrag = () => {
    setDragging(null);
    setCarried(false);
  };

  // A card carried over another card of the same list: an insertion line, then a move.
  const overCard = (event: DragEvent<HTMLDivElement>) => {
    if (!reorderable || !Array.from(event.dataTransfer.types).includes(SKILL_DRAG_TYPE)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    const box = event.currentTarget.getBoundingClientRect();
    onReorderOver(skill, event.clientY < box.top + box.height / 2 ? "before" : "after");
  };

  const onKey = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.target !== event.currentTarget) return;
    if (event.key === "Enter" && targetPane) {
      event.preventDefault();
      onPaste(skill);
    } else if (event.key === " ") {
      event.preventDefault();
      onOpen(skill, "preview");
    } else if (event.key === "F2" || event.key.toLowerCase() === "e") {
      event.preventDefault();
      onOpen(skill, "edit");
    }
  };

  const items: SkillMenuItem[] = [
    { id: "preview", label: t("ide_side_panel.skills.action_preview"), icon: Eye, onSelect: () => onOpen(skill, "preview"), hint: t("ide_side_panel.skills.key_space") },
    { id: "edit", label: t("ide_side_panel.skills.action_edit"), icon: Pencil, onSelect: () => onOpen(skill, "edit"), hint: "E" },
    { id: "duplicate", label: t("ide_side_panel.skills.action_duplicate"), icon: CopyPlus, onSelect: () => onDuplicate(skill) },
    { id: "copy", label: t("ide_side_panel.skills.action_copy"), icon: Copy, onSelect: () => onCopy(skill) },
    { id: "export", label: t("ide_side_panel.skills.action_export"), icon: Download, onSelect: () => onExport(skill) },
    confirmDelete
      ? { id: "delete-confirm", label: t("ide_side_panel.skills.action_delete_confirm"), icon: Trash2, tone: "danger", onSelect: () => onDelete(skill) }
      : { id: "delete", label: t("ide_side_panel.skills.action_delete"), icon: Trash2, tone: "danger", keepOpen: true, onSelect: () => setConfirmDelete(true) },
  ];

  return (
    <div
      role="listitem"
      className="relative"
      onDragOver={overCard}
      onDrop={(event) => {
        if (!reorderable || !Array.from(event.dataTransfer.types).includes(SKILL_DRAG_TYPE)) return;
        event.preventDefault();
        onReorderDrop(skill);
      }}
    >
      {dropSide && (
        <span
          aria-hidden
          className={cn(
            "pointer-events-none absolute inset-x-2 z-10 h-0.5 rounded-full bg-accent",
            dropSide === "before" ? "-top-[5px]" : "-bottom-[5px]",
          )}
        />
      )}
      <div
        data-testid={`skill-card-${skill.id}`}
        tabIndex={0}
        draggable
        onDragStart={startDrag}
        onDragEnd={endDrag}
        onKeyDown={onKey}
        onDoubleClick={() => onOpen(skill, "preview")}
        aria-label={fill(t("ide_side_panel.skills.card_aria"), { title: skill.title })}
        aria-describedby={`skill-${skill.id}-desc`}
        style={skillStyle(skill.hue)}
        className={cn(
          "group/skill relative flex cursor-grab select-none gap-3 overflow-hidden rounded-xl border bg-card/70 p-3 pl-2.5 outline-none",
          "transition-[border-color,box-shadow,opacity,transform] duration-200 ease-out motion-reduce:transition-none",
          "hover:border-[hsl(var(--skill)/0.45)]",
          "focus-visible:border-[hsl(var(--skill)/0.6)] focus-visible:ring-2 focus-visible:ring-ring",
          "active:cursor-grabbing",
          landed ? "border-[hsl(var(--skill)/0.7)] shadow-[0_0_0_3px_hsl(var(--skill)/0.18)]" : "border-border",
          carried && "scale-[0.98] opacity-40",
        )}
      >
        {/* The spine: a thin line of the hue down the leading edge. */}
        <span
          aria-hidden
          className="pointer-events-none absolute inset-y-3 left-0 w-[3px] rounded-r-full bg-[hsl(var(--skill))] opacity-0 transition-opacity duration-200 group-hover/skill:opacity-90 group-focus-visible/skill:opacity-90"
        />
        <GripVertical
          aria-hidden
          className="relative mt-2.5 h-4 w-4 shrink-0 text-muted-foreground/40 transition-colors group-hover/skill:text-muted-foreground"
        />
        <SkillSeal hue={skill.hue} icon={skill.icon} title={skill.title} description={skill.description} className="relative mt-0.5" />
        <div className="relative min-w-0 flex-1">
          {/* Only the title row leaves room for the two actions at the right. */}
          <button
            type="button"
            onClick={() => onOpen(skill, "preview")}
            className="block max-w-[calc(100%-56px)] truncate text-left text-sm font-semibold text-foreground-strong outline-none hover:underline hover:decoration-[hsl(var(--skill)/0.6)] hover:underline-offset-4"
          >
            {skill.title}
          </button>
          <p id={`skill-${skill.id}-desc`} className="mt-0.5 line-clamp-2 text-xs leading-5 text-muted-foreground">
            {skill.description || t("ide_side_panel.skills.no_description")}
          </p>
          <div className="mt-2 flex h-5 items-center gap-2.5 text-xs text-muted-foreground">
            {landed ? (
              <span data-testid={`skill-landed-${skill.id}`} className="flex min-w-0 items-center gap-1 font-medium text-[hsl(var(--skill))] animate-in fade-in-0 slide-in-from-bottom-1 duration-200">
                <Check className="h-3.5 w-3.5 shrink-0" aria-hidden />
                <span className="truncate">{fill(t("ide_side_panel.skills.landed"), { pane: landed })}</span>
              </span>
            ) : (
              <>
                <span className="flex items-center gap-1 tabular-nums" title={t("ide_side_panel.skills.meta_lines")}>
                  <AlignLeft className="h-3 w-3" aria-hidden />
                  {compactCount(lines)}
                </span>
                <span className="flex items-center gap-1 tabular-nums" title={t("ide_side_panel.skills.meta_tokens")}>
                  <Hash className="h-3 w-3" aria-hidden />
                  {fill(t("ide_side_panel.skills.tokens_short"), { n: compactCount(tokens) })}
                </span>
                {skill.use_count > 0 && (
                  <span className="flex items-center gap-1 tabular-nums" title={t("ide_side_panel.skills.meta_used")}>
                    <Repeat2 className="h-3 w-3" aria-hidden />
                    {fill(t("ide_side_panel.skills.used_short"), { n: compactCount(skill.use_count) })}
                  </span>
                )}
              </>
            )}
          </div>
        </div>
        {/* Actions sit in reserved room at the right, so showing them moves nothing. */}
        <div className="absolute right-2 top-2 flex items-center gap-0.5 opacity-0 transition-opacity duration-150 group-hover/skill:opacity-100 group-focus-within/skill:opacity-100 [@media(hover:none)]:opacity-100">
          <button
            type="button"
            data-testid={`skill-paste-${skill.id}`}
            disabled={!targetPane}
            onClick={() => onPaste(skill)}
            title={targetPane ? fill(t("ide_side_panel.skills.paste_into"), { pane: targetPane }) : t("ide_side_panel.skills.paste_no_target")}
            aria-label={targetPane ? fill(t("ide_side_panel.skills.paste_into"), { pane: targetPane }) : t("ide_side_panel.skills.paste_no_target")}
            className="inline-flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-[hsl(var(--skill)/0.14)] hover:text-[hsl(var(--skill))] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-40 disabled:hover:bg-transparent disabled:hover:text-muted-foreground"
          >
            <CornerDownLeft className="h-4 w-4" aria-hidden />
          </button>
          <button
            ref={menuButton}
            type="button"
            data-testid={`skill-more-${skill.id}`}
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            aria-label={fill(t("ide_side_panel.skills.more"), { title: skill.title })}
            title={t("ide_side_panel.skills.more_short")}
            onClick={() => { setConfirmDelete(false); setMenuOpen((value) => !value); }}
            className="inline-flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <MoreHorizontal className="h-4 w-4" aria-hidden />
          </button>
        </div>
      </div>
      <SkillMenu
        anchor={menuButton}
        open={menuOpen}
        onClose={() => { setMenuOpen(false); setConfirmDelete(false); }}
        items={items}
        label={fill(t("ide_side_panel.skills.more"), { title: skill.title })}
      />
      {/* The chip the cursor carries. Rendered off-screen (a drag image must be
          laid out to be captured), themed like everything else. */}
      <div aria-hidden className="pointer-events-none fixed -left-[9999px] -top-[9999px]">
        <div
          ref={ghost}
          style={skillStyle(skill.hue)}
          className="flex max-w-[260px] items-center gap-2.5 rounded-xl border border-[hsl(var(--skill)/0.45)] bg-popover py-2 pl-2 pr-3.5 text-foreground"
        >
          <SkillSeal hue={skill.hue} icon={skill.icon} title={skill.title} description={skill.description} size="sm" />
          <span className="min-w-0">
            <span className="block truncate text-sm font-semibold">{skill.title}</span>
            <span className="block text-xs text-muted-foreground">{fill(t("ide_side_panel.skills.ghost_meta"), { lines: compactCount(lines) })}</span>
          </span>
        </div>
      </div>
    </div>
  );
});
