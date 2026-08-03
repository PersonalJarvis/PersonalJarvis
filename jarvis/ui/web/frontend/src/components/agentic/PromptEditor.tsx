import { useLayoutEffect, useRef, useState } from "react";
import { ArrowUp, Loader2, Paperclip } from "lucide-react";
import { cn } from "@/lib/utils";

export interface PromptSeed {
  value: string;
  revision: number;
}

/**
 * The hot typing path, isolated from the terminal wall around it.
 *
 * A workspace may hold dozens of xterm canvases. Keeping the draft in the
 * grid made every character reconcile all of them, so composer lag grew with
 * the number of sessions. Only intentional resets travel down from the grid;
 * ordinary keystrokes stay in this small component.
 *
 * ## Shape
 *
 * The editor is the BODY of the composer surface, not a boxed control sitting
 * inside one: the border, the radius and the focus treatment belong to the
 * surface (see the composer in AgenticGrid), so what is drawn here is a bare
 * writing area with one row of controls under it. A field with its own border
 * inside a bordered panel is the single most reliable way to make an interface
 * read as assembled rather than designed — two frames, 4 px apart, neither of
 * which is the edge of anything.
 *
 * The placeholder names the target and stops there. It used to carry the
 * keyboard contract as well ("Enter sends, Shift+Enter adds a line"), which put
 * a permanent instruction manual in the largest empty area on screen; that
 * contract now lives in the footer, at label size, where it is available
 * without being the loudest text in the view.
 */
export function PromptEditor({
  target,
  sending,
  seed,
  onSend,
  onAttach,
}: {
  target: string;
  sending: boolean;
  seed: PromptSeed;
  onSend: (draft: string) => Promise<void>;
  /**
   * Pick files by hand, for the people who do not drag them.
   *
   * Optional: dropping and pasting are the primary paths and neither needs
   * this, so a caller that has no attachment pipeline simply gets no button
   * rather than a dead one.
   */
  onAttach?: (files: File[]) => void;
}) {
  const [value, setValue] = useState(seed.value);
  const fileRef = useRef<HTMLInputElement>(null);
  useLayoutEffect(() => setValue(seed.value), [seed.revision, seed.value]);
  const ready = Boolean(target) && !sending && value.trim().length > 0;
  const submit = () => {
    if (!ready) return;
    void onSend(value);
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-1.5">
      <textarea
        name="agent-instruction"
        aria-label={target ? `Instruction for ${target}` : "Agent instruction"}
        value={value}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
            event.preventDefault();
            submit();
          }
        }}
        placeholder={target ? `Message ${target}` : "Pick a terminal first"}
        className={
          "min-h-0 w-full flex-1 resize-none bg-transparent px-3 pt-2.5 text-sm leading-relaxed " +
          "text-foreground outline-none placeholder:text-muted-foreground/60"
        }
      />
      <div className="flex shrink-0 items-center gap-2 px-2 pb-2">
        {onAttach && (
          <>
            <input
              ref={fileRef}
              type="file"
              multiple
              className="sr-only"
              tabIndex={-1}
              onChange={(event) => {
                const files = Array.from(event.currentTarget.files ?? []);
                event.currentTarget.value = "";
                if (files.length > 0) onAttach(files);
              }}
            />
            <button
              type="button"
              data-testid="agentic-composer-attach"
              aria-label="Attach files to this instruction"
              title="Attach files — you can also drop or paste them here"
              disabled={!target}
              onClick={() => fileRef.current?.click()}
              className={
                "flex h-7 w-7 shrink-0 items-center justify-center rounded-control " +
                "text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground " +
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60 " +
                "disabled:cursor-not-allowed disabled:opacity-40"
              }
            >
              <Paperclip className="h-3.5 w-3.5" />
            </button>
          </>
        )}
        {/* The keyboard contract, at the weight of a footnote. Hidden on a
            narrow window before anything that can be clicked is: it is the one
            thing in this row a reader can do without. */}
        <span className="ml-auto hidden shrink-0 items-center gap-2 text-[11px] text-muted-foreground/70 sm:flex">
          <kbd className="rounded border border-border/80 px-1 font-sans text-[10px]">
            ↵
          </kbd>
          send
          <kbd className="rounded border border-border/80 px-1 font-sans text-[10px]">
            ⇧↵
          </kbd>
          new line
        </span>
        <button
          type="button"
          aria-label={sending ? "Preparing the instruction" : "Send the instruction"}
          title={sending ? "Preparing the instruction…" : "Send"}
          className={cn(
            "ml-auto flex h-7 w-7 shrink-0 items-center justify-center rounded-control",
            "transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60",
            "sm:ml-0",
            ready
              ? "bg-primary text-primary-foreground hover:bg-primary/90"
              : "bg-secondary text-muted-foreground/60",
            !ready && "cursor-not-allowed",
          )}
          disabled={!ready}
          onClick={submit}
        >
          {sending ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <ArrowUp className="h-3.5 w-3.5" />
          )}
        </button>
      </div>
    </div>
  );
}
