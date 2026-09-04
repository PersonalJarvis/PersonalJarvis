/**
 * The list over the society composer after "@" — agents, plugins, MCP
 * servers, CLIs, skills and Jarvis tools, grouped, with the service's mark.
 *
 * Portalled into the agent card (a Radix dialog) the way Combobox is, so a
 * click still lands: the dialog sets pointer-events none on body. Drawn
 * above the composer; the highlighted row follows the arrow keys the text
 * box receives.
 */
import { useEffect, useId, useLayoutEffect, useRef, useState, type RefObject } from "react";
import { createPortal } from "react-dom";

import { cn } from "@/lib/utils";
import { resolveToolBrand } from "@/lib/toolBrand";
import { useT } from "@/i18n";

import { AgentSwatch } from "../AgentSwatch";
import { groupMentions, type MentionGroup, type MentionItem } from "./mentionItems";

const PANEL_MAX_HEIGHT = 340;
const PANEL_MIN_WIDTH = 320;
const VIEWPORT_MARGIN = 8;

function panelHost(anchor: HTMLElement | null): HTMLElement {
  return anchor?.closest<HTMLElement>('[role="dialog"]') ?? document.body;
}

export function MentionPicker({
  anchorRef,
  open,
  items,
  loading,
  activeIndex,
  onHover,
  onPick,
}: {
  anchorRef: RefObject<HTMLElement | null>;
  open: boolean;
  items: MentionItem[];
  loading: boolean;
  activeIndex: number;
  onHover: (index: number) => void;
  onPick: (item: MentionItem) => void;
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
      const width = Math.min(
        Math.max(rect.width, PANEL_MIN_WIDTH),
        window.innerWidth - 2 * VIEWPORT_MARGIN,
      );
      const left = Math.min(
        Math.max(VIEWPORT_MARGIN, rect.left),
        Math.max(VIEWPORT_MARGIN, window.innerWidth - width - VIEWPORT_MARGIN),
      );
      setPosition({
        left,
        bottom: window.innerHeight - rect.top + 6,
        width,
        maxHeight: Math.max(140, Math.min(PANEL_MAX_HEIGHT, rect.top - VIEWPORT_MARGIN - 6)),
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

  useEffect(() => {
    if (!open) return;
    const row = listRef.current?.querySelector<HTMLElement>(`[data-index="${activeIndex}"]`);
    if (row && typeof row.scrollIntoView === "function") row.scrollIntoView({ block: "nearest" });
  }, [open, activeIndex]);

  if (!open || !position) return null;

  const runs = groupMentions(items);
  let runningIndex = 0;
  const host = panelHost(anchorRef.current);

  return createPortal(
    <div
      data-testid="mention-picker"
      data-combobox-panel=""
      role="listbox"
      id={listId}
      aria-label={t("society.chat.mention_hint")}
      ref={listRef}
      style={{
        left: position.left,
        bottom: position.bottom,
        width: position.width,
        maxHeight: position.maxHeight,
      }}
      onMouseDown={(ev) => ev.preventDefault()}
      className="pointer-events-auto fixed z-[70] flex flex-col overflow-y-auto rounded-xl border border-border-strong bg-popover p-1 text-sm shadow-float"
    >
      {items.length === 0 ? (
        <div className="px-3 py-2 text-xs text-muted-foreground" data-testid="mention-picker-empty">
          {loading ? t("society.chat.mention_loading") : t("society.chat.mention_empty")}
        </div>
      ) : (
        runs.map((run) => (
          <div key={run.group} role="group" aria-label={groupLabel(run.group, t)}>
            <div className="px-2 pb-0.5 pt-1.5 text-micro font-semibold text-muted-foreground">
              {groupLabel(run.group, t)}
            </div>
            {run.items.map((item) => {
              const index = runningIndex;
              runningIndex += 1;
              const active = index === activeIndex;
              return (
                <div
                  key={item.key}
                  role="option"
                  aria-selected={active}
                  data-index={index}
                  data-kind={item.kind}
                  data-testid="mention-picker-item"
                  onMouseEnter={() => onHover(index)}
                  onClick={() => onPick(item)}
                  className={cn(
                    "flex cursor-pointer items-center gap-2 rounded-lg px-2 py-1.5",
                    active ? "bg-secondary text-foreground" : "text-foreground",
                    !item.connected && "opacity-50",
                  )}
                >
                  <Mark item={item} />
                  <span className="min-w-0 flex-1">
                    <span className="flex items-baseline gap-1.5">
                      <span className="font-mono text-meta">
                        <span className="text-muted-foreground">@</span>
                        {item.value}
                      </span>
                      {item.label.toLowerCase() !== item.value.toLowerCase() ? (
                        <span className="truncate text-xs text-foreground">{item.label}</span>
                      ) : null}
                    </span>
                    {item.hint ? (
                      <span className="block truncate text-xs text-muted-foreground">{item.hint}</span>
                    ) : null}
                  </span>
                  {!item.connected ? (
                    <span className="shrink-0 text-micro text-muted-foreground">
                      {t("society.chat.mention_disconnected")}
                    </span>
                  ) : item.kind === "mcp" && !item.detail && item.pinIds.length > 1 ? (
                    <span className="shrink-0 font-mono text-micro text-muted-foreground">
                      {item.pinIds.length}
                    </span>
                  ) : null}
                </div>
              );
            })}
          </div>
        ))
      )}
    </div>,
    host,
  );
}

function groupLabel(group: MentionGroup, t: (key: string) => string): string {
  switch (group) {
    case "agents":
      return t("society.chat.mention_group_agents");
    case "plugins":
      return t("society.chat.mention_group_plugins");
    case "mcp":
      return t("society.chat.mention_group_mcp");
    case "cli":
      return t("society.chat.mention_group_cli");
    case "skills":
      return t("society.chat.mention_group_skills");
    case "tools":
      return t("society.chat.mention_group_tools");
    default:
      return group;
  }
}

function Mark({ item }: { item: MentionItem }) {
  if (item.agent) {
    return <AgentSwatch agent={item.agent} size={20} className="h-5 w-5" />;
  }
  const brand = resolveToolBrand(item.toolName || item.value);
  return (
    <span
      aria-hidden
      className="inline-flex h-5 w-5 shrink-0 items-center justify-center overflow-hidden rounded-[5px] bg-secondary"
    >
      {brand.logoUrl ? (
        <img src={brand.logoUrl} alt="" className="h-full w-full object-contain p-[2px]" draggable={false} />
      ) : (
        <span className="font-mono text-[10px] font-semibold uppercase text-muted-foreground">
          {brand.monogram}
        </span>
      )}
    </span>
  );
}
