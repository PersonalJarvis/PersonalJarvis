import { useCallback, useEffect, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import {
  CloudDownload, ExternalLink, FolderGit2, GitCommitHorizontal, GitFork, GitMerge, GitPullRequest,
  Loader2, RefreshCw, Sparkles, Trash2, Upload, X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { openExternalUrl } from "@/lib/openExternal";
import {
  GitApiError, commitAll, fetchRemotes, folderName, inspectGit, mergeBack, openPullRequest, prepareGit,
  pruneWorktrees, pushBranch, removeWorktree, type GitRepoInfo, type GitWorktree,
} from "@/lib/gitApi";
import { GitStatusLine } from "./GitStatusLine";
import { fill, translate, useT } from "@/i18n";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The workspace folder the panel works on. */
  folder: string;
  workspace: string;
  /** Open an existing worktree as a workspace tab of its own. */
  onOpenWorktree: (tree: GitWorktree) => void;
  /** Start the "New workspace" flow preset to a fresh worktree. */
  onNewWorktree: () => void;
}

/** Git letters → a short, readable tag for the change list. */
function changeTag(index: string, worktree: string): string {
  const letter = index === "?" ? "?" : index !== "." ? index : worktree;
  const key = ({ M: "modified", A: "added", D: "deleted", R: "renamed", C: "copied", U: "conflict", "?": "new" } as Record<string, string>)[letter];
  return key ? translate(`ide_git.tag_${key}`) : letter;
}

type Busy = "" | "refresh" | "commit" | "push" | "fetch" | "pr" | "merge" | "prune" | "init" | `remove:${string}`;

/**
 * The workspace's Git panel: where the checkout stands, and the few actions
 * that finish an agent's work — commit, push, pull request, merge back — plus
 * worktree housekeeping. Every result is said in the panel itself.
 */
export function GitPanelDialog({ open, onOpenChange, folder, workspace, onOpenWorktree, onNewWorktree }: Props) {
  const t = useT();
  const [info, setInfo] = useState<GitRepoInfo | null>(null);
  const [busy, setBusy] = useState<Busy>("");
  const [notice, setNotice] = useState<{ tone: "ok" | "error"; text: string; url?: string } | null>(null);
  const [message, setMessage] = useState("");
  const [draft, setDraft] = useState(false);
  const [removing, setRemoving] = useState<{ tree: GitWorktree; deleteBranch: boolean; dirty: string } | null>(null);

  const refresh = useCallback(async () => {
    try { setInfo(await inspectGit(folder)); }
    catch (error) { setNotice({ tone: "error", text: (error as Error).message }); }
  }, [folder]);

  useEffect(() => {
    if (!open) return;
    setNotice(null); setRemoving(null);
    setBusy("refresh");
    void refresh().finally(() => setBusy(""));
  }, [open, refresh]);

  const act = async (kind: Busy, work: () => Promise<string | { text: string; url?: string } | void>) => {
    setBusy(kind); setNotice(null);
    try {
      const result = await work();
      if (typeof result === "string") setNotice({ tone: "ok", text: result });
      else if (result) setNotice({ tone: "ok", ...result });
      await refresh();
    } catch (error) {
      setNotice({ tone: "error", text: (error as Error).message });
    } finally { setBusy(""); }
  };

  const commit = () => act("commit", async () => {
    const { commit: sha } = await commitAll(folder, message.trim());
    setMessage("");
    return fill(t("ide_git.committed"), { sha });
  });
  const push = () => act("push", async () => fill(t("ide_git.pushed_to"), { upstream: (await pushBranch(folder)).upstream }));
  const fetchAll = () => act("fetch", async () => { await fetchRemotes(folder); return t("ide_git.fetched"); });
  const pr = () => act("pr", async () => {
    const { url } = await openPullRequest(folder, { draft });
    return { text: draft ? t("ide_git.draft_pr_opened") : t("ide_git.pr_opened"), url };
  });
  const merge = () => act("merge", async () => {
    const { commit: sha } = await mergeBack(folder);
    return fill(t("ide_git.merged"), { branch: info?.branch ?? "", target: info?.default_branch ?? "", sha });
  });
  const prune = () => act("prune", async () => { await pruneWorktrees(folder); return t("ide_git.pruned"); });
  const init = () => act("init", async () => { await prepareGit(folder, { mode: "init", branch: "", base: "" }); return t("ide_git.initialized"); });
  const remove = (force: boolean) => {
    if (!removing) return;
    const { tree, deleteBranch } = removing;
    void act(`remove:${tree.path}`, async () => {
      try {
        const { message: summary } = await removeWorktree(folder, tree.path, { force, deleteBranch });
        setRemoving(null);
        return summary;
      } catch (error) {
        // Uncommitted work: ask once more, explicitly, instead of failing.
        if (error instanceof GitApiError && error.code === "dirty") { setRemoving({ ...removing, dirty: error.message }); return; }
        throw error;
      }
    });
  };

  const linked = (info?.worktrees ?? []).filter((tree) => !tree.main);
  const prunable = linked.some((tree) => tree.prunable);
  const onFeatureBranch = Boolean(info?.branch && info.default_branch && info.branch !== info.default_branch);
  const button = "flex items-center justify-center gap-1.5 rounded-lg border border-border px-3 py-2 text-sm hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-40";
  const spin = (kind: Busy, Icon: typeof Upload) => busy === kind ? <Loader2 className="h-4 w-4 animate-spin" /> : <Icon className="h-4 w-4" />;
  const locked = busy !== "";

  return <Dialog.Root open={open} onOpenChange={(next) => { if (!locked || next) onOpenChange(next); }}>
    <Dialog.Portal>
      <Dialog.Overlay className="fixed inset-0 z-[80] bg-background/70 backdrop-blur-sm" />
      <Dialog.Content data-testid="git-panel" aria-busy={locked}
        className="fixed left-1/2 top-1/2 z-[90] flex max-h-[88vh] w-[min(560px,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-2xl border border-border bg-popover text-popover-foreground shadow-2xl">
        <header className="flex items-start justify-between gap-3 border-b border-border px-6 py-4">
          <div className="min-w-0">
            <Dialog.Title className="text-lg font-semibold">Git</Dialog.Title>
            <Dialog.Description className="mt-0.5 truncate text-sm text-muted-foreground" title={folder}>{workspace} · {folderName(folder)}</Dialog.Description>
          </div>
          <div className="flex items-center gap-1">
            <button type="button" aria-label={t("ide_git.refresh")} title={t("ide_git.refresh")} disabled={locked} onClick={() => { setBusy("refresh"); void refresh().finally(() => setBusy("")); }}
              className="rounded-lg p-2 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-40"><RefreshCw className={cn("h-4 w-4", busy === "refresh" && "animate-spin")} /></button>
            <Dialog.Close aria-label={t("ide_git.close_panel")} className="rounded-lg p-2 text-muted-foreground hover:bg-muted hover:text-foreground"><X className="h-4 w-4" /></Dialog.Close>
          </div>
        </header>

        <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-6 py-5">
          {notice && <div role={notice.tone === "error" ? "alert" : "status"}
            className={cn("rounded-lg border px-3 py-2 text-sm", notice.tone === "error" ? "border-destructive/40 bg-destructive/10 text-destructive" : "border-border bg-muted/40")}>
            {notice.text}
            {notice.url && <button type="button" aria-label={t("ide_git.open_link")} onClick={() => void openExternalUrl(notice.url!)} className="ml-2 inline-flex items-center gap-1 font-medium underline underline-offset-2">{t("ide_git.open")} <ExternalLink className="h-3 w-3" /></button>}
          </div>}

          {!info ? <p className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />{t("ide_git.reading_repo")}</p>
          : !info.git_available ? <p className="text-sm text-muted-foreground">{t("ide_git.not_installed_refresh")}</p>
          : !info.is_repo ? <div className="space-y-3">
            <p className="text-sm text-muted-foreground">{t("ide_git.not_a_repo")}</p>
            <button type="button" disabled={locked} onClick={init} className={button}>{spin("init", Sparkles)}{t("ide_git.init")}</button>
          </div>
          : <>
            <section aria-label={t("common.status")} className="space-y-2">
              <GitStatusLine info={info} className="text-sm" />
              {info.is_worktree && (() => {
                const [before, after = ""] = t("ide_git.worktree_of").split("{folder}");
                return <p className="text-xs text-muted-foreground">{before}<span className="font-mono">{folderName(info.main_root)}</span>{after}</p>;
              })()}
            </section>

            <section aria-label={t("ide_git.changes")} className="space-y-2.5">
              <h3 className="text-sm font-medium">{t("ide_git.changes")}</h3>
              {info.changes.length === 0 ? <p className="text-xs text-muted-foreground">{t("ide_git.nothing_uncommitted_full")}</p>
              : <ul className="max-h-40 space-y-0.5 overflow-y-auto rounded-lg border border-border bg-muted/30 p-2">
                {info.changes.map((change) => <li key={`${change.path}-${change.index}${change.worktree}`} className="flex items-center gap-2 text-xs">
                  <span className={cn("w-16 shrink-0 text-[10px] uppercase tracking-wide", change.index === "U" ? "text-destructive" : "text-muted-foreground")}>{changeTag(change.index, change.worktree)}</span>
                  <span className="truncate font-mono" title={change.path}>{change.path}</span>
                </li>)}
              </ul>}
              <form className="flex gap-2" onSubmit={(event) => { event.preventDefault(); void commit(); }}>
                <input value={message} onChange={(event) => setMessage(event.target.value)} disabled={locked || !info.dirty} aria-label={t("ide_git.commit_message")}
                  placeholder={info.dirty ? t("ide_git.commit_placeholder") : t("ide_git.no_changes")}
                  className="h-9 min-w-0 flex-1 rounded-lg border border-input bg-background px-3 text-sm outline-none focus:border-ring focus:ring-1 focus:ring-ring/30 disabled:opacity-50" />
                <button type="submit" disabled={locked || !info.dirty || !message.trim()} className={button}>{spin("commit", GitCommitHorizontal)}{t("ide_git.commit_all")}</button>
              </form>
            </section>

            <section aria-label={t("ide_git.share")} className="space-y-2.5">
              <h3 className="text-sm font-medium">{t("ide_git.share")}</h3>
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                <button type="button" disabled={locked || !info.branch || info.remotes.length === 0} onClick={push} className={button}
                  title={info.remotes.length === 0 ? t("ide_git.no_remote") : undefined}>{spin("push", Upload)}{info.upstream ? t("ide_git.push") : t("ide_git.publish_branch")}</button>
                <button type="button" disabled={locked || info.remotes.length === 0} onClick={fetchAll} className={button}>{spin("fetch", CloudDownload)}{t("ide_git.fetch")}</button>
                <button type="button" disabled={locked || !info.gh_available || !onFeatureBranch || info.remotes.length === 0} onClick={pr} className={button}
                  title={!info.gh_available ? t("ide_git.install_gh") : !onFeatureBranch ? t("ide_git.switch_feature_branch") : undefined}>{spin("pr", GitPullRequest)}{t("ide_git.pull_request")}</button>
              </div>
              <label className="flex items-center gap-2 text-xs text-muted-foreground">
                <input type="checkbox" checked={draft} onChange={(event) => setDraft(event.target.checked)} className="accent-primary" />{t("ide_git.drafts")}
              </label>
              {onFeatureBranch && <button type="button" disabled={locked || info.dirty} onClick={merge} className={cn(button, "w-full")}
                title={info.dirty ? t("ide_git.commit_first") : undefined}>{spin("merge", GitMerge)}{fill(t("ide_git.merge_into"), { branch: info.branch, target: info.default_branch })}</button>}
            </section>

            <section aria-label={t("ide_git.worktrees")} className="space-y-2.5">
              <div className="flex items-center justify-between gap-2">
                <h3 className="text-sm font-medium">{t("ide_git.worktrees")}</h3>
                <div className="flex gap-1.5">
                  {prunable && <button type="button" disabled={locked} onClick={prune} className="rounded-md px-2 py-1 text-xs text-muted-foreground hover:bg-muted">{busy === "prune" ? t("ide_git.pruning") : t("ide_git.prune_missing")}</button>}
                  <button type="button" disabled={locked || info.unborn} onClick={() => { onOpenChange(false); onNewWorktree(); }}
                    className="flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium hover:bg-muted disabled:opacity-40"><GitFork className="h-3.5 w-3.5" />{t("ide_git.new_worktree")}</button>
                </div>
              </div>
              {linked.length === 0 ? <p className="text-xs text-muted-foreground">{t("ide_git.no_worktrees")}</p>
              : <ul className="space-y-1.5">
                {linked.map((tree) => <li key={tree.path} className="rounded-lg border border-border px-3 py-2">
                  <div className="flex items-center gap-2">
                    <FolderGit2 className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate font-mono text-xs text-foreground">{tree.branch || fill(t("ide_git.detached_head"), { head: tree.head })}</span>
                      <span className="block truncate text-[11px] text-muted-foreground" title={tree.path}>{folderName(tree.path)}
                        {tree.current && ` · ${t("ide_git.this_workspace")}`}{tree.prunable && ` · ${t("ide_git.folder_missing")}`}{tree.locked && ` · ${t("ide_git.locked")}`}</span>
                    </span>
                    {!tree.current && !tree.prunable && <button type="button" disabled={locked} onClick={() => { onOpenChange(false); onOpenWorktree(tree); }}
                      className="rounded-md px-2 py-1 text-xs hover:bg-muted">{t("ide_git.open")}</button>}
                    {!tree.current && <button type="button" disabled={locked} aria-label={fill(t("ide_git.remove_named"), { folder: folderName(tree.path) })}
                      onClick={() => setRemoving({ tree, deleteBranch: false, dirty: "" })}
                      className="rounded-md p-1.5 text-muted-foreground hover:bg-muted hover:text-destructive"><Trash2 className="h-3.5 w-3.5" /></button>}
                  </div>
                  {removing?.tree.path === tree.path && <div role="group" aria-label={t("ide_git.confirm_removal")} className="mt-2 space-y-2 border-t border-border pt-2">
                    <p className="text-xs text-muted-foreground">{removing.dirty || t("ide_git.remove_note")}</p>
                    {tree.branch && <label className="flex items-center gap-2 text-xs text-muted-foreground">
                      <input type="checkbox" checked={removing.deleteBranch} onChange={(event) => setRemoving({ ...removing, deleteBranch: event.target.checked })} className="accent-primary" />
                      {(() => {
                        const [before, after = ""] = t("ide_git.also_delete_branch").split("{branch}");
                        return <>{before}<span className="font-mono">{tree.branch}</span>{after}</>;
                      })()}</label>}
                    <div className="flex justify-end gap-2">
                      <button type="button" onClick={() => setRemoving(null)} className="rounded-md px-2.5 py-1 text-xs text-muted-foreground hover:bg-muted">{t("common.cancel")}</button>
                      <button type="button" disabled={locked} onClick={() => remove(Boolean(removing.dirty))}
                        className="rounded-md bg-destructive px-2.5 py-1 text-xs font-medium text-destructive-foreground disabled:opacity-50">
                        {busy === `remove:${tree.path}` ? t("ide_git.removing") : removing.dirty ? t("ide_git.remove_anyway") : t("ide_git.remove_worktree")}</button>
                    </div>
                  </div>}
                </li>)}
              </ul>}
            </section>
          </>}
        </div>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}
