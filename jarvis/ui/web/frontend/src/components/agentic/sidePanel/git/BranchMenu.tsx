import { useEffect, useLayoutEffect, useRef, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { createPortal } from "react-dom";
import {
  Copy,
  ExternalLink,
  FolderOpen,
  GitBranch,
  GitCommitHorizontal,
  GitCompareArrows,
  GitPullRequest,
  GitPullRequestCreate,
  Hash,
  History,
  Link2,
  CirclePlay,
  SquareTerminal,
  type LucideIcon,
} from "lucide-react";
import { fill, useT } from "@/i18n";
import { robustCopy } from "@/lib/clipboard";
import { openExternalUrl } from "@/lib/openExternal";
import { useEventStore } from "@/store/events";
import type { BranchRow } from "./gitOverviewApi";

const MENU_WIDTH = 264;
const MENU_MARGIN = 8;

/** A branch name as a GitHub URL path: every segment encoded, the slashes kept. */
export function branchPath(name: string): string {
  return name.split("/").map(encodeURIComponent).join("/");
}

/** The branch's page on GitHub, or "" when it is not there to open. */
export function branchUrl(repoUrl: string, row: BranchRow): string {
  if (!repoUrl || !(row.on_github || row.remote_only)) return "";
  return `${repoUrl}/tree/${branchPath(row.name)}`;
}

interface MenuItem {
  id: string;
  label: string;
  hint?: string;
  icon: LucideIcon;
  disabled?: boolean;
  onSelect: () => void;
}

/**
 * Everything one branch row offers: its GitHub pages (branch, commits,
 * comparison with the default branch, the pull request or a new one, the CI
 * run) and the strings people paste elsewhere. Opened by right-click or by
 * the row's ⋯ button; portalled so the scrolling list never clips it.
 */
export function BranchMenu({
  row,
  repoUrl,
  defaultBranch,
  x,
  y,
  onDismiss,
}: {
  row: BranchRow;
  repoUrl: string;
  defaultBranch: string;
  x: number;
  y: number;
  onDismiss: () => void;
}) {
  const t = useT();
  const pushToast = useEventStore((state) => state.pushToast);
  const menuRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        onDismiss();
      }
    };
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (menuRef.current?.contains(target)) return;
      // The ⋯ button toggles the menu itself; closing here first would reopen it.
      if (target instanceof Element && target.closest("[data-branch-menu-anchor]")) return;
      onDismiss();
    };
    window.addEventListener("keydown", onKeyDown, true);
    window.addEventListener("pointerdown", onPointerDown, true);
    window.addEventListener("resize", onDismiss);
    window.addEventListener("blur", onDismiss);
    document.addEventListener("scroll", onDismiss, true);
    return () => {
      window.removeEventListener("keydown", onKeyDown, true);
      window.removeEventListener("pointerdown", onPointerDown, true);
      window.removeEventListener("resize", onDismiss);
      window.removeEventListener("blur", onDismiss);
      document.removeEventListener("scroll", onDismiss, true);
    };
  }, [onDismiss]);

  useLayoutEffect(() => {
    const node = menuRef.current;
    if (!node) return;
    const { width, height } = node.getBoundingClientRect();
    node.style.left = `${Math.max(MENU_MARGIN, Math.min(x, window.innerWidth - width - MENU_MARGIN))}px`;
    node.style.top = `${Math.max(MENU_MARGIN, Math.min(y, window.innerHeight - height - MENU_MARGIN))}px`;
    node.style.visibility = "visible";
    node.querySelector<HTMLButtonElement>("button:not([disabled])")?.focus();
  }, [x, y]);

  const onMenuKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    const items = Array.from(menuRef.current?.querySelectorAll<HTMLButtonElement>("button:not([disabled])") ?? []);
    if (!items.length) return;
    const current = items.indexOf(document.activeElement as HTMLButtonElement);
    const step = event.key === "ArrowDown" ? 1 : -1;
    items[(current + step + items.length) % items.length]?.focus();
  };

  const open = (url: string) => () => {
    onDismiss();
    void openExternalUrl(url);
  };
  const copy = (text: string) => () => {
    onDismiss();
    void robustCopy(text).then((ok) =>
      pushToast(ok ? "success" : "error", ok ? fill(t("ide_side_panel.git.menu.copied"), { text }) : t("ide_side_panel.git.menu.copy_failed")),
    );
  };

  const onGitHub = Boolean(branchUrl(repoUrl, row));
  const offHint = !repoUrl ? t("ide_side_panel.git.menu.no_repo") : onGitHub ? undefined : t("ide_side_panel.git.not_pushed");
  const path = branchPath(row.name);
  const isDefault = row.name === defaultBranch;
  const pr = row.pull_requests[0];
  const livePr = pr && pr.state !== "merged" && pr.state !== "closed" ? pr : undefined;

  const github: MenuItem[] = [
    {
      id: "branch",
      label: t("ide_side_panel.git.menu.open_branch"),
      hint: offHint,
      icon: GitBranch,
      disabled: !onGitHub,
      onSelect: open(`${repoUrl}/tree/${path}`),
    },
    {
      id: "commits",
      label: t("ide_side_panel.git.menu.commits"),
      hint: offHint,
      icon: History,
      disabled: !onGitHub,
      onSelect: open(`${repoUrl}/commits/${path}`),
    },
  ];
  if (!isDefault && defaultBranch) {
    github.push({
      id: "compare",
      label: fill(t("ide_side_panel.git.menu.compare"), { target: defaultBranch }),
      hint: offHint,
      icon: GitCompareArrows,
      disabled: !onGitHub,
      onSelect: open(`${repoUrl}/compare/${branchPath(defaultBranch)}...${path}`),
    });
  }
  if (pr) {
    github.push({
      id: "pr",
      label: fill(t("ide_side_panel.git.menu.open_pr"), { number: pr.number }),
      hint: pr.title,
      icon: GitPullRequest,
      onSelect: open(pr.url),
    });
  }
  if (!livePr && !isDefault && defaultBranch) {
    github.push({
      id: "create-pr",
      label: t("ide_side_panel.git.menu.create_pr"),
      hint: offHint ?? fill(t("ide_side_panel.git.menu.create_pr_hint"), { target: defaultBranch }),
      icon: GitPullRequestCreate,
      disabled: !onGitHub,
      onSelect: open(`${repoUrl}/compare/${branchPath(defaultBranch)}...${path}?expand=1`),
    });
  }
  if (row.ci.url) {
    github.push({
      id: "ci",
      label: t("ide_side_panel.git.menu.open_ci"),
      hint: t(`ide_side_panel.git.ci.${row.ci.state}`),
      icon: CirclePlay,
      onSelect: open(row.ci.url),
    });
  }
  if (row.head && onGitHub) {
    github.push({
      id: "commit",
      label: t("ide_side_panel.git.menu.open_commit"),
      hint: row.head.slice(0, 7),
      icon: GitCommitHorizontal,
      onSelect: open(`${repoUrl}/commit/${encodeURIComponent(row.head)}`),
    });
  }

  const copies: MenuItem[] = [
    { id: "copy-name", label: t("ide_side_panel.git.menu.copy_name"), icon: Copy, onSelect: copy(row.name) },
    {
      id: "copy-switch",
      label: t("ide_side_panel.git.menu.copy_switch"),
      hint: `git switch ${row.name}`,
      icon: SquareTerminal,
      onSelect: copy(`git switch ${row.name}`),
    },
  ];
  if (row.head) {
    copies.push({ id: "copy-sha", label: t("ide_side_panel.git.menu.copy_sha"), hint: row.head.slice(0, 7), icon: Hash, onSelect: copy(row.head) });
  }
  if (onGitHub) {
    copies.push({ id: "copy-link", label: t("ide_side_panel.git.menu.copy_link"), icon: Link2, onSelect: copy(`${repoUrl}/tree/${path}`) });
  }
  if (row.worktree) {
    copies.push({
      id: "copy-worktree",
      label: t("ide_side_panel.git.menu.copy_worktree"),
      hint: row.worktree,
      icon: FolderOpen,
      onSelect: copy(row.worktree),
    });
  }

  return createPortal(
    <div
      ref={menuRef}
      role="menu"
      aria-label={fill(t("ide_side_panel.git.menu.aria"), { branch: row.name })}
      data-testid="git-branch-menu"
      onKeyDown={onMenuKeyDown}
      style={{ width: MENU_WIDTH, visibility: "hidden" }}
      className="fixed z-[100] overflow-hidden rounded-xl border border-border bg-popover p-1 text-popover-foreground shadow-float"
    >
      <div className="flex items-center gap-2 px-2.5 pb-1.5 pt-1 text-[11px] font-medium text-muted-foreground">
        <GitBranch aria-hidden className="h-3 w-3 shrink-0" />
        <span className="min-w-0 truncate font-mono">{row.name}</span>
      </div>
      {[github, copies].map((section, index) => (
        <div key={section[0]?.id ?? index} role="group" className={index > 0 ? "mt-1 border-t border-border/70 pt-1" : ""}>
          {section.map((item) => {
            const Icon = item.icon;
            return (
              <button
                key={item.id}
                type="button"
                role="menuitem"
                data-testid={`git-menu-${item.id}`}
                disabled={item.disabled}
                onClick={item.onSelect}
                className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-left text-[13px] text-foreground transition-colors hover:bg-muted focus-visible:bg-muted focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-45"
              >
                <Icon aria-hidden className="h-4 w-4 shrink-0 text-muted-foreground" />
                <span className="flex min-w-0 flex-1 flex-col">
                  <span className="truncate">{item.label}</span>
                  {item.hint && <span className="truncate text-[11px] text-muted-foreground">{item.hint}</span>}
                </span>
                {section === github && !item.disabled && <ExternalLink aria-hidden className="h-3 w-3 shrink-0 text-muted-foreground/70" />}
              </button>
            );
          })}
        </div>
      ))}
    </div>,
    document.body,
  );
}
