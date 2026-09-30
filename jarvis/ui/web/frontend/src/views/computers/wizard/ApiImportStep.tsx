/**
 * Step 3 (API) — connect the provider account once with a token, then pick
 * the server from the account's own list. No IP to copy; on providers that
 * plant keys themselves, no password either.
 */
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ExternalLink, KeyRound, Loader2, RefreshCw, ShieldCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { computerKeys, useCloudServers } from "@/hooks/useComputers";
import { useT } from "@/i18n";
import { openExternalUrl } from "@/lib/openExternal";
import { cn } from "@/lib/utils";
import { computersApi, type CloudServer, type Computer, type ProviderInfo } from "@/lib/computersApi";
import { Field, formatMemory, inputClass } from "../parts";
import { ErrorNote, errorText } from "./shared";
import { StepActions } from "./StepActions";

export function ApiImportStep({
  provider,
  onBack,
  onAdded,
  onProviderChanged,
}: {
  provider: ProviderInfo;
  onBack: () => void;
  onAdded: (computer: Computer) => void;
  onProviderChanged: () => void;
}) {
  const t = useT();
  const qc = useQueryClient();
  const api = provider.api;
  const [connected, setConnected] = useState(Boolean(api?.connected));
  const servers = useCloudServers(provider.id, connected);
  const [token, setToken] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const needsPassword = !api?.attaches_keys;
  const fill = (key: string) => t(key).replace("{provider}", provider.name);

  async function saveToken() {
    if (token.trim().length < 8 || busy) return;
    setBusy(true);
    setError(null);
    try {
      const result = await computersApi.saveCloudToken(provider.id, token.trim());
      qc.setQueryData(computerKeys.cloudServers(provider.id), result.servers);
      setConnected(true);
      setToken("");
      onProviderChanged();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  async function importServer() {
    if (!selected || busy || (needsPassword && !password)) return;
    setBusy(true);
    setError(null);
    try {
      onAdded(
        await computersApi.importCloudServer(provider.id, {
          server_id: selected,
          username: provider.ssh.default_username || "root",
          password: needsPassword ? password : undefined,
        }),
      );
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  if (!connected) {
    return (
      <div className="flex h-full flex-col">
        <div className="flex-1 space-y-5">
          <ol className="space-y-4">
            <li className="flex gap-3">
              <StepNumber n={1} />
              <div className="min-w-0 text-sm">
                <div className="font-medium text-foreground-strong">{fill("computers.wz_token_step1")}</div>
                {api && <div className="mt-0.5 text-muted-foreground">{api.setup_hint}</div>}
                {api && (
                  <button
                    type="button"
                    onClick={() => void openExternalUrl(api.console_url)}
                    className="mt-1.5 inline-flex items-center gap-1 font-medium text-accent underline-offset-4 hover:underline"
                  >
                    {fill("computers.token_open")}
                    <ExternalLink className="h-3.5 w-3.5" aria-hidden />
                  </button>
                )}
              </div>
            </li>
            <li className="flex gap-3">
              <StepNumber n={2} />
              <div className="min-w-0 flex-1">
                <Field label={api?.token_label || t("computers.token_placeholder")} hint={t("computers.token_storage")}>
                  <input
                    type="password"
                    className={cn(inputClass, "font-mono")}
                    value={token}
                    onChange={(e) => setToken(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") void saveToken();
                    }}
                    autoFocus
                    autoComplete="off"
                    data-testid="wz-token"
                  />
                </Field>
              </div>
            </li>
          </ol>
          <ErrorNote message={error} />
        </div>
        <StepActions onBack={onBack}>
          <Button type="button" disabled={token.trim().length < 8 || busy} onClick={() => void saveToken()}>
            {busy ? <Loader2 className="animate-spin" /> : <KeyRound />}
            {t("computers.wz_token_connect")}
          </Button>
        </StepActions>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 space-y-4">
        <div className="flex items-center justify-between gap-3">
          <div className="text-sm font-medium text-foreground-secondary">{fill("computers.wz_pick_server")}</div>
          <Button type="button" variant="ghost" size="sm" onClick={() => void servers.refetch()} disabled={servers.isFetching}>
            {servers.isFetching ? <Loader2 className="animate-spin" /> : <RefreshCw />}
            {t("computers.refresh")}
          </Button>
        </div>
        {servers.isError && <ErrorNote message={errorText(servers.error)} />}
        {servers.isLoading && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" aria-hidden />}
        {servers.data && servers.data.length === 0 && (
          <p className="rounded-lg border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground">
            {t("computers.no_servers_in_account")}
          </p>
        )}
        {servers.data && servers.data.length > 0 && (
          <ul
            role="radiogroup"
            aria-label={fill("computers.wz_pick_server")}
            className="max-h-72 divide-y divide-border overflow-y-auto rounded-lg border border-border scrollbar-jarvis"
          >
            {servers.data.map((server) => (
              <ServerRow
                key={server.id}
                server={server}
                selected={selected === server.id}
                onSelect={() => setSelected(server.id)}
              />
            ))}
          </ul>
        )}
        {selected &&
          (needsPassword ? (
            <Field label={t("computers.field_password")} hint={provider.ssh.password_hint || t("computers.password_once_hint")}>
              <input
                type="password"
                className={inputClass}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="new-password"
              />
            </Field>
          ) : (
            <div className="flex items-start gap-3 rounded-lg border border-border bg-card px-4 py-3 text-sm">
              <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-success" aria-hidden />
              <p className="text-muted-foreground">{fill("computers.auto_key_body")}</p>
            </div>
          ))}
        <ErrorNote message={error} />
      </div>
      <StepActions onBack={onBack}>
        <Button
          type="button"
          disabled={!selected || busy || (needsPassword && !password)}
          onClick={() => void importServer()}
          data-testid="wz-import"
        >
          {busy && <Loader2 className="animate-spin" />}
          {t("computers.add")}
        </Button>
      </StepActions>
    </div>
  );
}

function StepNumber({ n }: { n: number }) {
  return (
    <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-border text-xs tabular-nums text-muted-foreground">
      {n}
    </span>
  );
}

function ServerRow({ server, selected, onSelect }: { server: CloudServer; selected: boolean; onSelect: () => void }) {
  const t = useT();
  const disabled = Boolean(server.added_as) || !server.host;
  const specs = [
    server.os,
    server.cpus ? `${server.cpus} ${t("computers.unit_cpu")}` : null,
    formatMemory(server.memory_mb),
    server.region,
  ].filter(Boolean);
  return (
    <li>
      <button
        type="button"
        role="radio"
        aria-checked={selected}
        disabled={disabled}
        onClick={onSelect}
        className={cn(
          "flex w-full items-center gap-3 px-4 py-3 text-left transition-colors",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
          selected ? "bg-accent-soft" : "hover:bg-secondary/60",
          disabled && "cursor-not-allowed opacity-55 hover:bg-transparent",
        )}
      >
        <span
          aria-hidden
          className={cn(
            "flex h-4 w-4 shrink-0 items-center justify-center rounded-full border",
            selected ? "border-accent" : "border-border-strong",
          )}
        >
          {selected && <span className="h-2 w-2 rounded-full bg-accent" />}
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-2">
            <span className="truncate text-sm font-medium text-foreground-strong">{server.name}</span>
            <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", server.running ? "bg-success" : "bg-foreground-faint")} aria-hidden />
            <span className="shrink-0 text-xs text-muted-foreground">{server.status}</span>
          </span>
          <span className="mt-0.5 block truncate text-xs text-muted-foreground">
            <span className="font-mono">{server.host ?? t("computers.no_ip")}</span>
            {specs.length > 0 && ` · ${specs.join(" · ")}`}
          </span>
        </span>
        {server.added_as && (
          <span className="shrink-0 rounded-full bg-secondary px-2 py-0.5 text-xs text-foreground-secondary">
            {t("computers.already_added")}
          </span>
        )}
      </button>
    </li>
  );
}
