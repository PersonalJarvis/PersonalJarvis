/** A pane-header button and on-demand browser for every prompt sent to that pane. */
import { useEffect, useMemo, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import {
  AlertCircle,
  Check,
  Copy,
  History,
  RefreshCw,
  X,
} from "lucide-react";

import { useT } from "@/i18n";
import {
  fetchPromptHistory,
  type PromptHistoryItem,
  type PromptHistoryResponse,
} from "@/lib/agenticIdeApi";
import { robustCopy } from "@/lib/clipboard";
import { cn } from "@/lib/utils";

type CopyState = "idle" | "copied" | "failed";

function fill(template: string, ...values: Array<string | number>): string {
  // Annotated because the accumulator is a string while the values are not:
  // without it the reduce is inferred over the ELEMENT type and the build fails
  // on `text.replace` not existing for a number.
  return values.reduce<string>(
    (text, value, index) => text.replace(`{${index}}`, String(value)),
    template,
  );
}

function whenLabel(at: number): string {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(at * 1000));
}

function firstLine(text: string): string {
  return text.split(/\r?\n/, 1)[0]?.replace(/^#+\s*/, "").trim() || text.trim();
}

export function PromptHistoryButton({
  terminal,
  workspaceId,
  count,
  light,
}: {
  terminal: string;
  workspaceId?: string;
  count: number;
  light: boolean;
}): JSX.Element {
  const t = useT();
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [history, setHistory] = useState<PromptHistoryResponse | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [copyState, setCopyState] = useState<CopyState>("idle");
  const [reloadToken, setReloadToken] = useState(0);

  useEffect(() => {
    if (!open) return;
    let current = true;
    setLoading(true);
    setError("");
    fetchPromptHistory(terminal, workspaceId)
      .then((result) => {
        if (!current) return;
        setHistory(result);
        setSelectedId((selected) =>
          selected && result.items.some((item) => item.id === selected)
            ? selected
            : (result.items[0]?.id ?? null),
        );
      })
      .catch((reason: unknown) => {
        if (!current) return;
        setError(reason instanceof Error ? reason.message : String(reason));
      })
      .finally(() => {
        if (current) setLoading(false);
      });
    // Renames and workspace switches can reuse this component while an older
    // request is still in flight. Ignore that stale answer instead of briefly
    // showing another pane's prompts.
    return () => {
      current = false;
    };
  }, [open, reloadToken, terminal, workspaceId]);

  useEffect(() => {
    if (copyState === "idle") return;
    const timer = window.setTimeout(() => setCopyState("idle"), 2_000);
    return () => window.clearTimeout(timer);
  }, [copyState]);

  const selected = useMemo(
    () => history?.items.find((item) => item.id === selectedId) ?? null,
    [history, selectedId],
  );

  const copySelected = async () => {
    if (!selected) return;
    setCopyState((await robustCopy(selected.text)) ? "copied" : "failed");
  };

  const triggerLabel = fill(t("agentic_grid.history.open"), terminal);

  return (
    <Dialog.Root
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) setCopyState("idle");
      }}
    >
      <Dialog.Trigger asChild>
        <button
          type="button"
          aria-label={triggerLabel}
          title={triggerLabel}
          data-testid={`pane-prompt-history-${terminal}`}
          onMouseDown={(event) => event.stopPropagation()}
          className={cn(
            "flex h-6 min-w-6 shrink-0 items-center justify-center gap-1 rounded-md px-1.5",
            "text-[10px] font-medium tabular-nums transition-colors active:translate-y-px",
            light
              ? "text-[#6b6b73] hover:bg-scrim/10 hover:text-[#2b2b33]"
              : "text-[#9a9aa5] hover:bg-sheen/10 hover:text-[#e8e8ec]",
          )}
        >
          <History className="h-3.5 w-3.5" aria-hidden="true" />
          {count > 0 && <span>{count}</span>}
        </button>
      </Dialog.Trigger>

      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-[80] bg-[#090909]/75 backdrop-blur-sm data-[state=open]:animate-in data-[state=open]:fade-in-0 motion-reduce:animate-none" />
        <Dialog.Content
          data-testid="prompt-history-dialog"
          className={cn(
            "fixed left-1/2 top-1/2 z-[90] flex max-h-[82dvh] w-[min(940px,calc(100vw-2rem))]",
            "-translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-2xl border border-border",
            "bg-card shadow-[0_28px_90px_-24px_rgba(0,0,0,0.75)] outline-none",
            "data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95 motion-reduce:animate-none",
          )}
        >
          <header className="flex items-start justify-between gap-4 border-b border-border px-5 py-4">
            <div className="min-w-0">
              <div className="mb-1 flex items-center gap-2">
                <History className="h-4 w-4 text-primary" aria-hidden="true" />
                <Dialog.Title className="font-display text-base font-semibold tracking-tight text-foreground">
                  {t("agentic_grid.history.title")}
                </Dialog.Title>
              </div>
              <Dialog.Description className="text-xs leading-relaxed text-muted-foreground">
                {fill(t("agentic_grid.history.description"), terminal)}
              </Dialog.Description>
            </div>
            <Dialog.Close asChild>
              <button
                type="button"
                aria-label={t("agentic_grid.history.close")}
                className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground active:translate-y-px"
              >
                <X className="h-4 w-4" aria-hidden="true" />
              </button>
            </Dialog.Close>
          </header>

          {loading ? (
            <HistorySkeleton />
          ) : error ? (
            <div className="flex min-h-64 flex-col items-center justify-center px-6 text-center">
              <AlertCircle className="mb-3 h-6 w-6 text-destructive" aria-hidden="true" />
              <p className="text-sm font-medium text-foreground">
                {t("agentic_grid.history.load_failed")}
              </p>
              <p className="mt-1 max-w-md text-xs text-muted-foreground" role="alert">
                {error}
              </p>
              <button
                type="button"
                onClick={() => setReloadToken((token) => token + 1)}
                className="mt-4 inline-flex h-9 items-center gap-2 rounded-lg border border-border px-3 text-xs font-medium text-foreground transition-colors hover:bg-muted active:translate-y-px"
              >
                <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
                {t("agentic_grid.history.retry")}
              </button>
            </div>
          ) : !history || history.items.length === 0 ? (
            <div className="flex min-h-64 flex-col items-center justify-center px-6 text-center">
              <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-2xl border border-border bg-muted/30">
                <History className="h-5 w-5 text-muted-foreground" aria-hidden="true" />
              </div>
              <p className="text-sm font-medium text-foreground">
                {t("agentic_grid.history.empty_title")}
              </p>
              <p className="mt-1 max-w-sm text-xs leading-relaxed text-muted-foreground">
                {t("agentic_grid.history.empty_detail")}
              </p>
            </div>
          ) : (
            <div className="grid min-h-0 flex-1 grid-cols-1 md:grid-cols-[minmax(15rem,0.8fr)_minmax(0,1.4fr)]">
              <aside className="min-h-0 border-b border-border md:border-b-0 md:border-r">
                <div className="flex items-center justify-between border-b border-border/60 px-4 py-2.5">
                  <span className="text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
                    {fill(
                      t(
                        history.available === 1
                          ? "agentic_grid.history.count_one"
                          : "agentic_grid.history.count_many",
                      ),
                      history.available,
                    )}
                  </span>
                  {!history.complete && (
                    <span className="text-[10px] text-amber-500">
                      {t("agentic_grid.history.partial")}
                    </span>
                  )}
                </div>
                <div className="max-h-52 overflow-y-auto md:max-h-none md:h-[min(60dvh,34rem)]">
                  {history.items.map((item) => (
                    <HistoryRow
                      key={item.id}
                      item={item}
                      selected={item.id === selectedId}
                      onSelect={() => {
                        setSelectedId(item.id);
                        setCopyState("idle");
                      }}
                    />
                  ))}
                </div>
              </aside>

              {selected && (
                <section className="flex min-h-0 flex-col" data-testid="prompt-history-detail">
                  <div className="flex items-center justify-between gap-3 border-b border-border/60 px-4 py-2.5">
                    <div className="min-w-0">
                      <div className="truncate text-xs font-medium text-foreground">
                        {fill(t("agentic_grid.history.prompt_number"), selected.sequence)}
                      </div>
                      <div className="mt-0.5 text-[10px] text-muted-foreground">
                        {whenLabel(selected.at)} · {fill(t("agentic_grid.history.characters"), selected.chars)}
                      </div>
                    </div>
                    <button
                      type="button"
                      onClick={() => void copySelected()}
                      data-testid="prompt-history-copy"
                      className={cn(
                        "inline-flex h-8 shrink-0 items-center gap-1.5 rounded-lg border px-2.5 text-xs font-medium",
                        "transition-colors active:translate-y-px",
                        copyState === "failed"
                          ? "border-destructive/40 text-destructive"
                          : copyState === "copied"
                            ? "border-emerald-500/30 text-emerald-500"
                            : "border-border text-foreground hover:border-primary/40 hover:bg-primary/10",
                      )}
                    >
                      {copyState === "copied" ? (
                        <Check className="h-3.5 w-3.5" aria-hidden="true" />
                      ) : (
                        <Copy className="h-3.5 w-3.5" aria-hidden="true" />
                      )}
                      {t(
                        copyState === "copied"
                          ? "agentic_grid.history.copied"
                          : copyState === "failed"
                            ? "agentic_grid.history.copy_failed"
                            : "agentic_grid.history.copy",
                      )}
                    </button>
                  </div>
                  <pre className="min-h-0 flex-1 overflow-auto whitespace-pre-wrap break-words bg-background/35 p-4 font-mono text-[11px] leading-relaxed text-foreground/90">
                    {selected.text}
                  </pre>
                </section>
              )}
            </div>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function HistoryRow({
  item,
  selected,
  onSelect,
}: {
  item: PromptHistoryItem;
  selected: boolean;
  onSelect: () => void;
}): JSX.Element {
  const t = useT();
  const statusKey =
    item.submitted === true
      ? "agentic_grid.history.sent"
      : item.submitted === false
        ? "agentic_grid.history.waiting"
        : "agentic_grid.history.unconfirmed";
  return (
    <button
      type="button"
      onClick={onSelect}
      data-testid={`prompt-history-row-${item.id}`}
      className={cn(
        "group flex w-full items-start gap-3 border-b border-border/50 px-4 py-3 text-left transition-colors",
        selected ? "bg-primary/10" : "hover:bg-muted/40",
      )}
    >
      <span
        className={cn(
          "mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full",
          item.submitted === true
            ? "bg-emerald-500"
            : item.submitted === false
              ? "bg-amber-500"
              : "bg-muted-foreground",
        )}
        aria-hidden="true"
      />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-xs font-medium text-foreground">
          {firstLine(item.text)}
        </span>
        <span className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[10px] text-muted-foreground">
          <span>{whenLabel(item.at)}</span>
          <span>{t(statusKey)}</span>
        </span>
      </span>
    </button>
  );
}

function HistorySkeleton(): JSX.Element {
  return (
    <div className="grid min-h-64 grid-cols-1 md:grid-cols-[minmax(15rem,0.8fr)_minmax(0,1.4fr)]">
      <div className="border-b border-border p-4 md:border-b-0 md:border-r">
        {[0, 1, 2, 3].map((row) => (
          <div key={row} className="border-b border-border/50 py-3">
            <div className="h-3 w-4/5 animate-pulse rounded bg-muted" />
            <div className="mt-2 h-2 w-1/2 animate-pulse rounded bg-muted/70" />
          </div>
        ))}
      </div>
      <div className="p-4">
        <div className="h-3 w-1/3 animate-pulse rounded bg-muted" />
        <div className="mt-6 space-y-3">
          {["w-full", "w-11/12", "w-4/5", "w-5/6", "w-2/3"].map((width, row) => (
            <div key={row} className={cn("h-2.5 animate-pulse rounded bg-muted/70", width)} />
          ))}
        </div>
      </div>
    </div>
  );
}
