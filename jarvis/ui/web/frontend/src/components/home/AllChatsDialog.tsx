import { useEffect, useMemo, useRef, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { MessageSquare, Mic, Search, Trash2, X } from "lucide-react";

import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import {
  filterChatRows,
  formatChatWhen,
  groupChatRows,
  useChatRows,
  type ChatRow,
} from "@/components/home/chatRows";

/**
 * "All chats" — the whole history, not the handful the sidebar has room for.
 *
 * The sidebar block is a shortcut to what you touched last; this is the
 * archive: every voice session and every agent chat, searchable, grouped by
 * day like a mail client, opening on the same click path as the sidebar
 * (components/home/chatRows). Modelled on the recents dialog desktop chat
 * apps use — one field, one list, Escape closes — because a second full
 * SECTION for the same data would be one more place to keep in sync.
 *
 * The list is whatever the two pollers already hold, so opening the dialog
 * costs no request; the sidebar refreshes both lists every few seconds.
 */
export function AllChatsDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const t = useT();
  const { rows, isActive, open: openRow, remove } = useChatRows();
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState<"all" | "agent" | "voice">("all");
  const searchRef = useRef<HTMLInputElement | null>(null);

  // A reopened dialog starts clean — a stale filter hiding the chat someone
  // came looking for is the one failure this surface must not have.
  useEffect(() => {
    if (open) {
      setQuery("");
      setKind("all");
    }
  }, [open]);

  const visible = useMemo(() => {
    const byKind = kind === "all" ? rows : rows.filter((r) => r.kind === kind);
    return filterChatRows(byKind, query);
  }, [kind, query, rows]);
  const groups = useMemo(() => groupChatRows(visible), [visible]);

  const counts = useMemo(
    () => ({
      all: rows.length,
      agent: rows.filter((r) => r.kind === "agent").length,
      voice: rows.filter((r) => r.kind === "voice").length,
    }),
    [rows],
  );

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-[80] bg-[#090909]/70 backdrop-blur-sm data-[state=open]:animate-in data-[state=open]:fade-in-0 motion-reduce:animate-none" />
        <Dialog.Content
          data-testid="all-chats-dialog"
          onOpenAutoFocus={(e) => {
            e.preventDefault();
            searchRef.current?.focus();
          }}
          className={cn(
            "fixed left-1/2 top-1/2 z-[90] flex max-h-[min(84dvh,44rem)] w-[min(680px,calc(100vw-2rem))]",
            "-translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-2xl border border-border",
            "bg-card outline-none",
            "data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95 motion-reduce:animate-none",
          )}
        >
          <header className="flex items-start justify-between gap-4 border-b border-border px-5 py-4">
            <div className="min-w-0">
              <Dialog.Title className="font-display text-base font-semibold tracking-tight text-foreground">
                {t("all_chats.title")}
              </Dialog.Title>
              <Dialog.Description className="mt-0.5 text-xs leading-relaxed text-muted-foreground">
                {t("all_chats.description")}
              </Dialog.Description>
            </div>
            <Dialog.Close asChild>
              <button
                type="button"
                aria-label={t("all_chats.close")}
                className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
              >
                <X className="h-4 w-4" aria-hidden />
              </button>
            </Dialog.Close>
          </header>

          <div className="flex flex-col gap-3 border-b border-border px-5 py-3">
            <div className="relative">
              <Search
                aria-hidden
                className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground"
              />
              <input
                ref={searchRef}
                type="text"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={t("all_chats.search_placeholder")}
                aria-label={t("all_chats.search_placeholder")}
                data-testid="all-chats-search"
                className={cn(
                  "w-full rounded-xl border border-border bg-background/60 py-2 pl-9 pr-3 text-sm text-foreground",
                  "placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
                )}
              />
            </div>
            <div className="flex items-center gap-1.5">
              <FilterChip
                label={t("all_chats.filter_all")}
                count={counts.all}
                selected={kind === "all"}
                onClick={() => setKind("all")}
              />
              <FilterChip
                label={t("all_chats.filter_chats")}
                count={counts.agent}
                selected={kind === "agent"}
                onClick={() => setKind("agent")}
              />
              <FilterChip
                label={t("all_chats.filter_voice")}
                count={counts.voice}
                selected={kind === "voice"}
                onClick={() => setKind("voice")}
              />
            </div>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto px-3 py-3 scrollbar-jarvis">
            {visible.length === 0 ? (
              <p className="px-2 py-10 text-center text-xs text-muted-foreground">
                {rows.length === 0 ? t("all_chats.empty") : t("all_chats.no_match")}
              </p>
            ) : (
              groups.map(({ bucket, rows: group }) => (
                <section key={bucket} className="mb-3 last:mb-0">
                  <h3 className="px-2 pb-1 font-mono text-[10px] font-medium uppercase tracking-[0.12em] text-muted-foreground">
                    {t(`all_chats.group_${bucket}`)}
                  </h3>
                  <ul className="space-y-px">
                    {group.map((row) => (
                      <ArchiveRow
                        key={`${row.kind}-${row.id}`}
                        row={row}
                        active={isActive(row)}
                        onOpen={() => {
                          openRow(row);
                          onOpenChange(false);
                        }}
                        onDelete={row.kind === "agent" ? () => remove(row) : undefined}
                      />
                    ))}
                  </ul>
                </section>
              ))
            )}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function FilterChip({
  label,
  count,
  selected,
  onClick,
}: {
  label: string;
  count: number;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={selected}
      className={cn(
        "flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-[11px] font-medium transition-colors",
        "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
        selected
          ? "border-primary/40 bg-primary/10 text-primary"
          : "border-border text-muted-foreground hover:bg-background/60 hover:text-foreground",
      )}
    >
      {label}
      <span className={cn("font-mono tabular-nums", selected ? "text-primary/70" : "text-muted-foreground/70")}>
        {count}
      </span>
    </button>
  );
}

function ArchiveRow({
  row,
  active,
  onOpen,
  onDelete,
}: {
  row: ChatRow;
  active: boolean;
  onOpen: () => void;
  onDelete?: () => void;
}) {
  const t = useT();
  const isVoice = row.kind === "voice";
  const Icon = isVoice ? Mic : MessageSquare;
  const title = row.title || t("chats_view.new_chat");
  const preview = row.preview && row.preview !== title ? row.preview : "";
  return (
    <li className="group relative">
      <button
        type="button"
        onClick={onOpen}
        title={title}
        data-testid="all-chats-row"
        data-kind={row.kind}
        className={cn(
          "flex w-full items-start gap-2.5 rounded-xl px-2.5 py-2 text-left transition-colors",
          "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
          active ? "bg-background/80" : "hover:bg-background/50",
        )}
      >
        <Icon
          aria-hidden
          className={cn("mt-0.5 h-3.5 w-3.5 shrink-0", active ? "text-primary" : "text-muted-foreground")}
        />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-xs font-medium text-foreground">{title}</span>
          {preview && (
            <span className="mt-0.5 block truncate text-[11px] text-muted-foreground">{preview}</span>
          )}
        </span>
        <span className="mt-0.5 shrink-0 pr-6 font-mono text-[10px] tabular-nums text-muted-foreground">
          {formatChatWhen(row.updatedMs)}
        </span>
      </button>
      {onDelete && (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onDelete();
          }}
          title={t("chats_view.delete")}
          aria-label={t("chats_view.delete")}
          className="absolute right-1.5 top-2 hidden rounded-md bg-card p-1 text-muted-foreground transition-colors hover:bg-destructive/15 hover:text-destructive group-hover:block"
        >
          <Trash2 className="h-3 w-3" aria-hidden />
        </button>
      )}
    </li>
  );
}
