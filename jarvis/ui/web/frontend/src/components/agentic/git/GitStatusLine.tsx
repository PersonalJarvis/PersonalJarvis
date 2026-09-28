import { ArrowDown, ArrowUp, GitBranch } from "lucide-react";
import { cn } from "@/lib/utils";
import type { GitRepoInfo } from "@/lib/gitApi";

/** Branch · changes · ahead/behind · +/- lines, in one compact row. */
export function GitStatusLine({ info, className }: { info: GitRepoInfo; className?: string }) {
  const changes = info.staged + info.unstaged + info.untracked + info.conflicted;
  return <span data-testid="git-status-line" className={cn("flex flex-wrap items-center gap-x-2.5 gap-y-1 text-xs text-muted-foreground", className)}>
    <span className="flex items-center gap-1 font-mono text-foreground" title={info.detached ? `Detached at ${info.head}` : "Current branch"}>
      <GitBranch className="h-3.5 w-3.5" aria-hidden />{info.detached ? info.head : info.branch || "—"}
    </span>
    {info.is_worktree && <span className="rounded-full border border-border px-1.5 text-[10px]">worktree</span>}
    <span className="flex items-center gap-1" title={changes ? `${changes} uncommitted` : "Nothing uncommitted"}>
      <span className={cn("h-1.5 w-1.5 rounded-full", info.conflicted ? "bg-destructive" : changes ? "bg-warning" : "bg-success")} aria-hidden />
      {changes ? `${changes} ${changes === 1 ? "change" : "changes"}` : "clean"}
    </span>
    {(info.insertions > 0 || info.deletions > 0) && <span className="font-mono">
      <span className="text-success">+{info.insertions}</span>{" "}
      <span className="text-destructive">−{info.deletions}</span>
    </span>}
    {info.upstream ? <span className="flex items-center gap-0.5 font-mono" title={`Compared with ${info.upstream}`}>
      <ArrowUp className="h-3 w-3" aria-label="ahead" />{info.ahead}<ArrowDown className="ml-1 h-3 w-3" aria-label="behind" />{info.behind}
    </span> : info.branch && <span title="This branch has not been pushed yet">not pushed</span>}
  </span>;
}
