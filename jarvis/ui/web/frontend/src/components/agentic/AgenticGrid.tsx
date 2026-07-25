/**
 * The running Agentic-IDE workspace: a grid of named agent terminals, a toolbar
 * (focus mode, appearance, font size, close), and one prompt bar that types into
 * whichever terminal is selected.
 *
 * The prompt bar is deliberately the same channel Jarvis uses by voice — it
 * POSTs to `/terminals/{name}/prompt` rather than writing into xterm locally.
 * So "click Mika, type 'run the tests'" and saying "tell Mika to run the tests"
 * take the identical path through the app, and the two can never behave
 * differently.
 */
import { useCallback, useMemo, useState } from "react";
import {
  ArrowUp,
  Brain,
  FolderGit2,
  Minus,
  Moon,
  Plus,
  Power,
  Sun,
  Type,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";
import {
  AgenticTerminal,
  PaneStatusPill,
  type PaneStatus,
} from "./AgenticTerminal";
import type { TerminalAppearance } from "./terminalThemes";
import { promptTerminal, type SessionState } from "@/lib/agenticIdeApi";

interface AgenticGridProps {
  session: SessionState;
  focusMode: boolean;
  onToggleFocus: (enabled: boolean) => void;
  onClose: () => void;
  busy?: boolean;
}

function gridColumns(count: number): number {
  if (count <= 1) return 1;
  if (count <= 2) return 2;
  if (count <= 6) return Math.min(3, Math.ceil(count / 2));
  return 4;
}

const FONT_MIN = 10;
const FONT_MAX = 20;

export function AgenticGrid({
  session,
  focusMode,
  onToggleFocus,
  onClose,
  busy = false,
}: AgenticGridProps) {
  const pushToast = useEventStore((s) => s.pushToast);
  const [appearance, setAppearance] = useState<TerminalAppearance>("light");
  const [fontSize, setFontSize] = useState(13);
  const [target, setTarget] = useState(session.terminals[0]?.name ?? "");
  const [statuses, setStatuses] = useState<Record<string, { status: PaneStatus; detail?: string }>>({});
  const [prompt, setPrompt] = useState("");
  const [sending, setSending] = useState(false);

  const columns = gridColumns(session.terminals.length);

  const setStatus = useCallback((name: string, status: PaneStatus, detail?: string) => {
    setStatuses((prev) => ({ ...prev, [name]: { status, detail } }));
  }, []);

  const send = async () => {
    const text = prompt.trim();
    if (!text || !target) return;
    setSending(true);
    try {
      await promptTerminal(target, text);
      setPrompt("");
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setSending(false);
    }
  };

  const project = session.project;
  const branchLabel = useMemo(
    () => (project.is_repo && project.branch ? `on ${project.branch}` : ""),
    [project],
  );

  return (
    <div className="flex h-full flex-col">
      {/* ---------------------------------------------------------- toolbar */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-border px-4 py-2.5">
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <FolderGit2 className="h-4 w-4 shrink-0 text-primary" />
          <span className="font-display text-sm font-semibold">{project.name}</span>
          {branchLabel && (
            <span className="chip shrink-0 text-[10px] uppercase tracking-wide">
              {branchLabel}
            </span>
          )}
          <code className="hidden min-w-0 truncate font-mono text-[11px] text-muted-foreground lg:block">
            {session.folder}
          </code>
        </div>

        {/* Focus mode — the explicit switch into "Jarvis codes with me here". */}
        <button
          type="button"
          onClick={() => onToggleFocus(!focusMode)}
          aria-pressed={focusMode}
          data-testid="agentic-focus-toggle"
          title={
            focusMode
              ? "Focused coding mode is on — Jarvis answers inside this workspace. Click to leave."
              : "Turn on focused coding mode — Jarvis answers inside this workspace until you switch back."
          }
          className={cn(
            "flex shrink-0 items-center gap-2 rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors",
            focusMode
              ? "border-primary/60 bg-primary/15 text-primary"
              : "border-border text-muted-foreground hover:border-primary/40 hover:text-foreground",
          )}
        >
          <Brain className="h-4 w-4" />
          {focusMode ? "Coding mode ON" : "Coding mode OFF"}
        </button>

        <div className="flex shrink-0 items-center gap-1 rounded-lg border border-border p-0.5">
          <button
            type="button"
            aria-label="Light terminals"
            aria-pressed={appearance === "light"}
            onClick={() => setAppearance("light")}
            className={cn(
              "flex h-7 w-7 items-center justify-center rounded-md transition-colors",
              appearance === "light" ? "bg-primary/20 text-primary" : "text-muted-foreground",
            )}
          >
            <Sun className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            aria-label="Dark terminals"
            aria-pressed={appearance === "dark"}
            onClick={() => setAppearance("dark")}
            className={cn(
              "flex h-7 w-7 items-center justify-center rounded-md transition-colors",
              appearance === "dark" ? "bg-primary/20 text-primary" : "text-muted-foreground",
            )}
          >
            <Moon className="h-3.5 w-3.5" />
          </button>
        </div>

        <div className="flex shrink-0 items-center gap-1 rounded-lg border border-border p-0.5">
          <button
            type="button"
            aria-label="Smaller text"
            onClick={() => setFontSize((n) => Math.max(FONT_MIN, n - 1))}
            className="flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground hover:text-foreground"
          >
            <Minus className="h-3.5 w-3.5" />
          </button>
          <Type className="h-3.5 w-3.5 text-muted-foreground" />
          <button
            type="button"
            aria-label="Larger text"
            onClick={() => setFontSize((n) => Math.min(FONT_MAX, n + 1))}
            className="flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground hover:text-foreground"
          >
            <Plus className="h-3.5 w-3.5" />
          </button>
        </div>

        <button
          type="button"
          className="btn-ghost shrink-0"
          onClick={onClose}
          disabled={busy}
          title="Close the workspace and stop every agent in it"
        >
          <Power className="h-4 w-4" />
          Close
        </button>
      </div>

      {/* ------------------------------------------------------------- grid */}
      <div
        className="grid flex-1 gap-3 overflow-hidden p-3"
        style={{
          gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`,
          gridAutoRows: "1fr",
        }}
      >
        {session.terminals.map((term) => (
          <AgenticTerminal
            key={term.key}
            name={term.name}
            displayName={term.display_name}
            appearance={appearance}
            fontSize={fontSize}
            focused={target === term.name}
            onFocus={() => setTarget(term.name)}
            onStatus={(status, detail) => setStatus(term.name, status, detail)}
          />
        ))}
      </div>

      {/* -------------------------------------------------------- prompt bar */}
      <div className="border-t border-border px-4 py-3">
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <span className="text-[11px] uppercase tracking-wider text-muted-foreground">
            Send to
          </span>
          {session.terminals.map((term) => {
            const state = statuses[term.name];
            return (
              <button
                key={term.key}
                type="button"
                onClick={() => setTarget(term.name)}
                aria-pressed={target === term.name}
                className={cn(
                  "flex items-center gap-2 rounded-lg border px-2.5 py-1 text-xs transition-colors",
                  target === term.name
                    ? "border-primary/60 bg-primary/10 text-primary"
                    : "border-border text-muted-foreground hover:border-primary/40",
                )}
              >
                <span className="font-medium">{term.name}</span>
                <PaneStatusPill status={state?.status ?? "connecting"} detail={state?.detail} />
              </button>
            );
          })}
        </div>
        <div className="flex items-end gap-2">
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void send();
              }
            }}
            rows={2}
            placeholder={
              target
                ? `Type an instruction for ${target} — Enter sends, Shift+Enter adds a line`
                : "Pick a terminal first"
            }
            className="max-h-32 min-h-[52px] flex-1 resize-y rounded-lg border border-border bg-background/60 px-3 py-2 text-sm outline-none focus:border-primary/50"
          />
          <button
            type="button"
            className="btn-primary h-[52px] shrink-0"
            disabled={sending || !prompt.trim() || !target}
            onClick={() => void send()}
          >
            <ArrowUp className="h-4 w-4" />
            Send
          </button>
        </div>
        <p className="mt-2 text-[11px] text-muted-foreground">
          Anything you can type here, you can also say out loud — for example
          “what is {session.terminals[0]?.name ?? "Mika"} doing?” or “tell{" "}
          {session.terminals[0]?.name ?? "Mika"} to run the tests”.
        </p>
      </div>
    </div>
  );
}
