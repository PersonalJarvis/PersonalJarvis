import { useLayoutEffect, useState } from "react";
import { ArrowUp } from "lucide-react";

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
 */
export function PromptEditor({
  target,
  sending,
  seed,
  onSend,
}: {
  target: string;
  sending: boolean;
  seed: PromptSeed;
  onSend: (draft: string) => Promise<void>;
}) {
  const [value, setValue] = useState(seed.value);
  useLayoutEffect(() => setValue(seed.value), [seed.revision, seed.value]);
  const submit = () => {
    if (!target || sending || !value.trim()) return;
    void onSend(value);
  };

  return (
    <div className="flex min-h-0 flex-1 items-end gap-2">
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
        placeholder={
          target
            ? `Type an instruction for ${target} — Enter sends, Shift+Enter adds a line`
            : "Pick a terminal first"
        }
        className="h-full min-h-[44px] flex-1 resize-none rounded-lg border border-border bg-background/60 px-3 py-2 text-sm outline-none focus:border-primary/50 focus-visible:ring-2 focus-visible:ring-primary/30"
      />
      <button
        type="button"
        className="btn-primary h-[52px] shrink-0"
        disabled={sending || !value.trim() || !target}
        onClick={submit}
      >
        <ArrowUp className="h-4 w-4" />
        {sending ? "Preparing…" : "Send"}
      </button>
    </div>
  );
}
