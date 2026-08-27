import { useEffect, useId, useLayoutEffect, useRef, useState, type RefObject } from "react";
import { createPortal } from "react-dom";
import { Bot, FileText, Folder, Sparkles, TerminalSquare } from "lucide-react";

import { groupRuns, type TypeaheadItem } from "@/components/agentchat/typeahead";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

const PANEL_MAX_HEIGHT = 320;
const VIEWPORT_MARGIN = 8;

/**
 * The list over the composer — what `/`, `@` or `$` offers, one row per
 * item, grouped by where it came from (this folder, the account, a plugin,
 * Jarvis' registry, the subagents, the files).
 *
 * Drawn above the card, portalled and fixed like the Combobox panel, so the
 * page's scroll columns cannot clip it. The highlighted row follows the
 * arrow keys the text box receives (`useComposerTypeahead`); the mouse
 * hovers to move it and clicks to pick. `onMouseDown` prevents default so a
 * click never takes focus from the text box — the caret is the whole state.
 */
export function ComposerTypeahead({
  anchorRef,
  open,
  trigger,
  items,
  loading,
  activeIndex,
  onHover,
  onPick,
}: {
  anchorRef: RefObject<HTMLElement | null>;
  open: boolean;
  trigger: string | null;
  items: TypeaheadItem[];
  loading: boolean;
  activeIndex: number;
  onHover: (index: number) => void;
  onPick: (item: TypeaheadItem) => void;
}) {
  const t = useT();
  const listId = useId();
  const listRef = useRef<HTMLDivElement | null>(null);
  const [position, setPosition] = useState<{
    left: number;
    bottom: number;
    width: number;
    maxHeight: number;
  } | null>(null);

  useLayoutEffect(() => {
    if (!open) return;
    const measure = () => {
      const anchor = anchorRef.current;
      if (!anchor) return;
      const rect = anchor.getBoundingClientRect();
      setPosition({
        left: rect.left,
        bottom: window.innerHeight - rect.top + 6,
        width: rect.width,
        maxHeight: Math.max(120, Math.min(PANEL_MAX_HEIGHT, rect.top - VIEWPORT_MARGIN - 6)),
      });
    };
    measure();
    window.addEventListener("scroll", measure, true);
    window.addEventListener("resize", measure);
    return () => {
      window.removeEventListener("scroll", measure, true);
      window.removeEventListener("resize", measure);
    };
  }, [open, anchorRef, items.length]);

  // The highlighted row stays in view as the arrows move it.
  useEffect(() => {
    if (!open) return;
    const row = listRef.current?.querySelector<HTMLElement>(`[data-index="${activeIndex}"]`);
    // jsdom draws no layout and has no scrollIntoView; the browser does.
    if (row && typeof row.scrollIntoView === "function") row.scrollIntoView({ block: "nearest" });
  }, [open, activeIndex]);

  if (!open || !position || !trigger) return null;

  const runs = groupRuns(items);
  let runningIndex = 0;

  return createPortal(
    <div
      data-testid="composer-typeahead"
      data-trigger={trigger}
      role="listbox"
      id={listId}
      aria-label={t("agent_chat.typeahead_label")}
      ref={listRef}
      style={{
        left: position.left,
        bottom: position.bottom,
        width: position.width,
        maxHeight: position.maxHeight,
      }}
      onMouseDown={(ev) => ev.preventDefault()}
      className="fixed z-[70] flex flex-col overflow-y-auto rounded-xl border border-primary/25 bg-popover/95 p-1 text-sm shadow-[0_22px_55px_-16px_rgb(var(--scrim-rgb)/0.7),inset_0_1px_0_hsl(var(--foreground)/0.05)] backdrop-blur-xl"
    >
      {items.length === 0 ? (
        <div className="px-3 py-2 text-xs text-muted-foreground" data-testid="composer-typeahead-empty">
          {loading ? t("agent_chat.typeahead_loading") : t("agent_chat.typeahead_empty")}
        </div>
      ) : (
        runs.map((run) => (
          <div key={`${run.group}-${runningIndex}`} role="group" aria-label={groupLabel(run.group, t)}>
            <div className="px-2 pb-0.5 pt-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
              {groupLabel(run.group, t)}
            </div>
            {run.items.map((item) => {
              const index = runningIndex;
              runningIndex += 1;
              const active = index === activeIndex;
              const Icon = kindIcon(item.kind);
              return (
                <div
                  key={`${item.group}:${item.value}`}
                  role="option"
                  aria-selected={active}
                  data-index={index}
                  data-testid="composer-typeahead-item"
                  onMouseEnter={() => onHover(index)}
                  onClick={() => onPick(item)}
                  className={cn(
                    "flex cursor-pointer items-center gap-2 rounded-lg px-2 py-1.5",
                    active ? "bg-primary/15 text-foreground" : "text-foreground/90",
                  )}
                >
                  <Icon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
                  <span className="shrink-0 font-mono text-[13px]">
                    <span className="text-muted-foreground">{trigger}</span>
                    {item.value}
                  </span>
                  {item.hint && (
                    <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">{item.hint}</span>
                  )}
                </div>
              );
            })}
          </div>
        ))
      )}
    </div>,
    document.body,
  );
}

function groupLabel(group: string, t: (key: string) => string): string {
  switch (group) {
    case "project":
      return t("agent_chat.typeahead_group_project");
    case "account":
      return t("agent_chat.typeahead_group_account");
    case "plugins":
      return t("agent_chat.typeahead_group_plugins");
    case "jarvis":
      return t("agent_chat.typeahead_group_jarvis");
    case "agents":
      return t("agent_chat.typeahead_group_agents");
    case "files":
      return t("agent_chat.typeahead_group_files");
    default:
      return group;
  }
}

function kindIcon(kind: string) {
  switch (kind) {
    case "skill":
      return Sparkles;
    case "command":
      return TerminalSquare;
    case "agent":
      return Bot;
    case "folder":
      return Folder;
    default:
      return FileText;
  }
}
