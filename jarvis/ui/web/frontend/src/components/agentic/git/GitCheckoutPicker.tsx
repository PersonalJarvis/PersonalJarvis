import { useEffect, useMemo, useState } from "react";
import { ChevronDown, Dices, FolderGit2, FolderTree, GitBranch, GitBranchPlus, GitFork, Loader2, Sparkles } from "lucide-react";
import { cn } from "@/lib/utils";
import { BrandedSelect } from "@/components/ui/select";
import { folderName, inspectGit, type GitPlan, type GitPrepareMode, type GitRepoInfo } from "@/lib/gitApi";
import { GitStatusLine } from "./GitStatusLine";

interface Props {
  /** The folder the workspace/agent would open in. */
  folder: string;
  value: GitPlan;
  onChange: (plan: GitPlan) => void;
  disabled?: boolean;
  /**
   * `workspace`: every option, including changing the branch in place.
   * `agent`: share the workspace's checkout, or give the agent a worktree of
   * its own — which opens as its own workspace tab.
   */
  context: "workspace" | "agent";
  /** Receives the inspection, so the caller can label its submit button. */
  onInfo?: (info: GitRepoInfo | null) => void;
}

interface Option { mode: GitPrepareMode; title: string; hint: string; Icon: typeof GitBranch; recommended?: boolean }

/** A fresh suggestion that is not the one already offered. */
function reroll(current: string): string {
  const adjectives = ["amber", "brave", "calm", "cosmic", "eager", "golden", "keen", "lucky", "nimble", "swift", "vivid"];
  const nouns = ["comet", "delta", "ember", "falcon", "harbor", "meadow", "orbit", "otter", "river", "summit", "willow"];
  for (;;) {
    const pick = (list: string[]) => list[Math.floor(Math.random() * list.length)];
    const hex = Math.floor(Math.random() * 0xffff).toString(16).padStart(4, "0");
    const name = `agent/${pick(adjectives)}-${pick(nouns)}-${hex}`;
    if (name !== current) return name;
  }
}

/**
 * The git half of "New workspace" and "Add coding agent": keep the checkout,
 * branch, or give the agents a worktree of their own. Reads the folder once
 * and shows where things stand (branch, changes, ahead/behind) beside the choice.
 */
export function GitCheckoutPicker({ folder, value, onChange, disabled, context, onInfo }: Props) {
  const [info, setInfo] = useState<GitRepoInfo | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  // Keeping the checkout is the default and needs no decision, so the choices
  // stay folded behind one summary line until someone asks to change it.
  const [unfolded, setUnfolded] = useState(false);
  const expanded = unfolded || value.mode !== "current";

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError("");
    inspectGit(folder, controller.signal)
      .then((next) => { setInfo(next); onInfo?.(next); })
      .catch((reason: Error) => { if (!controller.signal.aborted) { setInfo(null); onInfo?.(null); setError(reason.message); } })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
    // onInfo is a callback prop; re-inspecting when it changes identity would loop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [folder]);

  const linked = useMemo(() => (info?.worktrees ?? []).filter((tree) => !tree.main && !tree.prunable && !tree.current), [info]);
  const switchable = useMemo(() => (info?.branches ?? []).filter((branch) => !branch.current && !branch.worktree), [info]);
  const bases = useMemo(() => {
    if (!info) return [];
    const local = info.branches.map((branch) => branch.name);
    const ordered = [info.branch, info.default_branch, ...local].filter(Boolean);
    return [...new Set([...ordered, ...info.remote_branches])];
  }, [info]);

  // A plan preset from outside ("New workspace in a worktree") learns its
  // branch name and base once the repository is read; a mode this folder
  // cannot offer (a worktree of a plain folder) falls back to keeping it.
  useEffect(() => {
    if (!info || !info.git_available) return;
    const creates = value.mode === "new_worktree" || value.mode === "new_branch";
    if (creates && !info.is_repo) onChange({ mode: "current", branch: "", base: "" });
    else if (creates && !value.branch) onChange({ ...value, branch: info.suggested_branch, base: value.base || info.branch });
    // Only when the inspection lands — not on every keystroke in the branch field.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [info]);

  const options: Option[] = !info?.is_repo
    ? [
      { mode: "current", title: "Without git", hint: "Open the folder as it is", Icon: FolderTree, recommended: true },
      { mode: "init", title: "Initialize git", hint: "New repository on main, ready for branches", Icon: Sparkles },
    ]
    : [
      context === "agent"
        ? { mode: "current", title: "Share this checkout", hint: `Same folder as the other agents${info.branch ? ` · ${info.branch}` : ""}`, Icon: FolderTree, recommended: true }
        : { mode: "current", title: "Current checkout", hint: info.branch ? `Work on ${info.branch} in the project folder` : "Work in the project folder", Icon: FolderTree, recommended: true },
      { mode: "new_worktree", title: "New worktree", hint: context === "agent" ? "Own folder and branch, opens as its own tab" : "Own folder and branch, agents never collide", Icon: GitFork },
      ...(context === "workspace" ? [
        { mode: "new_branch" as const, title: "New branch", hint: "Branch off here, in the project folder", Icon: GitBranchPlus },
        ...(switchable.length ? [{ mode: "switch_branch" as const, title: "Existing branch", hint: "Check out another branch here", Icon: GitBranch }] : []),
      ] : []),
      ...(linked.length ? [{ mode: "open_worktree" as const, title: "Existing worktree", hint: "Open a worktree that is already there", Icon: FolderGit2 }] : []),
    ];

  const choose = (mode: GitPrepareMode) => {
    if (mode === value.mode) return;
    if (mode === "new_worktree" || mode === "new_branch") onChange({ mode, branch: info?.suggested_branch || reroll(""), base: info?.branch ?? "" });
    else if (mode === "switch_branch") onChange({ mode, branch: switchable[0]?.name ?? "", base: "" });
    else if (mode === "open_worktree") onChange({ mode, branch: linked[0]?.branch ?? "", base: linked[0]?.path ?? "" });
    else onChange({ mode, branch: "", base: "" });
  };

  const field = "h-9 w-full rounded-lg border border-input bg-background/60 px-3 text-sm text-foreground outline-none focus:border-ring focus:ring-1 focus:ring-ring/30 disabled:opacity-50";
  const creating = value.mode === "new_worktree" || value.mode === "new_branch";
  const preview = value.mode === "new_worktree" && info?.main_root
    ? `${folderName(info.main_root)}/.worktrees/${value.branch.trim().replace(/\s+/g, "-").replace(/\//g, "-") || "…"}` : "";

  const summary = !info?.is_repo
    ? { title: "Opens the folder as it is", hint: "No git here. You can set it up if you like." }
    : context === "agent"
      ? { title: info.branch ? <>Works on <span className="font-mono">{info.branch}</span></> : "Works in the project folder", hint: "Same folder as your other agents. Nothing to set up." }
      : { title: info.branch ? <>Works on <span className="font-mono">{info.branch}</span></> : "Works in the project folder", hint: "In the project folder, as it is now. Nothing to set up." };

  if (!expanded && !error && (loading || info?.git_available)) {
    return <section aria-label="Git" data-testid="git-checkout-picker">
      <div className="flex items-center gap-3 rounded-lg border border-border bg-background px-3 py-2.5">
        <FolderTree className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
        <span className="min-w-0 flex-1">
          {loading ? <span className="flex items-center gap-1.5 text-sm text-muted-foreground"><Loader2 className="h-3 w-3 animate-spin" />Reading repository…</span>
            : <>
              <span className="block truncate text-sm font-medium text-foreground">{summary.title}</span>
              <span className="mt-0.5 block text-xs leading-snug text-muted-foreground">{summary.hint}</span>
            </>}
        </span>
        <button type="button" disabled={disabled || loading} aria-expanded={false} onClick={() => setUnfolded(true)}
          className="flex shrink-0 items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50">
          Git options<ChevronDown className="h-3.5 w-3.5" aria-hidden />
        </button>
      </div>
    </section>;
  }

  return <section aria-label="Git" data-testid="git-checkout-picker" className="space-y-3">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <span className="flex items-center gap-2 text-xs font-medium text-muted-foreground">Git
        {value.mode === "current" && <button type="button" aria-expanded onClick={() => setUnfolded(false)}
          className="rounded-md px-1.5 py-0.5 text-xs font-normal hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">Hide options</button>}
      </span>
      {loading ? <span className="flex items-center gap-1.5 text-xs text-muted-foreground"><Loader2 className="h-3 w-3 animate-spin" />Reading repository…</span>
        : info?.is_repo ? <GitStatusLine info={info} /> : null}
    </div>
    {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
    {!loading && info && !info.git_available && <p className="rounded-lg border border-border bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
      Git is not installed on this computer. Install it from git-scm.com to use branches and worktrees.</p>}

    {!loading && info?.git_available && <div className="grid grid-cols-1 gap-2 sm:grid-cols-2" role="radiogroup" aria-label="Git checkout">
      {options.map((option) => {
        const checked = value.mode === option.mode;
        return <button key={option.mode} type="button" role="radio" aria-checked={checked} disabled={disabled}
          onClick={() => choose(option.mode)}
          className={cn("flex items-start gap-2.5 rounded-lg border px-3 py-2.5 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50",
            checked ? "border-primary bg-primary/10 ring-1 ring-primary/40" : "border-border bg-background hover:bg-muted")}>
          <option.Icon className={cn("mt-0.5 h-4 w-4 shrink-0", checked ? "text-primary" : "text-muted-foreground")} aria-hidden />
          <span className="min-w-0">
            <span className="flex items-center gap-1.5 text-sm font-medium text-foreground">{option.title}
              {option.recommended && <span className="rounded-full bg-muted px-1.5 py-px text-[10px] font-medium text-muted-foreground">Recommended</span>}</span>
            <span className="mt-0.5 block text-xs leading-snug text-muted-foreground">{option.hint}</span>
          </span>
        </button>;
      })}
    </div>}

    {info?.is_repo && creating && <div className="grid grid-cols-1 gap-2 rounded-xl border border-border bg-muted/30 p-3 sm:grid-cols-2">
      <label className="block text-xs text-muted-foreground">Branch name
        <span className="mt-1 flex gap-1.5">
          <input value={value.branch} disabled={disabled} spellCheck={false} aria-label="Branch name"
            onChange={(event) => onChange({ ...value, branch: event.target.value })}
            placeholder={info.suggested_branch} className={cn(field, "font-mono text-xs")} />
          <button type="button" disabled={disabled} aria-label="Suggest another name" title="Suggest another name"
            onClick={() => onChange({ ...value, branch: reroll(value.branch) })}
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-border text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-50"><Dices className="h-4 w-4" /></button>
        </span>
      </label>
      <div className="block text-xs text-muted-foreground"><span>Based on</span>
        <BrandedSelect value={value.base} disabled={disabled || info.unborn} ariaLabel="Base branch"
          onValueChange={(base) => onChange({ ...value, base })} className="mt-1 h-9"
          options={bases.map((name) => ({ value: name, label: name, hint: name === info.branch ? "current" : name === info.default_branch ? "default" : undefined }))} />
      </div>
      <p className="text-xs text-muted-foreground sm:col-span-2">
        {info.unborn ? "This repository has no commit yet — make a first commit before branching."
          : value.mode === "new_worktree" ? <>Creates <span className="font-mono text-foreground">{preview}</span>. Only committed work is copied; an existing branch is reused.</>
          : info.dirty ? "Your uncommitted changes move along to the new branch." : "The project folder switches to the new branch for every agent in it."}
      </p>
    </div>}

    {info?.is_repo && value.mode === "switch_branch" && <div className="block text-xs text-muted-foreground"><span>Branch</span>
      <BrandedSelect value={value.branch} disabled={disabled} ariaLabel="Existing branch" onValueChange={(branch) => onChange({ ...value, branch })} className="mt-1 h-9"
        options={switchable.map((branch) => ({ value: branch.name, label: branch.name }))} />
    </div>}

    {info?.is_repo && value.mode === "open_worktree" && <div className="block text-xs text-muted-foreground"><span>Worktree</span>
      <BrandedSelect value={value.base} disabled={disabled} ariaLabel="Existing worktree"
        onValueChange={(path) => { const tree = linked.find((entry) => entry.path === path); onChange({ ...value, base: path, branch: tree?.branch ?? "" }); }}
        className="mt-1 h-9"
        options={linked.map((tree) => ({ value: tree.path, label: folderName(tree.path), hint: tree.branch || "detached", searchText: tree.path }))} />
    </div>}

    {value.mode === "init" && <p className="text-xs text-muted-foreground">Creates a repository on <span className="font-mono">main</span> with an empty first commit. Your files are not committed — you decide what goes in.</p>}
  </section>;
}
