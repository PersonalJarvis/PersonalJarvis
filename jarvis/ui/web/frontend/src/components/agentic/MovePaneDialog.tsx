import { useEffect, useState, type KeyboardEvent, type ReactNode } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Check, FolderInput, Loader2, Wand2 } from "lucide-react";
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

type MapPane = WorkspaceLayoutView["terminals"][number];
/** What the map is showing: the automatic place, or one side of one pane. */
type Spot = { kind: "auto" } | { kind: "side"; anchor: string; side: TransferSide };

/**
 * Four triangles from the tile's centre to its edges: the side nearest the
 * pointer is the one that lights up, the same rule the grid's own drag uses.
 * Clipped buttons hit-test by their shape, so the whole tile is the target.
 */
const SIDES: { side: TransferSide; words: string; clip: string; key: string }[] = [
  { side: "left", words: "left of", clip: "polygon(0 0, 50% 50%, 0 100%)", key: "ArrowLeft" },
  { side: "right", words: "right of", clip: "polygon(100% 0, 100% 100%, 50% 50%)", key: "ArrowRight" },
  { side: "above", words: "above", clip: "polygon(0 0, 100% 0, 50% 50%)", key: "ArrowUp" },
  { side: "below", words: "below", clip: "polygon(0 100%, 50% 50%, 100% 100%)", key: "ArrowDown" },
];
const SPLIT_DIRECTION = { left: "left", right: "right", above: "above", below: "down" } as const;
const INCOMING = "__incoming__";
const anchorOf = (pane: MapPane) => pane.history_id ? `pane:${pane.history_id}` : pane.name;
const capital = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);

/** A pane on the map, drawn like the real one: a title row over its task. */
function MiniPane({ pane, dimmed = false }: { pane: MapPane; dimmed?: boolean }) {
  return <div className={cn("flex h-full min-h-0 w-full min-w-0 flex-col overflow-hidden rounded-lg border border-border bg-card transition-opacity", dimmed && "opacity-60")}>
    <div className="flex h-7 shrink-0 items-center gap-1.5 border-b border-border/70 px-2">
      <AgentMark agent={pane.agent} label={pane.display_name} variant="plain" size="sm" />
      <span className="truncate text-xs font-medium">{pane.name}</span>
    </div>
    <p className="line-clamp-3 px-2 py-1.5 text-[11px] leading-snug text-muted-foreground">{pane.title || pane.display_name}</p>
  </div>;
}

/** Where the moved pane would sit: its own mark in the accent, or "no room". */
function Ghost({ request, chosen, allowed, children }: { request: MovePaneRequest; chosen: boolean; allowed: boolean; children?: ReactNode }) {
  return <div className={cn(
    "flex h-full min-h-0 w-full min-w-0 flex-col items-center justify-center gap-1 rounded-lg border-2 px-2 text-center",
    !allowed ? "border-dashed border-destructive/50 bg-destructive/5 text-destructive"
      : chosen ? "border-primary bg-primary/15 text-primary" : "border-dashed border-primary/70 bg-primary/10 text-primary",
  )}>
    {allowed ? <span className="flex min-w-0 items-center gap-1.5 text-xs font-semibold">
      {chosen && <Check className="h-3.5 w-3.5 shrink-0" aria-hidden />}
      <AgentMark agent={request.pane.agent} label={request.pane.displayName} variant="plain" size="sm" />
      <span className="truncate">{request.pane.name}</span>
    </span> : <span className="text-[11px] font-medium">No room</span>}
    {children}
  </div>;
}

/**
 * Choose where a pane lands in another workspace's grid.
 *
 * The target grid is drawn as a map of its real panes. Pointing at a pane
 * shows the moved one taking the half nearest the pointer — the tile splits
 * in place, so nothing jumps under the cursor — and a click keeps that
 * place. The dashed column on the right is the automatic place, chosen until
 * something else is. Arrow keys pick a side of the focused pane.
 */
export function MovePaneDialog({ request, busy, onCancel, onConfirm }: Props) {
  const [view, setView] = useState<WorkspaceLayoutView | null>(null);
  const [loadError, setLoadError] = useState("");
  const [choice, setChoice] = useState<TransferPlacement | null>(null);
  const [hover, setHover] = useState<Spot | null>(null);
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

  const panes = view?.terminals ?? [];
  const current = view ? workspaceLayout(view.layout, panes) : null;
  const boxes = treeLayout(current, panes).boxes;
  const full = view !== null && panes.length >= view.max_terminals;
  const fits = (pane: MapPane, side: TransferSide) =>
    !full && current !== null && fitsWorkspace(previewSplit(current, pane.key, INCOMING, SPLIT_DIRECTION[side]));
  const chosenSpot: Spot = choice ? { kind: "side", ...choice } : { kind: "auto" };
  const shown: Spot = hover ?? chosenSpot;
  const sameSpot = (a: Spot, b: Spot) => a.kind === b.kind && (a.kind === "auto" || (b.kind === "side" && a.anchor === b.anchor && a.side === b.side));
  const paneName = request?.pane.name ?? "";
  const targetName = request?.target.name ?? "";

  const pick = (pane: MapPane, side: TransferSide) => { if (!busy && fits(pane, side)) setChoice({ anchor: anchorOf(pane), side }); };
  const onTileKey = (event: KeyboardEvent<HTMLDivElement>, pane: MapPane) => {
    const entry = SIDES.find((item) => item.key === event.key);
    if (!entry) return;
    event.preventDefault();
    pick(pane, entry.side);
  };

  const summary = (() => {
    if (!view) return "";
    if (full) return `${targetName} is full: a workspace holds ${view.max_terminals} terminals.`;
    if (!panes.length) return `${targetName} is empty, so ${paneName} fills it.`;
    if (shown.kind === "auto") return `Automatic: ${paneName} joins the right edge and every pane gets an even share.`;
    const pane = panes.find((entry) => anchorOf(entry) === shown.anchor);
    const words = SIDES.find((entry) => entry.side === shown.side)?.words ?? shown.side;
    if (!pane) return "";
    if (!fits(pane, shown.side)) return `No room ${words} ${pane.name}. ${GRID_LIMIT_HINT}`;
    return `${capital(words)} ${pane.name}: ${paneName} takes half of its space.`;
  })();
  const submit = () => { if (!busy && !full && view) onConfirm(choice); };

  return <Dialog.Root open={request !== null} onOpenChange={(open) => { if (!open && !busy) onCancel(); }}>
    <Dialog.Portal>
      <Dialog.Overlay className="fixed inset-0 z-[80] bg-background/70 backdrop-blur-sm data-[state=open]:animate-in data-[state=open]:fade-in-0" />
      <Dialog.Content data-testid="move-pane-dialog"
        className="fixed left-1/2 top-1/2 z-[90] w-[min(720px,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 rounded-2xl border border-border bg-popover p-6 text-popover-foreground shadow-2xl data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95">
        <form onSubmit={(event) => { event.preventDefault(); submit(); }}>
          <div className="flex items-start gap-4">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary">
              <FolderInput className="h-[18px] w-[18px]" aria-hidden />
            </div>
            <div className="min-w-0 flex-1">
              <Dialog.Title className="truncate text-base font-semibold">Move {paneName} to {targetName}</Dialog.Title>
              <Dialog.Description className="mt-1.5 text-sm leading-relaxed text-muted-foreground">
                Point at a pane and click the side it should go on. The agent keeps running.
              </Dialog.Description>
            </div>
          </div>

          <div className="mt-5 flex h-[clamp(200px,42vh,320px)] gap-1.5 rounded-xl border border-border bg-muted/30 p-1.5"
            onMouseLeave={() => setHover(null)}>
            <div data-testid="move-pane-map" className="relative min-w-0 flex-1">
              {!view && !loadError && <div className="flex h-full items-center justify-center gap-2 text-xs text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />Loading {targetName}…</div>}
              {view && panes.length === 0 && request && <div className="h-full p-0.5"><Ghost request={request} chosen allowed>
                <span className="text-[11px] text-primary/80">Fills the empty workspace</span></Ghost></div>}
              {request && panes.map((pane, index) => {
                const box = boxes[index];
                if (!box) return null;
                const anchor = anchorOf(pane);
                const lit = shown.kind === "side" && shown.anchor === anchor ? shown.side : null;
                const chosenHere = lit !== null && choice?.anchor === anchor && choice.side === lit;
                const horizontal = lit === "left" || lit === "right";
                const ghostFirst = lit === "left" || lit === "above";
                const parts = lit
                  ? [<MiniPane key="pane" pane={pane} dimmed />, <Ghost key="ghost" request={request} chosen={chosenHere} allowed={fits(pane, lit)} />]
                  : [<MiniPane key="pane" pane={pane} />];
                if (ghostFirst) parts.reverse();
                return <div key={pane.key} data-testid={`move-pane-tile-${pane.name}`} data-lit={lit ?? undefined}
                  tabIndex={0} role="group" aria-label={`${pane.name}. Arrow keys choose the side ${paneName} goes on.`}
                  onKeyDown={(event) => onTileKey(event, pane)}
                  className="absolute rounded-xl p-0.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  style={{ left: `${box.x * 100}%`, top: `${box.y * 100}%`, width: `${box.w * 100}%`, height: `${box.h * 100}%` }}>
                  <div className={cn("flex h-full w-full gap-1", horizontal ? "flex-row" : "flex-col")}>
                    {parts.map((part) => <div key={part.key} className="min-h-0 min-w-0 flex-1">{part}</div>)}
                  </div>
                  {SIDES.map(({ side, words, clip }) => {
                    const allowed = fits(pane, side);
                    return <button key={side} type="button" tabIndex={-1} aria-disabled={!allowed || busy}
                      aria-pressed={choice?.anchor === anchor && choice.side === side}
                      data-testid={`move-pane-${pane.name}-${side}`}
                      aria-label={`Place ${paneName} ${words} ${pane.name}`}
                      onMouseEnter={() => setHover({ kind: "side", anchor, side })}
                      onClick={() => pick(pane, side)}
                      style={{ clipPath: clip }}
                      className={cn("absolute inset-0 z-10 bg-transparent", allowed ? "cursor-pointer" : "cursor-not-allowed")} />;
                  })}
                </div>;
              })}
            </div>
            {panes.length > 0 && request && <button type="button" data-testid="move-pane-auto" aria-pressed={choice === null}
              aria-label={`Place ${paneName} automatically at the right edge`} disabled={busy || full}
              onClick={() => setChoice(null)} onMouseEnter={() => setHover({ kind: "auto" })} onFocus={() => setHover({ kind: "auto" })} onBlur={() => setHover(null)}
              className={cn(
                "flex w-[76px] shrink-0 flex-col items-center justify-center gap-1.5 rounded-lg border-2 px-1 text-center transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50",
                sameSpot(shown, { kind: "auto" })
                  ? choice === null ? "border-primary bg-primary/15 text-primary" : "border-dashed border-primary/70 bg-primary/10 text-primary"
                  : "border-dashed border-border text-muted-foreground hover:border-primary/60 hover:text-primary",
              )}>
              {sameSpot(shown, { kind: "auto" })
                ? <><AgentMark agent={request.pane.agent} label={request.pane.displayName} variant="plain" size="sm" />
                    <span className="max-w-full truncate text-xs font-semibold">{paneName}</span></>
                : <Wand2 className="h-4 w-4" aria-hidden />}
              <span className="text-[10px] font-medium uppercase tracking-wide">Auto</span>
            </button>}
          </div>

          <p role="status" className={cn("mt-3 min-h-[1.25rem] text-xs leading-relaxed",
            shown.kind === "side" && view && !panes.some((pane) => anchorOf(pane) === shown.anchor && fits(pane, shown.side)) ? "text-destructive" : "text-muted-foreground")}>
            {summary}</p>
          {loadError && <p role="alert" className="mt-2 text-xs text-destructive">{loadError}</p>}

          <div className="mt-4 flex items-center justify-end gap-2">
            <button type="button" disabled={busy} onClick={onCancel}
              className="rounded-lg border border-border px-3.5 py-2 text-sm font-medium hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-40">
              Cancel</button>
            <button type="submit" data-testid="move-pane-confirm" disabled={busy || full || !view}
              className="flex items-center gap-1.5 rounded-lg bg-primary px-3.5 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-popover disabled:opacity-50">
              {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />}
              Move to {targetName}</button>
          </div>
        </form>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}
