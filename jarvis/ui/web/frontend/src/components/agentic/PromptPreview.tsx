import { useEffect } from "react";
import { FileCode2, Send, Type, X } from "lucide-react";

/**
 * The composed prompt, shown for approval before it is typed into an agent.
 *
 * Jarvis rewrites a rough instruction into a briefed markdown task with the
 * relevant files attached. That is a genuine improvement for a spoken sentence,
 * where the alternative is a transcript — but someone who TYPED an instruction
 * already chose their words, and silently replacing them would be the wrong
 * kind of helpful. So the typed path composes, shows the result, and lets the
 * user pick: send the brief, send exactly what they wrote, or back out.
 *
 * Escape always cancels. The caller keeps the original text, so nothing the
 * user typed can be lost behind this.
 */
export interface PromptPreviewProps {
  /** Call-sign of the terminal this would go to. */
  terminal: string;
  /** The briefed markdown prompt. */
  composed: string;
  /** Repo-relative files the prompt references. */
  files: string[];
  /** Which layer produced it — `llm` is the full path. */
  composedBy: "llm" | "fallback" | "raw";
  onSend: () => void;
  onSendVerbatim: () => void;
  onCancel: () => void;
}

export function PromptPreview({
  terminal,
  composed,
  files,
  composedBy,
  onSend,
  onSendVerbatim,
  onCancel,
}: PromptPreviewProps) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  return (
    <div
      className="rounded-lg border border-primary/40 bg-card/80 p-3"
      data-testid="prompt-preview"
    >
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="text-xs font-medium text-muted-foreground">
          Prompt for <span className="text-foreground">{terminal}</span>
        </span>
        <button
          type="button"
          onClick={onCancel}
          className="btn-ghost h-7 w-7 p-0"
          aria-label="Discard this prompt"
          data-testid="prompt-preview-cancel"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>

      <pre
        className="max-h-64 overflow-y-auto whitespace-pre-wrap break-words rounded-md
                   border border-border bg-background/60 px-3 py-2 font-mono text-xs
                   leading-relaxed text-foreground"
        data-testid="prompt-preview-body"
      >
        {composed}
      </pre>

      {files.length > 0 && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <FileCode2 className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          {files.map((file) => (
            <span key={file} className="chip font-mono text-[10px]">
              {file}
            </span>
          ))}
        </div>
      )}

      {/* Only ever shown when the full path did NOT run. The readback must not
          claim more than happened, and neither must this. */}
      {composedBy !== "llm" && (
        <p
          className="mt-2 text-[11px] text-muted-foreground"
          data-testid="prompt-preview-note"
        >
          Roughly prepared — no capable writing model was reachable, so this is
          the cleaned-up version of what you typed rather than a full brief.
        </p>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={onSend}
          className="btn-primary"
          data-testid="prompt-preview-send"
        >
          <Send className="h-3.5 w-3.5" />
          Send
        </button>
        <button
          type="button"
          onClick={onSendVerbatim}
          className="btn-ghost"
          data-testid="prompt-preview-verbatim"
        >
          <Type className="h-3.5 w-3.5" />
          Send verbatim
        </button>
        <span className="ml-auto text-[11px] text-muted-foreground">
          Esc to discard
        </span>
      </div>
    </div>
  );
}
