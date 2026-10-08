/**
 * Portions adapted from pingdotgg/t3code @ 12069ee (apps/web
 * GitHubRoutingSettings.tsx: per-machine GitHub sharing with the trust
 * warning as the first line next to the control), MIT License, Copyright (c)
 * 2026 T3 Tools Inc. Full text: third_party/t3code/LICENSE.
 *
 * "GitHub for the agents here": on an explicit click, this PC's GitHub login
 * is shared with the coding agents on this computer, so they can clone and
 * push while this PC is off (backend: ``jarvis.computers.github_access``).
 */
import { Check, GitBranch, Loader2, ShieldAlert } from "lucide-react";
import { Panel } from "@/components/extensions/primitives";
import { Button } from "@/components/ui/button";
import { useGithubShare } from "@/hooks/useComputers";
import { useT } from "@/i18n";
import type { Computer } from "@/lib/computersApi";
import { formatAgo } from "./parts";

function fill(template: string, values: Record<string, string>): string {
  return template.replace(/\{(\w+)\}/g, (_m, key: string) => values[key] ?? "");
}

export function GithubAccess({ computer }: { computer: Computer }) {
  const t = useT();
  const github = useGithubShare();
  const shared = computer.github_shared_at != null;
  const reachable = computer.enabled !== false && computer.health.status === "online";
  const error = github.error instanceof Error ? github.error.message : null;

  return (
    <div data-testid="computer-github">
    <Panel className="p-5">
      <div className="flex items-start gap-3">
        <GitBranch aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
        <div className="min-w-0 flex-1 space-y-2">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h3 className="text-base font-semibold text-foreground-strong">{t("computers.github_title")}</h3>
            <div className="flex items-center gap-2">
              {shared && (
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={!reachable || github.isPending}
                  onClick={() => github.mutate({ id: computer.id, share: false })}
                  data-testid="computer-github-unshare"
                >
                  {t("computers.github_unshare")}
                </Button>
              )}
              <Button
                variant="outline"
                size="sm"
                disabled={!reachable || github.isPending}
                onClick={() => github.mutate({ id: computer.id, share: true })}
                data-testid="computer-github-share"
              >
                {github.isPending && <Loader2 className="animate-spin" />}
                {shared ? t("computers.github_refresh") : t("computers.github_share")}
              </Button>
            </div>
          </div>
          <p className="text-sm text-muted-foreground">{t("computers.github_body")}</p>
          <p className="flex items-start gap-1.5 text-xs text-warning">
            <ShieldAlert aria-hidden className="mt-px h-3.5 w-3.5 shrink-0" />
            {t("computers.github_warning")}
          </p>
          {shared && computer.github_shared_at && (
            <p className="flex items-center gap-1.5 text-xs text-success">
              <Check aria-hidden className="h-3.5 w-3.5" />
              {fill(t("computers.github_shared"), { ago: formatAgo(computer.github_shared_at, t) })}
            </p>
          )}
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
        </div>
      </div>
    </Panel>
    </div>
  );
}
