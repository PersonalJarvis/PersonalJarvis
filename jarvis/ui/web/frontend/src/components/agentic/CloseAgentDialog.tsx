import * as Dialog from "@radix-ui/react-dialog";
import { Loader2, Power } from "lucide-react";
import { AgentMark } from "./AgentMark";

/** What is about to close: one pane, or a whole workspace with its panes. */
export type CloseTarget =
  | { kind: "terminal"; name: string; agent: string; displayName: string }
  | { kind: "workspace"; name: string; agents: { agent: string; displayName: string }[] };

interface Props {
  target: CloseTarget | null;
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}

/**
 * In-app replacement for `window.confirm` when a pane or workspace closes.
 * The desktop shell renders a native confirm as an unstyled host-named box
 * ("127.0.0.1:47821 says") in the OS language, and it blocks the event loop.
 */
export function CloseAgentDialog({ target, busy, onCancel, onConfirm }: Props) {
  const terminal = target?.kind === "terminal" ? target : null;
  const workspace = target?.kind === "workspace" ? target : null;
  const count = workspace?.agents.length ?? 1;
  const title = terminal ? `Close ${terminal.name}?` : `Close ${workspace?.name ?? "workspace"}?`;
  const body = terminal
    ? `The coding agent in this pane stops right away, including any task it is still working on.`
    : count === 0
      ? "The workspace closes. You can restore it from Projects at any time."
      : `${count === 1 ? "Its coding agent stops" : `All ${count} coding agents stop`} right away. You can restore the workspace from Projects at any time.`;

  return <Dialog.Root open={target !== null} onOpenChange={(open) => { if (!open && !busy) onCancel(); }}>
    <Dialog.Portal>
      <Dialog.Overlay className="fixed inset-0 z-[80] bg-background/70 backdrop-blur-sm data-[state=open]:animate-in data-[state=open]:fade-in-0" />
      <Dialog.Content data-testid="close-agent-dialog"
        onOpenAutoFocus={(event) => { event.preventDefault(); (event.currentTarget as HTMLElement).querySelector<HTMLElement>("[data-close-cancel]")?.focus(); }}
        className="fixed left-1/2 top-1/2 z-[90] w-[min(400px,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 rounded-2xl border border-border bg-popover p-6 text-popover-foreground shadow-2xl data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95">
        <div className="flex items-start gap-4">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-destructive/10 text-destructive">
            <Power className="h-[18px] w-[18px]" aria-hidden />
          </div>
          <div className="min-w-0 flex-1">
            <Dialog.Title className="truncate text-base font-semibold">{title}</Dialog.Title>
            <Dialog.Description className="mt-1.5 text-sm leading-relaxed text-muted-foreground">{body}</Dialog.Description>
          </div>
        </div>

        {terminal && <div className="mt-4 flex items-center gap-2.5 rounded-xl border border-border bg-muted/40 px-3 py-2.5">
          <AgentMark agent={terminal.agent} label={terminal.displayName} variant="plain" size="sm" />
          <span className="truncate text-sm font-medium">{terminal.displayName}</span>
          <span className="ml-auto shrink-0 font-mono text-xs text-muted-foreground">{terminal.name}</span>
        </div>}
        {workspace && workspace.agents.length > 0 && <div className="mt-4 flex flex-wrap gap-1.5">
          {workspace.agents.map((entry, index) => <span key={`${entry.agent}-${index}`}
            className="flex items-center gap-1.5 rounded-lg border border-border bg-muted/40 px-2 py-1 text-xs text-muted-foreground">
            <AgentMark agent={entry.agent} label={entry.displayName} variant="plain" size="sm" />{entry.displayName}
          </span>)}
        </div>}

        <div className="mt-6 flex justify-end gap-2">
          <button type="button" data-close-cancel disabled={busy} onClick={onCancel}
            className="rounded-lg border border-border px-3.5 py-2 text-sm font-medium hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-40">
            Keep running</button>
          <button type="button" disabled={busy} onClick={onConfirm}
            className="flex items-center gap-1.5 rounded-lg bg-destructive px-3.5 py-2 text-sm font-medium text-destructive-foreground hover:bg-destructive/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-destructive focus-visible:ring-offset-2 focus-visible:ring-offset-popover disabled:opacity-60">
            {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />}
            {terminal ? "Close agent" : "Close workspace"}</button>
        </div>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}
