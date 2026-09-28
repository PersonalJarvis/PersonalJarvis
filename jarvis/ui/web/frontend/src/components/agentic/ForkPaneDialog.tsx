import { useEffect, useRef, useState, type ComponentType } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { FolderGit2, Loader2, MessagesSquare } from "lucide-react";
import { AgentMark } from "./AgentMark";
import { BranchIcon } from "./branchIcon";
import { fetchForkSuggestion, type ForkSuggestion } from "@/lib/agenticIdeApi";
import { cn } from "@/lib/utils";

/** The pane a fork is being made of. */
export interface ForkSource {
  name: string;
  agent: string;
  displayName: string;
  workspaceId?: string;
}

export type ForkMode = "chat" | "worktree";

interface Props {
  source: ForkSource | null;
  busy: boolean;
  onCancel: () => void;
  onConfirm: (choice: { mode: ForkMode; branch: string }) => void;
}

/**
 * Fork a pane: a new agent beside it that continues a copy of its chat —
 * either in the same folder, or in a fresh git worktree on its own branch.
 * The worktree name is pre-filled with a free name derived from the pane
 * (call-sign plus what it is working on), and stays editable.
 */
export function ForkPaneDialog({ source, busy, onCancel, onConfirm }: Props) {
  const [mode, setMode] = useState<ForkMode>("chat");
  const [branch, setBranch] = useState("");
  const [info, setInfo] = useState<ForkSuggestion | null>(null);
  const [loadError, setLoadError] = useState("");
  const nameRef = useRef<HTMLInputElement>(null);
  const sourceName = source?.name;
  const sourceWorkspace = source?.workspaceId;

  useEffect(() => {
    setMode("chat");
    setBranch("");
    setInfo(null);
    setLoadError("");
    if (!sourceName) return;
    let live = true;
    fetchForkSuggestion(sourceName, sourceWorkspace)
      .then((next) => {
        if (!live) return;
        setInfo(next);
        setBranch(next.name);
      })
      .catch((error: unknown) => {
        if (live) setLoadError(error instanceof Error ? error.message : String(error));
      });
    return () => { live = false; };
  }, [sourceName, sourceWorkspace]);

  useEffect(() => {
    if (mode === "worktree") nameRef.current?.select();
  }, [mode]);

  const worktreeOff = info !== null && !info.in_repo;
  const trimmed = branch.trim();
  const canSubmit = !busy && (mode === "chat" || (trimmed !== "" && !worktreeOff && info !== null));
  const submit = () => { if (canSubmit) onConfirm({ mode, branch: trimmed }); };

  const options: { mode: ForkMode; title: string; body: string; Icon: ComponentType<{ className?: string }>; disabled: boolean }[] = [
    {
      mode: "chat",
      title: "Fork chat",
      body: "A new agent opens beside this one and continues a copy of the conversation, in the same folder.",
      Icon: MessagesSquare,
      disabled: false,
    },
    {
      mode: "worktree",
      title: "Fork into a worktree",
      body: worktreeOff
        ? "Needs a git repository — this folder is not one."
        : "Same copy of the conversation, but in its own git worktree and branch, so both agents can change files without getting in each other's way.",
      Icon: FolderGit2,
      disabled: worktreeOff,
    },
  ];

  return <Dialog.Root open={source !== null} onOpenChange={(open) => { if (!open && !busy) onCancel(); }}>
    <Dialog.Portal>
      <Dialog.Overlay className="fixed inset-0 z-[80] bg-background/70 backdrop-blur-sm data-[state=open]:animate-in data-[state=open]:fade-in-0" />
      <Dialog.Content data-testid="fork-pane-dialog"
        className="fixed left-1/2 top-1/2 z-[90] w-[min(460px,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 rounded-2xl border border-border bg-popover p-6 text-popover-foreground shadow-2xl data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95">
        <form onSubmit={(event) => { event.preventDefault(); submit(); }}>
          <div className="flex items-start gap-4">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary">
              <BranchIcon className="h-[18px] w-[18px]" />
            </div>
            <div className="min-w-0 flex-1">
              <Dialog.Title className="truncate text-base font-semibold">Fork {source?.name}</Dialog.Title>
              <Dialog.Description className="mt-1.5 text-sm leading-relaxed text-muted-foreground">
                Start a second agent from where this one is. The original keeps running unchanged.
              </Dialog.Description>
            </div>
          </div>

          {source && <div className="mt-4 flex items-center gap-2.5 rounded-xl border border-border bg-muted/40 px-3 py-2.5">
            <AgentMark agent={source.agent} label={source.displayName} variant="plain" size="sm" />
            <span className="truncate text-sm font-medium">{source.displayName}</span>
            <span className="ml-auto shrink-0 font-mono text-xs text-muted-foreground">{source.name}</span>
          </div>}

          <div role="radiogroup" aria-label="How to fork" className="mt-4 grid gap-2">
            {options.map((option) => {
              const checked = mode === option.mode;
              return <button key={option.mode} type="button" role="radio" aria-checked={checked}
                data-testid={`fork-mode-${option.mode}`} disabled={option.disabled || busy}
                onClick={() => setMode(option.mode)}
                className={cn(
                  "flex items-start gap-3 rounded-xl border px-3 py-3 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50",
                  checked ? "border-primary bg-primary/5" : "border-border hover:bg-muted/60",
                )}>
                <option.Icon className={cn("mt-0.5 h-4 w-4 shrink-0", checked ? "text-primary" : "text-muted-foreground")} aria-hidden />
                <span className="min-w-0">
                  <span className="block text-sm font-medium">{option.title}</span>
                  <span className="mt-0.5 block text-xs leading-relaxed text-muted-foreground">{option.body}</span>
                </span>
              </button>;
            })}
          </div>

          {mode === "worktree" && <div className="mt-4">
            <label htmlFor="fork-branch-name" className="text-xs font-medium text-muted-foreground">Worktree and branch name</label>
            <input id="fork-branch-name" ref={nameRef} data-testid="fork-branch-name" value={branch} disabled={busy || info === null}
              onChange={(event) => setBranch(event.target.value)} spellCheck={false} autoComplete="off" maxLength={80}
              placeholder={info === null ? "Finding a name…" : undefined}
              className="mt-1.5 w-full rounded-lg border border-border bg-background px-3 py-2 font-mono text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60" />
            <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">
              Created from the last commit. Changes that are not committed yet stay in the current folder.
            </p>
          </div>}

          {info && !info.can_fork && <p className="mt-4 text-xs leading-relaxed text-muted-foreground">
            {source?.displayName} cannot copy its conversation, so the fork starts with a fresh chat.
          </p>}
          {loadError && <p role="alert" className="mt-4 text-xs text-destructive">{loadError}</p>}

          <div className="mt-6 flex justify-end gap-2">
            <button type="button" disabled={busy} onClick={onCancel}
              className="rounded-lg border border-border px-3.5 py-2 text-sm font-medium hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-40">
              Cancel</button>
            <button type="submit" data-testid="fork-confirm" disabled={!canSubmit}
              className="flex items-center gap-1.5 rounded-lg bg-primary px-3.5 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-popover disabled:opacity-50">
              {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />}
              {mode === "worktree" ? "Fork into worktree" : "Fork chat"}</button>
          </div>
        </form>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}
