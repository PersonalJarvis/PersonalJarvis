import { ArrowDown, ArrowUp, GitBranch } from "lucide-react";
import { cn } from "@/lib/utils";
import type { GitRepoInfo } from "@/lib/gitApi";
import { fill, useT } from "@/i18n";

/** Branch · changes · ahead/behind · +/- lines, in one compact row. */
export function GitStatusLine({ info, className }: { info: GitRepoInfo; className?: string }) {
  const t = useT();
  const changes = info.staged + info.unstaged + info.untracked + info.conflicted;
  return <span data-testid="git-status-line" className={cn("flex flex-wrap items-center gap-x-2.5 gap-y-1 text-xs text-muted-foreground", className)}>
    <span className="flex items-center gap-1 font-mono text-foreground" title={info.detached ? fill(t("ide_git.detached_at"), { head: info.head }) : t("ide_git.current_branch")}>
      <GitBranch className="h-3.5 w-3.5" aria-hidden />{info.detached ? info.head : info.branch || "—"}
    </span>
    {info.is_worktree && <span className="rounded-full border border-border px-1.5 text-[10px]">{t("ide_git.worktree_badge")}</span>}
    <span className="flex items-center gap-1" title={changes ? fill(t("ide_git.uncommitted"), { count: changes }) : t("ide_git.nothing_uncommitted")}>
      <span className={cn("h-1.5 w-1.5 rounded-full", info.conflicted ? "bg-destructive" : changes ? "bg-warning" : "bg-accent")} aria-hidden />
      {changes ? fill(t(changes === 1 ? "ide_git.changes_one" : "ide_git.changes_other"), { count: changes }) : t("ide_git.clean")}
    </span>
    {(info.insertions > 0 || info.deletions > 0) && <span className="font-mono">
      <span className="text-success">+{info.insertions}</span>{" "}
      <span className="text-destructive">−{info.deletions}</span>
    </span>}
    {info.upstream ? <span className="flex items-center gap-0.5 font-mono" title={fill(t("ide_git.compared_with"), { upstream: info.upstream })}>
      <ArrowUp className="h-3 w-3" aria-label={t("ide_git.ahead")} />{info.ahead}<ArrowDown className="ml-1 h-3 w-3" aria-label={t("ide_git.behind")} />{info.behind}
    </span> : info.branch && <span title={t("ide_git.not_pushed_title")}>{t("ide_git.not_pushed")}</span>}
  </span>;
}
