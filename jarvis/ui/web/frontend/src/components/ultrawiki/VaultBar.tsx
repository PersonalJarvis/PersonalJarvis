/**
 * The Obsidian vault: the same knowledge as Markdown files on disk.
 *
 * Deliberately a quiet strip at the bottom of Explore rather than its own
 * tab. The in-app view is the floor — it works on every machine, including a
 * headless server that will never have Obsidian — and the vault is the
 * addition for people who want their knowledge in their own editor.
 *
 * Which is also why a machine without Obsidian gets a sentence, not a
 * disabled button: the export still runs there and the files are still the
 * deliverable.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, FolderOpen, Loader2 } from "lucide-react";

import { useT } from "@/i18n";
import {
  exportVault,
  fetchVaultStatus,
  registerVaultWithObsidian,
  type UltraWikiVaultExport,
} from "@/lib/ultrawikiExploreApi";

const STATUS_KEY = ["ultrawiki", "vault", "status"] as const;

export function VaultBar(): JSX.Element | null {
  const t = useT();
  const queryClient = useQueryClient();

  const statusQuery = useQuery({
    queryKey: STATUS_KEY,
    queryFn: fetchVaultStatus,
    staleTime: 10_000,
  });

  const exportMutation = useMutation<UltraWikiVaultExport, Error>({
    mutationFn: exportVault,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: STATUS_KEY }),
  });

  const registerMutation = useMutation({
    mutationFn: registerVaultWithObsidian,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: STATUS_KEY }),
  });

  const status = statusQuery.data;
  if (!status) return null;

  const canRegister =
    status.exists && status.obsidian.installed && !status.obsidian.registered;
  const failure = exportMutation.error ?? registerMutation.error;

  return (
    <div
      data-testid="vault-bar"
      className="border-t border-border bg-card/40 px-4 py-2.5"
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <FolderOpen className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
        <span className="text-xs text-foreground">
          {t("ultrawiki.explore.vault.title")}
        </span>

        <span
          data-testid="vault-state"
          className="text-[11px] tabular-nums text-muted-foreground"
        >
          {status.exists
            ? `${t("ultrawiki.explore.vault.notes").replace("{0}", String(status.notes))}${
                status.last_export_at
                  ? ` · ${t("ultrawiki.explore.vault.last").replace(
                      "{0}",
                      status.last_export_at.slice(0, 10),
                    )}`
                  : ""
              }`
            : t("ultrawiki.explore.vault.never")}
        </span>

        <span className="flex-1" />

        <button
          type="button"
          data-testid="vault-export"
          onClick={() => exportMutation.mutate()}
          disabled={exportMutation.isPending}
          className="inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1 text-[11px] text-foreground transition-colors hover:border-primary/50 disabled:opacity-60"
        >
          {exportMutation.isPending && (
            <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
          )}
          {exportMutation.isPending
            ? t("ultrawiki.explore.vault.exporting")
            : t("ultrawiki.explore.vault.export")}
        </button>

        {canRegister && (
          <button
            type="button"
            data-testid="vault-register"
            onClick={() => registerMutation.mutate()}
            disabled={registerMutation.isPending}
            className="inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1 text-[11px] text-foreground transition-colors hover:border-primary/50 disabled:opacity-60"
          >
            {registerMutation.isPending
              ? t("ultrawiki.explore.vault.registering")
              : t("ultrawiki.explore.vault.register")}
          </button>
        )}

        {status.obsidian.registered && (
          <span
            data-testid="vault-registered"
            className="inline-flex items-center gap-1 text-[11px] text-muted-foreground"
          >
            <Check className="h-3 w-3" aria-hidden />
            {t("ultrawiki.explore.vault.registered")}
          </span>
        )}
      </div>

      <p
        data-testid="vault-path"
        className="mt-1 truncate font-mono text-[10px] text-muted-foreground"
        title={status.path}
      >
        {status.path}
      </p>

      {!status.obsidian.installed && (
        <p
          data-testid="vault-no-obsidian"
          className="mt-1 text-[11px] text-muted-foreground"
        >
          {t("ultrawiki.explore.vault.not_installed")}
        </p>
      )}

      {exportMutation.data && (
        <p
          data-testid="vault-result"
          className="mt-1 text-[11px] tabular-nums text-muted-foreground"
        >
          {t("ultrawiki.explore.vault.exported")
            .replace("{0}", String(exportMutation.data.written))
            .replace("{1}", String(exportMutation.data.unchanged))}
        </p>
      )}

      {failure && (
        <p
          role="alert"
          data-testid="vault-error"
          className="mt-1 text-[11px] text-destructive"
        >
          {t("ultrawiki.explore.vault.error").replace("{0}", failure.message)}
        </p>
      )}
    </div>
  );
}

export default VaultBar;
