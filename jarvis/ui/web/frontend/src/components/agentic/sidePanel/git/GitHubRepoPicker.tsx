import { useEffect, useMemo, useState } from "react";
import { Check, Github, Loader2, Lock, Search, X } from "lucide-react";
import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";
import { bindGitHubRepo, fetchGitHubRepos, type GitHubRepoChoice, type GitHubRepoList } from "./gitOverviewApi";

/** No GitHub credential at all: one button to the GitHub connection. */
export function ConnectGitHubCard() {
  const t = useT();
  const setActiveSection = useEventStore((state) => state.setActiveSection);
  return (
    <div data-testid="git-connect-github" className="mx-3 my-3 space-y-2 rounded-lg border border-border/60 bg-muted/30 p-3">
      <p className="text-[12.5px] font-medium text-foreground">{t("ide_side_panel.git.connect_title")}</p>
      <p className="text-[11.5px] text-muted-foreground">{t("ide_side_panel.git.connect_body")}</p>
      <button
        type="button"
        onClick={() => setActiveSection("plugins")}
        className="inline-flex h-8 items-center gap-1.5 rounded-md bg-primary px-3 text-xs font-medium text-primary-foreground hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <Github className="h-3.5 w-3.5" aria-hidden />
        {t("ide_side_panel.git.connect_button")}
      </button>
    </div>
  );
}

/**
 * "Which of your GitHub repositories is this folder?" — asked once per
 * folder. The repository the folder's git remote points at comes first,
 * marked as the recommendation; one click remembers the choice.
 */
export function GitHubRepoPicker({
  workspaceId,
  current,
  suggested,
  onPicked,
  onCancel,
}: {
  workspaceId: string;
  /** The repository picked before, when the picker is reopened to change it. */
  current: string;
  suggested: string;
  /** The choice is saved; called with the picked `owner/name`. */
  onPicked: (repo: string) => void;
  /** Present when there is already a choice to go back to. */
  onCancel?: () => void;
}) {
  const t = useT();
  const [list, setList] = useState<GitHubRepoList | null>(null);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("");
  const [saving, setSaving] = useState("");

  useEffect(() => {
    let alive = true;
    fetchGitHubRepos()
      .then((next) => alive && setList(next))
      .catch((err: Error) => alive && setError(err.message));
    return () => {
      alive = false;
    };
  }, []);

  const repos = useMemo(() => {
    const all = list?.repos ?? [];
    // The folder's own remote leads, even when the list does not carry it
    // (an organisation repository the account can read but is not a member of).
    const lead: GitHubRepoChoice[] =
      suggested && !all.some((repo) => repo.name.toLowerCase() === suggested.toLowerCase())
        ? [{ name: suggested, description: "", private: false, fork: false, url: "", pushed_at: "" }]
        : [];
    const ordered = [...lead, ...all].sort(
      (a, b) => Number(b.name.toLowerCase() === suggested.toLowerCase()) - Number(a.name.toLowerCase() === suggested.toLowerCase()),
    );
    const needle = filter.trim().toLowerCase();
    return needle ? ordered.filter((repo) => repo.name.toLowerCase().includes(needle)) : ordered;
  }, [list, suggested, filter]);

  const pick = async (name: string) => {
    setSaving(name);
    setError("");
    try {
      await bindGitHubRepo(workspaceId, name);
      onPicked(name);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSaving("");
    }
  };

  if (list && !list.connected) return <ConnectGitHubCard />;

  return (
    <div data-testid="git-repo-picker" className="flex min-h-0 flex-1 flex-col">
      <div className="shrink-0 space-y-2 px-3 pb-2 pt-3">
        <div className="flex items-start gap-2">
          <div className="min-w-0 flex-1">
            <p className="text-[12.5px] font-medium text-foreground">{t("ide_side_panel.git.connect_repo_title")}</p>
            <p className="text-[11.5px] text-muted-foreground">
              {list?.login
                ? fill(t("ide_side_panel.git.pick_body_login"), { login: list.login })
                : t("ide_side_panel.git.pick_body")}
            </p>
          </div>
          {onCancel && (
            <button
              type="button"
              onClick={onCancel}
              aria-label={t("ide_side_panel.git.pick_cancel")}
              className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-secondary hover:text-foreground"
            >
              <X className="h-3.5 w-3.5" aria-hidden />
            </button>
          )}
        </div>
        <label className="flex h-8 items-center gap-2 rounded-lg border border-border/60 bg-background/50 px-2.5 focus-within:border-ring">
          <Search className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
          <input
            autoFocus
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
            placeholder={t("ide_side_panel.git.pick_search")}
            aria-label={t("ide_side_panel.git.pick_search")}
            className="min-w-0 flex-1 bg-transparent text-xs text-foreground outline-none placeholder:text-muted-foreground"
          />
        </label>
        {error && <p className="text-[11px] text-destructive">{error}</p>}
        {list?.reason && <p className="text-[11px] text-destructive">{list.reason}</p>}
      </div>
      <ul className="min-h-0 flex-1 overflow-y-auto pb-2" aria-label={t("ide_side_panel.git.pick_title")}>
        {!list && !error && (
          <li className="flex items-center justify-center gap-2 px-4 py-6 text-xs text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
            {t("ide_side_panel.git.pick_loading")}
          </li>
        )}
        {list && repos.length === 0 && (
          <li className="px-4 py-6 text-center text-xs text-muted-foreground">{t("ide_side_panel.git.pick_empty")}</li>
        )}
        {repos.map((repo) => {
          const recommended = suggested !== "" && repo.name.toLowerCase() === suggested.toLowerCase();
          const chosen = current !== "" && repo.name.toLowerCase() === current.toLowerCase();
          return (
            <li key={repo.name}>
              <button
                type="button"
                data-testid="git-repo-choice"
                data-repo={repo.name}
                disabled={saving !== ""}
                onClick={() => void pick(repo.name)}
                className={cn(
                  "flex w-full items-start gap-2 px-3 py-1.5 text-left hover:bg-muted/60 focus-visible:bg-muted/60 focus-visible:outline-none disabled:opacity-60",
                  recommended && "bg-accent/10",
                )}
              >
                {saving === repo.name ? (
                  <Loader2 className="mt-0.5 h-3.5 w-3.5 shrink-0 animate-spin text-muted-foreground" aria-hidden />
                ) : chosen ? (
                  <Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-accent" aria-hidden />
                ) : (
                  <Github className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
                )}
                <span className="flex min-w-0 flex-1 flex-col leading-tight">
                  <span className="flex min-w-0 items-center gap-1.5">
                    <span className="truncate font-mono text-[12px] text-foreground">{repo.name}</span>
                    {repo.private && <Lock className="h-3 w-3 shrink-0 text-muted-foreground" aria-label={t("ide_side_panel.git.pick_private")} />}
                    {recommended && (
                      <span className="shrink-0 rounded bg-accent/15 px-1 text-[9.5px] font-medium uppercase tracking-wide text-accent">
                        {t("ide_side_panel.git.pick_recommended")}
                      </span>
                    )}
                  </span>
                  {recommended ? (
                    <span className="text-[10.5px] text-muted-foreground">{t("ide_side_panel.git.pick_recommended_why")}</span>
                  ) : (
                    repo.description && <span className="truncate text-[10.5px] text-muted-foreground">{repo.description}</span>
                  )}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
