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
 * who wrote the recap and when, it can be refreshed, and a labelled action lets
 * the user write the header themselves.
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
import type { CSSProperties } from "react";
import { createPortal } from "react-dom";
import {
  ChevronDown,
  Loader2,
  Pencil,
  RotateCw,
  Sparkles,
  Terminal,
  User,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { RecapReason, RecapSource } from "@/lib/agenticIdeApi";
import { PANE_BRAND } from "./terminalThemes";

/** How wide the card gets before it starts wrapping, and its floor on a phone. */
const CARD_WIDTH = 400;
const VIEWPORT_MARGIN = 12;
/**
 * Room the card needs below the header before it opens downwards. Below this it
 * flips above the pane instead — a card that opens off the bottom of the window
 * is the same unreadable as one clipped by the pane.
 */
const ROOM_NEEDED = 300;
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
  arrowX: number;
  placement: "above" | "below";
  /** One of the two is set: the card hangs below the header, or above it. */
  top?: number;
  bottom?: number;
}

/** Where the card goes, measured off the header line it belongs to. */
function anchorTo(node: HTMLElement | null): Anchor | null {
  if (!node || typeof window === "undefined") return null;
  const rect = node.getBoundingClientRect();
  const headerRect = node.closest("header")?.getBoundingClientRect() ?? rect;
  const width = Math.min(CARD_WIDTH, window.innerWidth - VIEWPORT_MARGIN * 2);
  const left = Math.max(
    VIEWPORT_MARGIN,
    Math.min(headerRect.left + 8, window.innerWidth - width - VIEWPORT_MARGIN),
  );
  const arrowX = Math.max(20, Math.min(rect.left + rect.width / 2 - left, width - 20));
  const below = window.innerHeight - rect.bottom;
  if (below < ROOM_NEEDED && rect.top > below) {
    return {
      left,
      width,
      arrowX,
      placement: "above",
      bottom: window.innerHeight - rect.top + 8,
    };
  }
  return { left, width, arrowX, placement: "below", top: rect.bottom + 8 };
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
  /** Write this pane's recap. Absent leaves the edit action out of the card. */
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

  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState<"save" | "clear" | "refresh" | null>(null);
  const [failure, setFailure] = useState("");
  const [anchor, setAnchor] = useState<Anchor | null>(null);

  const headline = (recap ?? "").trim();
  const body = (detail ?? "").trim();

  const close = useCallback(() => {
    setOpen(false);
    setEditing(false);
    setFailure("");
  }, []);

  const reveal = useCallback(() => {
    setAnchor(anchorTo(lineRef.current));
    setOpen(true);
  }, []);

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

  // The pane's brand ink, keyed to the PANE's own ground (a light pane in a
  // dark app is a supported combination — see PANE_BRAND in ./terminalThemes).
  const brand = PANE_BRAND[light ? "light" : "dark"];

  // A pane with nothing to say yet keeps showing which CLI it runs, exactly as
  // it did before recaps existed. Inventing a sentence for it would be noise.
  if (!headline) {
    return (
      <span
        className="truncate font-display text-[11px] font-medium uppercase tracking-[0.14em]"
        style={{ color: brand.inkFaint }}
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
  const attribution = credit(source, writer);

  return (
    <span
      className="flex min-w-0 flex-1 items-center"
      // The brand ink as variables rather than inline colours, because an
      // inline `color` beats every class and would silence the hover below.
      // Set here as well as on the header, so the line styles itself even when
      // rendered outside a pane header (tests, future reuse).
      style={
        {
          "--pane-accent": brand.accent,
          "--pane-ink": brand.ink,
          "--pane-ink-muted": brand.inkMuted,
        } as CSSProperties
      }
    >
      <button
        ref={lineRef}
        type="button"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? tipId : undefined}
        aria-label={`What ${name} is doing`}
        data-testid={`pane-recap-${name}`}
        onMouseDown={(e) => e.stopPropagation()}
        onClick={(e) => {
          e.stopPropagation();
          if (open) close();
          else reveal();
        }}
        // The title is the bar's main text and dresses like it: the display
        // face at a readable size, quiet ink that sharpens under the pointer.
        className={cn(
          "flex min-w-0 flex-1 items-center gap-1 rounded-sm text-left font-display text-[12px] font-medium leading-tight tracking-tight outline-none",
          "text-[color:var(--pane-ink-muted)] transition-colors hover:text-[color:var(--pane-ink)]",
          "focus-visible:ring-1 focus-visible:ring-[color:var(--pane-accent)]",
        )}
      >
        <span className="min-w-0 flex-1 truncate">{headline}</span>
        <ChevronDown
          aria-hidden="true"
          className={cn(
            "h-3 w-3 shrink-0 opacity-55 transition-transform duration-150",
            open && "rotate-180 opacity-90",
          )}
        />
      </button>

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
            data-placement={anchor.placement}
            onMouseDown={(e) => e.stopPropagation()}
            style={{
              position: "fixed",
              left: anchor.left,
              width: anchor.width,
              ...(anchor.top !== undefined
                ? { top: anchor.top }
                : { bottom: anchor.bottom }),
            }}
            className={cn(
              "z-[60] flex max-h-[70vh] flex-col gap-3 rounded-xl border border-border/90 bg-card p-4 text-left",
              "shadow-[0_18px_48px_-22px_rgba(0,0,0,0.85)]",
              "animate-in fade-in-0 zoom-in-95 duration-150",
              anchor.placement === "below"
                ? "slide-in-from-top-1"
                : "slide-in-from-bottom-1",
            )}
          >
            <span
              aria-hidden="true"
              className={cn(
                "absolute h-2.5 w-2.5 -translate-x-1/2 rotate-45 bg-card",
                anchor.placement === "below"
                  ? "-top-[6px] border-l border-t border-border/90"
                  : "-bottom-[6px] border-b border-r border-border/90",
              )}
              style={{ left: anchor.arrowX }}
            />
            <div className="flex items-center justify-between gap-2">
              <span className="flex min-w-0 items-center gap-2 text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
                <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-primary/80" />
                <span className="truncate">
                  <strong className="font-semibold text-foreground/85">{name}</strong>
                  <span className="mx-1.5 opacity-40">/</span>
                  {displayName}
                </span>
              </span>
              <button
                type="button"
                aria-label="Close"
                data-testid={`pane-recap-close-${name}`}
                onClick={close}
                className="-mr-1.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              >
                <X className="h-3.5 w-3.5" />
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
                  className="font-display text-[14px] font-semibold leading-snug tracking-tight text-foreground"
                  data-testid={`pane-recap-headline-${name}`}
                >
                  {headline}
                </p>
                {body && body !== headline && (
                  <p
                    className="max-h-[36vh] overflow-y-auto whitespace-pre-line pr-1 text-[12.5px] leading-[1.65] text-muted-foreground"
                    data-testid={`pane-recap-detail-${name}`}
                  >
                    {body}
                  </p>
                )}
                {WHY[reason] && (
                  <p
                    className="border-l-2 border-primary/45 bg-primary/[0.035] px-3 py-2 text-[11px] leading-relaxed text-muted-foreground"
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
              <div className="mt-0.5 flex flex-wrap items-center gap-2 border-t border-border/60 pt-2.5">
                <span className="mr-auto flex min-w-0 items-center gap-1.5 rounded-full border border-border/60 bg-muted/35 px-2 py-1 text-[10px] text-muted-foreground">
                  {attribution.icon}
                  <span className="truncate">{attribution.label}</span>
                  {generatedAt > 0 && (
                    <span className="shrink-0 opacity-70">· {ago(generatedAt)}</span>
                  )}
                </span>
                <span className="flex shrink-0 items-center gap-1.5">
                  {onSave && (
                    <CardAction
                      label="Write it yourself"
                      testId={`pane-recap-card-edit-${name}`}
                      onClick={() => {
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
      className="flex h-7 items-center gap-1.5 rounded-md border border-transparent px-2 text-[10px] font-medium text-muted-foreground transition-colors hover:border-border/70 hover:bg-muted hover:text-foreground disabled:opacity-40"
    >
      {busy ? <Loader2 className="h-3 w-3 animate-spin" /> : children}
      <span>{label}</span>
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
          className="w-full rounded-lg border border-border bg-muted/25 px-2.5 py-2 text-[12.5px] text-foreground outline-none transition-colors focus:border-primary/60 focus:bg-background"
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
          className="w-full resize-y rounded-lg border border-border bg-muted/25 px-2.5 py-2 text-[12.5px] leading-relaxed text-foreground outline-none transition-colors focus:border-primary/60 focus:bg-background"
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
            className="flex items-center gap-1 rounded-md bg-primary px-2.5 py-1.5 text-[11px] font-semibold text-primary-foreground transition-[filter] hover:brightness-95 disabled:opacity-50"
          >
            {saving && <Loader2 className="h-3 w-3 animate-spin" />}
            Save
          </button>
        </span>
      </div>
    </div>
  );
}
