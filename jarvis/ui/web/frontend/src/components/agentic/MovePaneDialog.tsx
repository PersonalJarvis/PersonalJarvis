import { useEffect, useState, type ComponentType } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { ArrowDown, ArrowLeft, ArrowRight, ArrowUp, FolderInput, Loader2, Wand2 } from "lucide-react";
import { AgentMark } from "./AgentMark";
import { treeLayout } from "./treeLayout";
import { GRID_LIMIT_HINT, fitsWorkspace, previewSplit, workspaceLayout } from "./workspaceDocking";
import { fetchWorkspaceLayout, type TransferPlacement, type TransferSide, type WorkspaceLayoutView } from "@/lib/agenticIdeApi";
import { cn } from "@/lib/utils";

/** The pane being moved and the open workspace it goes to. */
export interface MovePaneRequest {
  pane: { name: string; agent: string; displayName: string };
  target: { id: string; name: string };
}

interface Props {
  request: MovePaneRequest | null;
  busy: boolean;
  onCancel: () => void;
  /** `null` places the pane automatically at the right edge of the grid. */
  onConfirm: (placement: TransferPlacement | null) => void;
}

const SIDES: { side: TransferSide; words: string; Icon: ComponentType<{ className?: string }>; at: string }[] = [
  { side: "left", words: "left of", Icon: ArrowLeft, at: "left-1 top-1/2 -translate-y-1/2" },
  { side: "right", words: "right of", Icon: ArrowRight, at: "right-1 top-1/2 -translate-y-1/2" },
  { side: "above", words: "above", Icon: ArrowUp, at: "left-1/2 top-1 -translate-x-1/2" },
  { side: "below", words: "below", Icon: ArrowDown, at: "bottom-1 left-1/2 -translate-x-1/2" },
];

/** The half of the anchor's tile the moved pane will take. */
const HALF: Record<TransferSide, string> = {
  left: "inset-y-0 left-0 w-1/2",
  right: "inset-y-0 right-0 w-1/2",
  above: "inset-x-0 top-0 h-1/2",
  below: "inset-x-0 bottom-0 h-1/2",
};

const SPLIT_DIRECTION = { left: "left", right: "right", above: "above", below: "down" } as const;
const INCOMING = "__incoming__";

/**
 * Choose where a pane lands in another workspace's grid.
 *
 * The target grid is drawn as a small map. Each of its panes offers four
 * arrows; picking one puts the moved pane on that side of it, sharing that
 * pane's room the way a split does. A side that would push the grid past its
 * largest shape is disabled rather than offered and then refused. "Automatic"
 * — the default, so Enter alone keeps the quick path — joins the right edge.
 */
export function MovePaneDialog({ request, busy, onCancel, onConfirm }: Props) {
  const [view, setView] = useState<WorkspaceLayoutView | null>(null);
  const [loadError, setLoadError] = useState("");
  const [choice, setChoice] = useState<TransferPlacement | null>(null);
  const [hover, setHover] = useState<TransferPlacement | null>(null);
  const targetId = request?.target.id;

  useEffect(() => {
    setView(null);
    setLoadError("");
    setChoice(null);
    setHover(null);
    if (!targetId) return;
    let live = true;
    fetchWorkspaceLayout(targetId)
      .then((next) => { if (live) setView(next); })
      .catch((error: unknown) => { if (live) setLoadError(error instanceof Error ? error.message : String(error)); });
    return () => { live = false; };
  }, [targetId]);

  const terminals = view?.terminals ?? [];
  const current = view ? workspaceLayout(view.layout, terminals) : null;
  const boxes = treeLayout(current, terminals).boxes;
  const full = view !== null && terminals.length >= view.max_terminals;
  const anchorOf = (terminal: (typeof terminals)[number]) => terminal.history_id ? `pane:${terminal.history_id}` : terminal.name;
  const fits = (key: string, side: TransferSide) =>
    !full && current !== null && fitsWorkspace(previewSplit(current, key, INCOMING, SPLIT_DIRECTION[side]));
  const shown = hover ?? choice;
  const chosen = choice ? terminals.find((terminal) => anchorOf(terminal) === choice.anchor) : null;
  const paneName = request?.pane.name ?? "";
  const summary = full
    ? `${request?.target.name} is full: a workspace holds ${view?.max_terminals} terminals.`
    : chosen && choice
      ? `${paneName} goes ${SIDES.find((entry) => entry.side === choice.side)?.words} ${chosen.name} and shares its space.`
      : terminals.length
        ? `${paneName} joins the right edge, and every pane gets an even share.`
        : `${request?.target.name} is empty, so ${paneName} fills it.`;
  const submit = () => { if (!busy && !full && view) onConfirm(choice); };

  return <Dialog.Root open={request !== null} onOpenChange={(open) => { if (!open && !busy) onCancel(); }}>
    <Dialog.Portal>
      <Dialog.Overlay className="fixed inset-0 z-[80] bg-background/70 backdrop-blur-sm data-[state=open]:animate-in data-[state=open]:fade-in-0" />
      <Dialog.Content data-testid="move-pane-dialog"
        className="fixed left-1/2 top-1/2 z-[90] w-[min(560px,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 rounded-2xl border border-border bg-popover p-6 text-popover-foreground shadow-2xl data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95">
        <form onSubmit={(event) => { event.preventDefault(); submit(); }}>
          <div className="flex items-start gap-4">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary">
              <FolderInput className="h-[18px] w-[18px]" aria-hidden />
            </div>
            <div className="min-w-0 flex-1">
              <Dialog.Title className="truncate text-base font-semibold">Move {paneName} to {request?.target.name}</Dialog.Title>
              <Dialog.Description className="mt-1.5 text-sm leading-relaxed text-muted-foreground">
                Pick where it goes in that grid. The agent keeps running.
              </Dialog.Description>
            </div>
          </div>

          <button type="button" role="radio" aria-checked={choice === null} data-testid="move-pane-auto"
            disabled={busy} onClick={() => setChoice(null)}
            className={cn(
              "mt-4 flex w-full items-center gap-3 rounded-xl border px-3 py-2.5 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50",
              choice === null ? "border-primary bg-primary/5" : "border-border hover:bg-muted/60",
            )}>
            <Wand2 className={cn("h-4 w-4 shrink-0", choice === null ? "text-primary" : "text-muted-foreground")} aria-hidden />
            <span className="min-w-0">
              <span className="block text-sm font-medium">Automatic</span>
              <span className="block text-xs text-muted-foreground">At the right edge, every pane evened out</span>
            </span>
          </button>

          <div className="mt-3 rounded-xl border border-border bg-muted/30 p-1.5">
            <div data-testid="move-pane-map" className="relative aspect-[16/9] w-full" onMouseLeave={() => setHover(null)}>
              {!view && !loadError && <div className="flex h-full items-center justify-center gap-2 text-xs text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />Loading {request?.target.name}…</div>}
              {view && terminals.length === 0 && <div className="flex h-full items-center justify-center text-xs text-muted-foreground">
                No terminals open here yet.</div>}
              {terminals.map((terminal, index) => {
                const box = boxes[index];
                if (!box) return null;
                const anchor = anchorOf(terminal);
                const lit = shown?.anchor === anchor ? shown.side : null;
                return <div key={terminal.key} data-testid={`move-pane-tile-${terminal.name}`}
                  className="absolute p-0.5" style={{ left: `${box.x * 100}%`, top: `${box.y * 100}%`, width: `${box.w * 100}%`, height: `${box.h * 100}%` }}>
                  <div className="relative flex h-full w-full items-center justify-center overflow-hidden rounded-lg border border-border bg-card">
                    {lit && <div aria-hidden className={cn("absolute flex items-center justify-center rounded-md border-2 border-primary bg-primary/15 text-xs font-semibold text-primary", HALF[lit])}>
                      {paneName}</div>}
                    <span className={cn("flex min-w-0 items-center gap-1.5 px-7 text-xs font-medium", lit && "invisible")}>
                      <AgentMark agent={terminal.agent} label={terminal.display_name} variant="plain" size="sm" />
                      <span className="truncate">{terminal.name}</span>
                    </span>
                    {SIDES.map(({ side, words, Icon, at }) => {
                      const allowed = fits(terminal.key, side);
                      const selected = choice?.anchor === anchor && choice.side === side;
                      return <button key={side} type="button" aria-pressed={selected} disabled={busy || !allowed}
                        data-testid={`move-pane-${terminal.name}-${side}`}
                        aria-label={`Place ${paneName} ${words} ${terminal.name}`}
                        title={allowed ? `${words[0].toUpperCase()}${words.slice(1)} ${terminal.name}` : `No room here. ${GRID_LIMIT_HINT}`}
                        onClick={() => setChoice({ anchor, side })}
                        onMouseEnter={() => { if (allowed) setHover({ anchor, side }); }}
                        onFocus={() => { if (allowed) setHover({ anchor, side }); }}
                        onBlur={() => setHover(null)}
                        className={cn(
                          "absolute z-10 flex h-6 w-6 items-center justify-center rounded-md border transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-30",
                          at,
                          selected ? "border-primary bg-primary text-primary-foreground" : "border-border bg-popover text-muted-foreground hover:border-primary hover:text-primary",
                        )}>
                        <Icon className="h-3.5 w-3.5" aria-hidden />
                      </button>;
                    })}
                  </div>
                </div>;
              })}
            </div>
          </div>

          <p role="status" className="mt-3 text-xs leading-relaxed text-muted-foreground">{view ? summary : ""}</p>
          {loadError && <p role="alert" className="mt-2 text-xs text-destructive">{loadError}</p>}

          <div className="mt-5 flex justify-end gap-2">
            <button type="button" disabled={busy} onClick={onCancel}
              className="rounded-lg border border-border px-3.5 py-2 text-sm font-medium hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-40">
              Cancel</button>
            <button type="submit" data-testid="move-pane-confirm" disabled={busy || full || !view}
              className="flex items-center gap-1.5 rounded-lg bg-primary px-3.5 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-popover disabled:opacity-50">
              {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />}
              Move to {request?.target.name}</button>
          </div>
        </form>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}
