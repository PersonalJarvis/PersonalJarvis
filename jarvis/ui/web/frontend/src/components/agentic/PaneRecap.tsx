/**
 * The pane header's headline — what this session is doing — and the card behind
 * it.
 *
 * ## What was wrong with the version this replaces
 *
 * The header line itself was never the problem: a header is a few centimetres
 * wide, a recap is a sentence, and CSS clipping it is correct. The problem was
 * everything around that:
 *
 * * **The long form was unreadable.** It lived in a CSS-only hover tooltip
 *   anchored inside the pane, capped at the pane's own width. On a quarter of a
 *   screen that is a column of 11 px text a dozen characters wide — the text
 *   was all there and none of it could be read.
 * * **It could not be touched.** `pointer-events-none` meant the moment you
 *   moved the mouse toward it, it vanished. Nothing in it could be selected,
 *   copied, or clicked.
 * * **A thin recap never explained itself.** The backend has always known
 *   whether a model wrote the line or the string rules did, and why not the
 *   model — that never reached the screen, so "the recaps are not meaningful"
 *   had no visible cause.
 * * **It could not be corrected.** No summarizer knows that a pane is "the
 *   branch I'm about to demo" or "leave this one alone".
 *
 * So the long form is a real card now: rendered in a portal so the pane's own
 * `overflow: hidden` cannot clip it, wide enough to read a paragraph in, given
 * the app's card surface rather than a bespoke one, and interactive — it says
 * who wrote the recap and when, it can be refreshed, and the pencil lets the
 * user write the header themselves.
 *
 * ## Why a portal
 *
 * A pane clips its own content; that is what makes a grid of terminals a grid.
 * Anything anchored inside a pane is therefore bounded by it, and a recap card
 * bounded by a narrow pane is the unreadable column described above. Fixed
 * positioning off the trigger's rect is the only placement that can be as wide
 * as the sentence needs while still pointing at the pane it belongs to.
 */
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Loader2, Pencil, RotateCw, Sparkles, Terminal, User, X } from "lucide-react";
import { cn } from "@/lib/utils";
import type { RecapReason, RecapSource } from "@/lib/agenticIdeApi";

/** How wide the card gets before it starts wrapping, and its floor on a phone. */
const CARD_WIDTH = 440;
const VIEWPORT_MARGIN = 12;
/**
 * Room the card needs below the header before it opens downwards. Below this it
 * flips above the pane instead — a card that opens off the bottom of the window
 * is the same unreadable as one clipped by the pane.
 */
const ROOM_NEEDED = 260;
/**
 * How long the card survives the pointer leaving it. Long enough to cross the
 * gap between the header line and the card, short enough that brushing past a
 * pane on the way somewhere else does not leave a card hanging.
 */
const CLOSE_DELAY_MS = 160;

/** The same two caps the backend enforces, so the counter cannot lie. */
export const MAX_HEADLINE = 200;
export const MAX_DETAIL = 2000;

/**
 * Why the recap on screen is the one on screen, in a sentence.
 *
 * Every key here was a silent early return in the backend's scheduler. Saying
 * them out loud is the entire fix for "the recap is thin and nobody knows why".
 * `summarized` is deliberately absent: the footer already names the model that
 * wrote it and how long ago, and repeating that in a sentence is noise.
 */
const WHY: Partial<Record<RecapReason, string>> = {
  pinned: "You wrote this. It stays until you reset it.",
  disabled:
    "Model recaps are switched off, so this is read from the pane's own output.",
  not_started: "Nothing is running in this pane yet, so there is nothing to summarize.",
  warming:
    "Too little output so far to summarize — this line is read from the pane's own output.",
  working: "A summary is being written now. This line is read from the pane's own output.",
  queued:
    "Waiting its turn to be summarized. This line is read from the pane's own output.",
  unavailable:
    "No model could summarize this pane, so this line is read from its own output.",
};

/** What the footer badge says about who wrote the sentence above it. */
function credit(source: RecapSource, writer: string): { label: string; icon: JSX.Element } {
  if (source === "user")
    return { label: "Written by you", icon: <User className="h-3 w-3" /> };
  if (source === "model")
    return {
      label: writer ? `Summarized by ${writer}` : "Summarized by a model",
      icon: <Sparkles className="h-3 w-3" />,
    };
  return { label: "Read from the output", icon: <Terminal className="h-3 w-3" /> };
}

/** "34s ago" / "6 min ago" — the same wording the backend uses for idle time. */
function ago(at: number): string {
  if (!at) return "";
  const seconds = Math.max(0, Math.round(Date.now() / 1000 - at));
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  return `${Math.floor(seconds / 3600)} h ago`;
}

interface Anchor {
  left: number;
  width: number;
  /** One of the two is set: the card hangs below the header, or above it. */
  top?: number;
  bottom?: number;
}

/** Where the card goes, measured off the header line it belongs to. */
function anchorTo(node: HTMLElement | null): Anchor | null {
  if (!node || typeof window === "undefined") return null;
  const rect = node.getBoundingClientRect();
  const width = Math.min(CARD_WIDTH, window.innerWidth - VIEWPORT_MARGIN * 2);
  const left = Math.max(
    VIEWPORT_MARGIN,
    Math.min(rect.left, window.innerWidth - width - VIEWPORT_MARGIN),
  );
  const below = window.innerHeight - rect.bottom;
  if (below < ROOM_NEEDED && rect.top > below) {
    return { left, width, bottom: window.innerHeight - rect.top + 6 };
  }
  return { left, width, top: rect.bottom + 6 };
}

export interface PaneRecapProps {
  /** The pane's call-sign — "Mika". */
  name: string;
  /** The coding CLI behind it — "Claude Code". */
  displayName: string;
  recap?: string;
  detail?: string;
  source?: RecapSource;
  reason?: RecapReason;
  writer?: string;
  /** What went wrong the last time this pane was summarized, if anything. */
  note?: string;
  /** When the model wrote it, or when the user did. Unix seconds; 0 for derived. */
  generatedAt?: number;
  light: boolean;
  /** Write this pane's recap. Absent leaves the pencil off the header. */
  onSave?: (headline: string, detail: string) => Promise<void>;
  /** Hand the pane back to the automatic recap. */
  onClear?: () => Promise<void>;
  /** Read the pane again and summarize it now. */
  onRefresh?: () => Promise<void>;
}

export function PaneRecap({
  name,
  displayName,
  recap,
  detail,
  source = "heuristic",
  reason = "",
  writer = "",
  note = "",
  generatedAt = 0,
  light,
  onSave,
  onClear,
  onRefresh,
}: PaneRecapProps) {
  const lineRef = useRef<HTMLButtonElement | null>(null);
  const cardRef = useRef<HTMLDivElement | null>(null);
  const closeTimer = useRef<number | null>(null);
  /**
   * When the card was last dismissed on purpose.
   *
   * Escape returns focus to the header line, and the header line opens the card
   * on focus — so without this, dismissing it re-opens it immediately. The same
   * guard covers the pointer that is still resting on the line after a click.
   */
  const dismissedAt = useRef(0);

  const [open, setOpen] = useState(false);
  /** Opened by a click rather than by hovering: it stays until dismissed. */
  const [sticky, setSticky] = useState(false);
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState<"save" | "clear" | "refresh" | null>(null);
  const [failure, setFailure] = useState("");
  const [anchor, setAnchor] = useState<Anchor | null>(null);

  const headline = (recap ?? "").trim();
  const body = (detail ?? "").trim();

  const cancelClose = useCallback(() => {
    if (closeTimer.current !== null) {
      window.clearTimeout(closeTimer.current);
      closeTimer.current = null;
    }
  }, []);

  const close = useCallback(() => {
    cancelClose();
    dismissedAt.current = Date.now();
    setOpen(false);
    setSticky(false);
    setEditing(false);
    setFailure("");
  }, [cancelClose]);

  /** Leaving the trigger or the card closes it — unless it was clicked open. */
  const closeSoon = useCallback(() => {
    if (sticky || editing) return;
    cancelClose();
    closeTimer.current = window.setTimeout(() => setOpen(false), CLOSE_DELAY_MS);
  }, [cancelClose, editing, sticky]);

  const reveal = useCallback(() => {
    // Dismissing means dismissed: neither the focus Escape hands back to the
    // header line nor a pointer still resting on it may spring the card open
    // again in the same breath.
    if (Date.now() - dismissedAt.current < 250) return;
    cancelClose();
    setAnchor(anchorTo(lineRef.current));
    setOpen(true);
  }, [cancelClose]);

  useEffect(() => () => cancelClose(), [cancelClose]);

  // The card is positioned in viewport coordinates, so anything that moves the
  // pane underneath it — scrolling the workspace, resizing the window, dragging
  // the prompt bar's seam — has to move the card with it.
  useLayoutEffect(() => {
    if (!open) return;
    const track = () => setAnchor(anchorTo(lineRef.current));
    track();
    window.addEventListener("resize", track);
    window.addEventListener("scroll", track, true);
    return () => {
      window.removeEventListener("resize", track);
      window.removeEventListener("scroll", track, true);
    };
  }, [open]);

  // Escape closes, and a click anywhere outside dismisses a card that was
  // clicked open. Bound only while it IS open, so a grid of eight panes is not
  // eight permanent document listeners.
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        close();
        lineRef.current?.focus();
      }
    };
    const onDown = (event: MouseEvent) => {
      const target = event.target as Node;
      if (cardRef.current?.contains(target) || lineRef.current?.contains(target)) return;
      close();
    };
    document.addEventListener("keydown", onKey, true);
    document.addEventListener("mousedown", onDown);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      document.removeEventListener("mousedown", onDown);
    };
  }, [close, open]);

  // A pane with nothing to say yet keeps showing which CLI it runs, exactly as
  // it did before recaps existed. Inventing a sentence for it would be noise.
  if (!headline) {
    return (
      <span
        className="truncate text-[11px] uppercase tracking-wider"
        style={{ color: light ? "#77777f" : "#8a8a95" }}
        data-testid={`pane-agent-${name}`}
      >
        {displayName}
      </span>
    );
  }

  const run = async (kind: "save" | "clear" | "refresh", action: () => Promise<void>) => {
    setBusy(kind);
    setFailure("");
    try {
      await action();
      if (kind !== "refresh") setEditing(false);
    } catch (error) {
      setFailure(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(null);
    }
  };

  const tipId = `pane-recap-card-${name}`;

  return (
    <span
      className="flex min-w-0 flex-1 items-center gap-1"
      onMouseEnter={reveal}
      onMouseLeave={closeSoon}
    >
      <button
        ref={lineRef}
        type="button"
        aria-expanded={open}
        aria-controls={open ? tipId : undefined}
        aria-label={`What ${name} is doing`}
        data-testid={`pane-recap-${name}`}
        onFocus={reveal}
        onBlur={closeSoon}
        onMouseDown={(e) => e.stopPropagation()}
        onClick={(e) => {
          e.stopPropagation();
          if (sticky) close();
          else {
            setSticky(true);
            reveal();
          }
        }}
        className="block min-w-0 flex-1 truncate rounded-sm text-left text-[11px] leading-tight outline-none transition-colors hover:text-foreground focus-visible:ring-1 focus-visible:ring-primary/60"
        style={{ color: light ? "#5f5f68" : "#9b9ba6" }}
      >
        {headline}
      </button>

      {/* The pencil. Quiet until the header is hovered or something in it has
          focus, like every other pane action — but it is the affordance the
          whole editing feature hangs off, so it is never more than a hover
          away, and it stays lit while the card it opened is open. */}
      {onSave && (
        <button
          type="button"
          aria-label={`Write ${name}'s recap yourself`}
          title={`Write ${name}'s recap yourself`}
          data-testid={`pane-recap-edit-${name}`}
          onMouseDown={(e) => e.stopPropagation()}
          onClick={(e) => {
            e.stopPropagation();
            setSticky(true);
            setEditing(true);
            reveal();
          }}
          className={cn(
            "flex h-5 w-5 shrink-0 items-center justify-center rounded transition-all",
            light ? "hover:bg-black/10" : "hover:bg-white/10",
            open
              ? "opacity-100"
              : "opacity-0 focus-visible:opacity-100 group-hover/header:opacity-100",
          )}
          style={{ color: light ? "#6b6b73" : "#9a9aa5" }}
        >
          <Pencil className="h-3 w-3" />
        </button>
      )}

      {open &&
        anchor &&
        typeof document !== "undefined" &&
        createPortal(
          <div
            ref={cardRef}
            id={tipId}
            role="dialog"
            aria-label={`What ${name} is doing`}
            data-testid={`pane-recap-card-${name}`}
            onMouseEnter={cancelClose}
            onMouseLeave={closeSoon}
            onMouseDown={(e) => e.stopPropagation()}
            style={{
              position: "fixed",
              left: anchor.left,
              width: anchor.width,
              ...(anchor.top !== undefined
                ? { top: anchor.top }
                : { bottom: anchor.bottom }),
            }}
            className="z-[60] flex flex-col gap-2.5 rounded-xl border border-border bg-card/95 p-3.5 text-left shadow-2xl backdrop-blur-sm"
          >
            <div className="flex items-center justify-between gap-2">
              <span className="min-w-0 truncate text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
                {name} · {displayName}
              </span>
              <button
                type="button"
                aria-label="Close"
                data-testid={`pane-recap-close-${name}`}
                onClick={close}
                className="-mr-1 flex h-5 w-5 shrink-0 items-center justify-center rounded text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              >
                <X className="h-3 w-3" />
              </button>
            </div>

            {editing ? (
              <RecapEditor
                name={name}
                headline={headline}
                detail={body}
                saving={busy === "save"}
                onCancel={() => {
                  setEditing(false);
                  setFailure("");
                }}
                onSave={(nextHeadline, nextDetail) =>
                  void run("save", () => onSave!(nextHeadline, nextDetail))
                }
              />
            ) : (
              <>
                {/* The headline in FULL. The header clips it; this is the
                    place it is allowed to wrap, which is the whole reason
                    somebody opened the card. */}
                <p
                  className="text-[13px] font-medium leading-snug text-foreground"
                  data-testid={`pane-recap-headline-${name}`}
                >
                  {headline}
                </p>
                {body && body !== headline && (
                  <p
                    className="max-h-[40vh] overflow-y-auto whitespace-pre-line text-[12.5px] leading-relaxed text-muted-foreground"
                    data-testid={`pane-recap-detail-${name}`}
                  >
                    {body}
                  </p>
                )}
                {WHY[reason] && (
                  <p
                    className="rounded-lg bg-muted/50 px-2.5 py-1.5 text-[11px] leading-relaxed text-muted-foreground"
                    data-testid={`pane-recap-why-${name}`}
                  >
                    {WHY[reason]}
                    {note && <span className="mt-1 block opacity-70">{note}</span>}
                  </p>
                )}
              </>
            )}

            {failure && (
              <p
                className="text-[11px] leading-relaxed text-destructive"
                data-testid={`pane-recap-error-${name}`}
              >
                {failure}
              </p>
            )}

            {!editing && (
              <div className="flex items-center justify-between gap-2 border-t border-border/60 pt-2">
                <span className="flex min-w-0 items-center gap-1.5 text-[10px] text-muted-foreground">
                  {credit(source, writer).icon}
                  <span className="truncate">{credit(source, writer).label}</span>
                  {generatedAt > 0 && (
                    <span className="shrink-0 opacity-70">· {ago(generatedAt)}</span>
                  )}
                </span>
                <span className="flex shrink-0 items-center gap-1">
                  {onSave && (
                    <CardAction
                      label="Write it yourself"
                      testId={`pane-recap-card-edit-${name}`}
                      onClick={() => {
                        setSticky(true);
                        setEditing(true);
                      }}
                    >
                      <Pencil className="h-3 w-3" />
                    </CardAction>
                  )}
                  {source === "user" && onClear ? (
                    <CardAction
                      label="Back to the automatic recap"
                      testId={`pane-recap-reset-${name}`}
                      busy={busy === "clear"}
                      onClick={() => void run("clear", onClear)}
                    >
                      <RotateCw className="h-3 w-3" />
                    </CardAction>
                  ) : (
                    onRefresh && (
                      <CardAction
                        label="Summarize this pane again"
                        testId={`pane-recap-refresh-${name}`}
                        busy={busy === "refresh"}
                        onClick={() => void run("refresh", onRefresh)}
                      >
                        <RotateCw className="h-3 w-3" />
                      </CardAction>
                    )
                  )}
                </span>
              </div>
            )}
          </div>,
          document.body,
        )}
    </span>
  );
}

function CardAction({
  label,
  testId,
  busy = false,
  onClick,
  children,
}: {
  label: string;
  testId: string;
  busy?: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      data-testid={testId}
      disabled={busy}
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      className="flex h-6 items-center gap-1 rounded-md px-1.5 text-[10px] text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-40"
    >
      {busy ? <Loader2 className="h-3 w-3 animate-spin" /> : children}
    </button>
  );
}

/**
 * Writing a pane's recap by hand.
 *
 * Two fields rather than one, because the recap is two things: the clause the
 * header shows and the paragraph the card shows. Somebody labelling a pane
 * "don't touch — demo branch" wants only the first; somebody recording where
 * they left off wants both, and being made to cram that into one line is how a
 * good note becomes a bad one.
 */
function RecapEditor({
  name,
  headline,
  detail,
  saving,
  onSave,
  onCancel,
}: {
  name: string;
  headline: string;
  detail: string;
  saving: boolean;
  onSave: (headline: string, detail: string) => void;
  onCancel: () => void;
}) {
  const [line, setLine] = useState(headline);
  const [body, setBody] = useState(detail);
  const firstRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    firstRef.current?.focus();
    firstRef.current?.select();
  }, []);

  const submit = () => {
    if (!saving) onSave(line.trim(), body.trim());
  };

  // Ctrl/Cmd+Enter saves from either field: the detail box takes plain Enter as
  // a newline, so there has to be a way to finish without reaching for a mouse.
  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      submit();
    }
  };

  return (
    <div className="flex flex-col gap-2">
      <label className="flex flex-col gap-1">
        <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
          Header line
        </span>
        <input
          ref={firstRef}
          value={line}
          maxLength={MAX_HEADLINE}
          placeholder="What this pane is for"
          data-testid={`pane-recap-input-${name}`}
          onChange={(e) => setLine(e.target.value)}
          onKeyDown={onKeyDown}
          className="w-full rounded-lg border border-border bg-background px-2.5 py-1.5 text-[12.5px] text-foreground outline-none focus:border-primary/60"
        />
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
          The longer version <span className="opacity-60">— optional</span>
        </span>
        <textarea
          value={body}
          rows={4}
          maxLength={MAX_DETAIL}
          placeholder="Where the work stands, what is outstanding…"
          data-testid={`pane-recap-detail-input-${name}`}
          onChange={(e) => setBody(e.target.value)}
          onKeyDown={onKeyDown}
          className="w-full resize-y rounded-lg border border-border bg-background px-2.5 py-1.5 text-[12.5px] leading-relaxed text-foreground outline-none focus:border-primary/60"
        />
      </label>
      <div className="flex items-center justify-between gap-2">
        <span className="text-[10px] text-muted-foreground">
          {/* An empty header line is how you clear a hand-written recap, and
              saying so beats a disabled Save nobody can explain. */}
          {line.trim()
            ? "⌘/Ctrl + Enter to save"
            : "Save with an empty line to go back to the automatic recap"}
        </span>
        <span className="flex items-center gap-1.5">
          <button
            type="button"
            onClick={onCancel}
            data-testid={`pane-recap-cancel-${name}`}
            className="rounded-md px-2 py-1 text-[11px] text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={submit}
            disabled={saving}
            data-testid={`pane-recap-save-${name}`}
            className="flex items-center gap-1 rounded-md bg-primary/20 px-2.5 py-1 text-[11px] font-medium text-primary transition-colors hover:bg-primary/30 disabled:opacity-50"
          >
            {saving && <Loader2 className="h-3 w-3 animate-spin" />}
            Save
          </button>
        </span>
      </div>
    </div>
  );
}
