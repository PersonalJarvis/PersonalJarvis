import { useEffect, useLayoutEffect, useRef, useState, type RefObject } from "react";
import { createPortal } from "react-dom";
import { Check, FolderOpen, Loader2, Paperclip, Plus, Search, X } from "lucide-react";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { BrandedSelect } from "@/components/ui/select";
import { ToolChoiceIcon } from "./ToolChoiceChips";
import { CATEGORY_ICONS, toolIdentityStyle } from "./toolIdentity";
import {
  searchTools,
  TOOL_CATEGORIES,
  type ToolChoice,
  type ToolSearchResult,
} from "./toolChoices";

export function ComposerAddMenu({
  anchorRef,
  provider,
  model,
  cwd,
  stance,
  selected,
  onChange,
  onAttach,
  onFolder,
  onConnect,
  disabled,
}: {
  anchorRef: RefObject<HTMLElement | null>;
  provider: string;
  model: string;
  cwd: string;
  stance: string;
  selected: ToolChoice[];
  onChange: (items: ToolChoice[]) => void;
  onAttach: () => void;
  onFolder: () => void;
  onConnect: (row: ToolChoice) => void;
  disabled: boolean;
}) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [result, setResult] = useState<ToolSearchResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [retry, setRetry] = useState(0);
  const panel = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const [position, setPosition] = useState({
    left: 8,
    bottom: 8,
    width: 520,
    maxHeight: 480,
  });

  function close() {
    setOpen(false);
    button.current?.focus();
  }

  useLayoutEffect(() => {
    if (!open) return;
    const measure = () => {
      const rect = anchorRef.current?.getBoundingClientRect();
      if (!rect) return;
      const width = Math.min(560, window.innerWidth - 16);
      const height = Math.min(520, window.innerHeight - 24);
      const above = rect.top > 260;
      setPosition({
        left: Math.max(8, Math.min(rect.left, window.innerWidth - width - 8)),
        bottom: above ? window.innerHeight - rect.top + 8 : 12,
        width,
        maxHeight: above ? Math.min(height, rect.top - 16) : height,
      });
    };
    measure();
    input.current?.focus();
    window.addEventListener("resize", measure);
    window.addEventListener("scroll", measure, true);
    return () => {
      window.removeEventListener("resize", measure);
      window.removeEventListener("scroll", measure, true);
    };
  }, [open, anchorRef]);

  useEffect(() => {
    if (!open) return;
    const outside = (ev: PointerEvent) => {
      if (
        !panel.current?.contains(ev.target as Node) &&
        !button.current?.contains(ev.target as Node)
      )
        setOpen(false);
    };
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setLoading(true);
    setFailed(false);
    setResult(null);
    const timer = setTimeout(
      () => {
        void searchTools({ provider, model, cwd, stance, q: query, category }, controller.signal)
          .then((data) => {
            if (!controller.signal.aborted) setResult(data);
          })
          .catch(() => {
            if (!controller.signal.aborted) setFailed(true);
          })
          .finally(() => {
            if (!controller.signal.aborted) setLoading(false);
          });
      },
      query ? 600 : 0,
    );
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [open, provider, model, cwd, stance, query, category, retry]);

  const groups = new Map<string, ToolChoice[]>();
  const operationGroups = new Set(
    (result?.items ?? [])
      .filter((row) => row.id.startsWith("tool:") && ["plugins", "mcp"].includes(row.category))
      .map((row) => `${row.category}:${row.group}`),
  );
  for (const row of result?.items ?? []) {
    const subgroup = `${row.category}:${row.group}`;
    const key = operationGroups.has(subgroup) ? subgroup : row.category;
    groups.set(key, [...(groups.get(key) ?? []), row]);
  }

  return (
    <>
      <button
        ref={button}
        type="button"
        data-testid="composer-add"
        disabled={disabled}
        aria-expanded={open}
        aria-haspopup="dialog"
        aria-label={t("chat_tools.add")}
        onClick={() => setOpen(!open)}
        className="inline-flex h-8 items-center gap-1.5 rounded-lg px-2 text-xs font-medium text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
      >
        <Plus className="h-4 w-4" />
        {t("chat_tools.add")}
      </button>
      {open &&
        createPortal(
          <div
            ref={panel}
            role="dialog"
            aria-label={t("chat_tools.add")}
            style={position}
            data-testid="composer-add-menu"
            className="fixed z-[75] flex flex-col overflow-hidden rounded-2xl border border-border-strong bg-popover text-foreground shadow-xl"
            onKeyDown={(ev) => {
              if (
                ev.defaultPrevented ||
                (ev.target as HTMLElement).closest('[role="listbox"], [role="combobox"]')
              )
                return;
              if (ev.key === "Escape") {
                ev.preventDefault();
                ev.stopPropagation();
                close();
              }
              if (ev.key === "Tab") {
                const nodes = panel.current?.querySelectorAll<HTMLElement>(
                  "button:not(:disabled), input, select",
                );
                if (!nodes?.length) return;
                const first = nodes[0],
                  last = nodes[nodes.length - 1];
                if (ev.shiftKey && document.activeElement === first) {
                  ev.preventDefault();
                  last.focus();
                }
                if (!ev.shiftKey && document.activeElement === last) {
                  ev.preventDefault();
                  first.focus();
                }
              }
              if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
                if (ev.target instanceof HTMLSelectElement) return;
                const nodes = Array.from(
                  panel.current?.querySelectorAll<HTMLButtonElement>(
                    "[data-tool-row]:not(:disabled)",
                  ) ?? [],
                );
                if (!nodes.length) return;
                ev.preventDefault();
                const index = nodes.indexOf(document.activeElement as HTMLButtonElement);
                nodes[
                  index < 0
                    ? ev.key === "ArrowDown"
                      ? 0
                      : nodes.length - 1
                    : (index + (ev.key === "ArrowDown" ? 1 : nodes.length - 1) + nodes.length) %
                      nodes.length
                ]?.focus();
              }
            }}
          >
            <div className="mx-3 mb-1 mt-3 flex items-center gap-2 rounded-xl border border-border bg-background/50 px-3 py-2 focus-within:border-border-strong">
              <Search className="h-4 w-4 text-muted-foreground" />
              <input
                ref={input}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                maxLength={200}
                aria-label={t("chat_tools.search")}
                placeholder={t("chat_tools.search")}
                className="min-w-0 flex-1 bg-transparent py-0.5 text-sm outline-none placeholder:text-muted-foreground"
              />
              <button
                type="button"
                onClick={close}
                aria-label={t("chat_tools.close")}
                className="rounded p-1 hover:bg-secondary"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="flex items-center gap-2 border-b border-border px-3 py-2">
              <BrandedSelect
                ariaLabel={t("chat_tools.filter")}
                value={category}
                onValueChange={setCategory}
                options={[
                  { value: "", label: t("chat_tools.all") },
                  ...TOOL_CATEGORIES.map((c) => {
                    const Icon = CATEGORY_ICONS[c];
                    return {
                      value: c,
                      label: t(`chat_tools.${c}`),
                      icon: <Icon className="h-3.5 w-3.5" aria-hidden />,
                    };
                  }),
                ]}
                className="w-auto min-w-0 max-w-[60%] shrink-0 rounded-lg border border-border-strong bg-popover px-2 py-1 text-xs"
              />
              <span
                className="min-w-0 flex-1 truncate text-right text-xs text-muted-foreground"
                role="status"
                title={result ? t(`chat_tools.${result.mode}`) : undefined}
              >
                {loading ? (
                  <Loader2
                    aria-label={t("chat_tools.loading")}
                    className="ml-auto h-4 w-4 animate-spin"
                  />
                ) : result ? (
                  `${result.items.length} · ${t(`chat_tools.${result.mode}`)}`
                ) : (
                  ""
                )}
              </span>
            </div>
            <div className="min-h-0 overflow-y-auto p-1.5 scrollbar-jarvis">
              {!query && !category && (
                <div className="mb-2 border-b border-border pb-2">
                  <button
                    type="button"
                    onClick={() => {
                      close();
                      onAttach();
                    }}
                    className="flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm hover:bg-secondary"
                  >
                    <Paperclip className="h-5 w-5" />
                    {t("chat_tools.attach")}
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      close();
                      onFolder();
                    }}
                    className="flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm hover:bg-secondary"
                  >
                    <FolderOpen className="h-5 w-5" />
                    {t("chat_tools.folder")}
                  </button>
                </div>
              )}
              {failed && (
                <div className="p-3 text-sm" role="alert">
                  {t("chat_tools.error")}{" "}
                  <button type="button" onClick={() => setRetry(retry + 1)} className="underline">
                    {t("chat_tools.retry")}
                  </button>
                </div>
              )}
              {!loading && !failed && result?.items.length === 0 && (
                <p className="p-3 text-sm text-muted-foreground">{t("chat_tools.empty")}</p>
              )}
              {[...groups].map(([key, rows]) => (
                <div key={key}>
                  <div className="px-3 pb-1 pt-3 text-xs font-medium tracking-wide text-muted-foreground">
                    {t(`chat_tools.${rows[0].category}`)}
                    {operationGroups.has(key) ? ` · ${rows[0].group}` : ""}
                  </div>
                  {rows.map((row) => {
                    const picked = selected.some((s) => s.id === row.id);
                    return (
                      <button
                        key={row.id}
                        type="button"
                        data-tool-row
                        aria-pressed={picked}
                        style={toolIdentityStyle(row)}
                        disabled={row.available && !picked && selected.length >= 24}
                        onClick={() => {
                          if (!row.available) {
                            close();
                            onConnect(row);
                            return;
                          }
                          onChange(
                            picked ? selected.filter((s) => s.id !== row.id) : [...selected, row],
                          );
                        }}
                        className={cn(
                          "tool-identity tool-picker-row flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-current disabled:opacity-50",
                          !row.available && "opacity-60",
                        )}
                      >
                        <ToolChoiceIcon row={row} size={22} />
                        <span className="min-w-0 flex-1">
                          <span className="tool-picker-name block truncate text-sm font-medium">
                            {row.label}
                          </span>
                          <span
                            className="block truncate text-xs text-muted-foreground"
                            title={row.description}
                          >
                            {row.description}
                          </span>
                        </span>
                        {picked ? (
                          <Check className="h-4 w-4 shrink-0" />
                        ) : !row.available ? (
                          <span className="shrink-0 text-xs">{t("chat_tools.connect")}</span>
                        ) : (
                          <Plus className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                        )}
                      </button>
                    );
                  })}
                </div>
              ))}
            </div>
            <div className="flex items-center gap-3 border-t border-border px-3 py-2 text-xs text-muted-foreground">
              <span className="flex-1">
                {selected.length
                  ? `${selected.length} · ${t("chat_tools.next_message")}`
                  : t("chat_tools.hint")}
              </span>
              <button
                type="button"
                onClick={close}
                className="rounded-lg bg-secondary px-3 py-1.5 text-foreground hover:bg-muted"
              >
                {t("chat_tools.done")}
              </button>
            </div>
          </div>,
          document.body,
        )}
    </>
  );
}
