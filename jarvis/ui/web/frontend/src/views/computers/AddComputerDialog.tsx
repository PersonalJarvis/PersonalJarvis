/**
 * "Add a computer" — one dialog, three doors:
 *
 *   1. A server you rent       any SSH host: IP, login, and a password used
 *                              ONCE so Jarvis can plant its own key.
 *   2. From a hosting account  Hostinger / Hetzner / DigitalOcean: save an API
 *                              token, pick the server from the account's list.
 *                              Hostinger even takes the key through its API.
 *   3. A VM on this computer   Multipass creates Ubuntu with Jarvis's key
 *                              already inside; no password ever exists.
 *
 * The door the user picked is the only form on screen. Every failure is the
 * backend's sentence, shown where the user's eyes already are.
 */
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  Cloud,
  ExternalLink,
  KeyRound,
  Loader2,
  MonitorSmartphone,
  RefreshCw,
  Server,
  ShieldCheck,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { ProviderLogo } from "@/components/providers/ProviderLogo";
import {
  computerKeys,
  useCloudProviders,
  useCloudServers,
  useIdentity,
  useLocalStatus,
  useUpsertComputer,
} from "@/hooks/useComputers";
import { useT } from "@/i18n";
import { openExternalUrl } from "@/lib/openExternal";
import { cn } from "@/lib/utils";
import {
  computersApi,
  type CloudProviderId,
  type CloudServer,
  type Computer,
} from "@/lib/computersApi";
import { CopyField, Field, inputClass, formatMemory } from "./parts";

export type AddPath = "server" | "cloud" | "local";

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** The three doors, used by the dialog AND the empty page. */
export function PathChoices({ onPick }: { onPick: (path: AddPath) => void }) {
  const t = useT();
  const doors: { id: AddPath; icon: ReactNode; title: string; body: string; foot: ReactNode }[] = [
    {
      id: "server",
      icon: <Server />,
      title: t("computers.path_server_title"),
      body: t("computers.path_server_body"),
      foot: <span className="font-mono text-xs">ssh root@203.0.113.10</span>,
    },
    {
      id: "cloud",
      icon: <Cloud />,
      title: t("computers.path_cloud_title"),
      body: t("computers.path_cloud_body"),
      foot: (
        <span className="flex items-center gap-1.5">
          {(["hostinger", "hetzner", "digitalocean"] as const).map((p) => (
            <ProviderLogo key={p} providerId={p} label={p} size="sm" />
          ))}
        </span>
      ),
    },
    {
      id: "local",
      icon: <MonitorSmartphone />,
      title: t("computers.path_local_title"),
      body: t("computers.path_local_body"),
      foot: <span className="text-xs">{t("computers.path_local_foot")}</span>,
    },
  ];
  return (
    <div className="grid gap-3 md:grid-cols-3">
      {doors.map((door) => (
        <button
          key={door.id}
          type="button"
          data-testid={`computers-path-${door.id}`}
          onClick={() => onPick(door.id)}
          className={cn(
            "group flex min-h-[176px] flex-col rounded-lg border border-border bg-card p-5 text-left transition-[border-color,background-color,transform] duration-200",
            "hover:-translate-y-0.5 hover:border-border-strong hover:bg-secondary/40",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          )}
        >
          <span className="flex h-9 w-9 items-center justify-center rounded-md bg-secondary text-foreground [&>svg]:h-[18px] [&>svg]:w-[18px]">
            {door.icon}
          </span>
          <span className="mt-4 text-title font-medium text-foreground-strong">{door.title}</span>
          <span className="mt-1 text-sm text-muted-foreground">{door.body}</span>
          <span className="mt-auto pt-4 text-muted-foreground">{door.foot}</span>
        </button>
      ))}
    </div>
  );
}

/** Pick one value from a short list — used for sizes, images, login modes. */
function Choice<T extends string | number>({
  value,
  options,
  onChange,
  label,
}: {
  value: T;
  options: { id: T; label: string }[];
  onChange: (value: T) => void;
  label: string;
}) {
  return (
    <div role="radiogroup" aria-label={label} className="flex flex-wrap gap-1.5">
      {options.map((o) => {
        const active = o.id === value;
        return (
          <button
            key={String(o.id)}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => onChange(o.id)}
            className={cn(
              "h-8 rounded-md border px-3 text-sm font-medium tabular-nums transition-colors",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              active
                ? "border-accent bg-accent-soft text-foreground-strong"
                : "border-border text-muted-foreground hover:border-border-strong hover:text-foreground",
            )}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

/** A radio card for the login mode: title, one line of why. */
function ModeCard({
  active,
  onClick,
  title,
  body,
  badge,
}: {
  active: boolean;
  onClick: () => void;
  title: string;
  body: string;
  badge?: string;
}) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={active}
      onClick={onClick}
      className={cn(
        "flex w-full items-start gap-3 rounded-md border px-3.5 py-3 text-left transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        active ? "border-accent bg-accent-soft" : "border-border hover:border-border-strong",
      )}
    >
      <span
        aria-hidden
        className={cn(
          "mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full border",
          active ? "border-accent" : "border-border-strong",
        )}
      >
        {active && <span className="h-2 w-2 rounded-full bg-accent" />}
      </span>
      <span className="min-w-0">
        <span className="flex items-center gap-2 text-base font-medium text-foreground-strong">
          {title}
          {badge && (
            <span className="rounded-full bg-secondary px-2 py-0.5 text-xs font-medium text-foreground-secondary">
              {badge}
            </span>
          )}
        </span>
        <span className="mt-0.5 block text-sm text-muted-foreground">{body}</span>
      </span>
    </button>
  );
}

function ErrorNote({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <p
      role="alert"
      className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-foreground"
    >
      {message}
    </p>
  );
}

// ---------------------------------------------------------------------------
// Door 1 — a server over SSH
// ---------------------------------------------------------------------------

type LoginMode = "password_once" | "key_added" | "keep_password";

function ServerForm({ onAdded }: { onAdded: (computer: Computer) => void }) {
  const t = useT();
  const identity = useIdentity();
  const [name, setName] = useState("");
  const [host, setHost] = useState("");
  const [port, setPort] = useState("22");
  const [username, setUsername] = useState("root");
  const [mode, setMode] = useState<LoginMode>("password_once");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const needsPassword = mode !== "key_added";
  const portNumber = Number(port);
  const valid =
    host.trim() !== "" &&
    username.trim() !== "" &&
    Number.isInteger(portNumber) &&
    portNumber >= 1 &&
    portNumber <= 65535 &&
    (!needsPassword || password !== "");

  async function submit() {
    if (!valid || busy) return;
    setBusy(true);
    setError(null);
    try {
      const computer = await computersApi.add({
        name: name.trim() || host.trim(),
        host: host.trim(),
        port: portNumber,
        username: username.trim(),
        auth: needsPassword ? "password" : "key",
        password: needsPassword ? password : undefined,
        keep_password: mode === "keep_password",
      });
      onAdded(computer);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      className="space-y-5"
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_96px]">
        <Field label={t("computers.field_host")} hint={t("computers.field_host_hint")}>
          <input
            className={cn(inputClass, "font-mono")}
            value={host}
            onChange={(e) => setHost(e.target.value)}
            placeholder="203.0.113.10"
            autoFocus
            spellCheck={false}
            autoComplete="off"
          />
        </Field>
        <Field label={t("computers.field_port")}>
          <input
            className={cn(inputClass, "font-mono")}
            value={port}
            inputMode="numeric"
            onChange={(e) => setPort(e.target.value.replace(/[^0-9]/g, ""))}
          />
        </Field>
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label={t("computers.field_name")}>
          <input
            className={inputClass}
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={t("computers.field_name_placeholder")}
          />
        </Field>
        <Field label={t("computers.field_username")}>
          <input
            className={cn(inputClass, "font-mono")}
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            spellCheck={false}
            autoComplete="off"
          />
        </Field>
      </div>

      <div>
        <div className="mb-2 text-sm font-medium text-foreground-secondary">
          {t("computers.login_title")}
        </div>
        <div role="radiogroup" aria-label={t("computers.login_title")} className="space-y-2">
          <ModeCard
            active={mode === "password_once"}
            onClick={() => setMode("password_once")}
            title={t("computers.login_once_title")}
            body={t("computers.login_once_body")}
            badge={t("computers.recommended")}
          />
          <ModeCard
            active={mode === "key_added"}
            onClick={() => setMode("key_added")}
            title={t("computers.login_key_title")}
            body={t("computers.login_key_body")}
          />
          <ModeCard
            active={mode === "keep_password"}
            onClick={() => setMode("keep_password")}
            title={t("computers.login_keep_title")}
            body={t("computers.login_keep_body")}
          />
        </div>
      </div>

      {needsPassword ? (
        <Field
          label={t("computers.field_password")}
          hint={mode === "password_once" ? t("computers.password_once_hint") : t("computers.password_keep_hint")}
        >
          <input
            type="password"
            className={inputClass}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="new-password"
          />
        </Field>
      ) : identity.data ? (
        <CopyField
          multiline
          label={t("computers.key_paste_hint")}
          value={identity.data.public_key}
          copyLabel={t("computers.copy")}
          copiedLabel={t("computers.copied")}
        />
      ) : null}

      <ErrorNote message={error} />

      <div className="flex items-center justify-end gap-2 pt-1">
        <Button type="submit" disabled={!valid || busy}>
          {busy ? <Loader2 className="animate-spin" /> : <ShieldCheck />}
          {busy ? t("computers.connecting") : t("computers.connect")}
        </Button>
      </div>
    </form>
  );
}

// ---------------------------------------------------------------------------
// Door 2 — a hosting account
// ---------------------------------------------------------------------------

function CloudServerRow({
  server,
  selected,
  onSelect,
}: {
  server: CloudServer;
  selected: boolean;
  onSelect: () => void;
}) {
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
            <span className="truncate text-base font-medium text-foreground-strong">{server.name}</span>
            <span
              className={cn(
                "h-1.5 w-1.5 shrink-0 rounded-full",
                server.running ? "bg-success" : "bg-foreground-faint",
              )}
              aria-hidden
            />
            <span className="shrink-0 text-xs text-muted-foreground">{server.status}</span>
          </span>
          <span className="mt-0.5 block truncate text-sm text-muted-foreground">
            <span className="font-mono text-xs">{server.host ?? t("computers.no_ip")}</span>
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

function CloudForm({ onAdded }: { onAdded: (computer: Computer) => void }) {
  const t = useT();
  const qc = useQueryClient();
  const providers = useCloudProviders();
  const [providerId, setProviderId] = useState<CloudProviderId>("hostinger");
  const provider = providers.data?.find((p) => p.id === providerId);
  const connected = Boolean(provider?.connected);
  const servers = useCloudServers(providerId, connected);
  const [token, setToken] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [usePassword, setUsePassword] = useState(false);
  const [password, setPassword] = useState("");
  const [username, setUsername] = useState("root");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setSelected(null);
    setError(null);
    setToken("");
    setPassword("");
    setUsePassword(false);
  }, [providerId]);

  const needsPassword = !provider?.attaches_keys || usePassword;

  async function saveToken() {
    if (token.trim().length < 8 || busy) return;
    setBusy(true);
    setError(null);
    try {
      const result = await computersApi.saveCloudToken(providerId, token.trim());
      qc.setQueryData(computerKeys.cloudServers(providerId), result.servers);
      await qc.invalidateQueries({ queryKey: computerKeys.cloud(), exact: true });
      setToken("");
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  async function disconnect() {
    setBusy(true);
    try {
      await computersApi.forgetCloudToken(providerId);
      await qc.invalidateQueries({ queryKey: computerKeys.cloud() });
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  async function importServer() {
    if (!selected || busy) return;
    if (needsPassword && !password) return;
    setBusy(true);
    setError(null);
    try {
      const computer = await computersApi.importCloudServer(providerId, {
        server_id: selected,
        username: username.trim() || "root",
        password: needsPassword ? password : undefined,
      });
      onAdded(computer);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-5">
      <div role="tablist" aria-label={t("computers.cloud_provider")} className="grid grid-cols-3 gap-2">
        {(providers.data ?? []).map((p) => (
          <button
            key={p.id}
            type="button"
            role="tab"
            aria-selected={p.id === providerId}
            onClick={() => setProviderId(p.id)}
            className={cn(
              "flex items-center gap-2.5 rounded-md border px-3 py-2.5 text-left transition-colors",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              p.id === providerId
                ? "border-accent bg-accent-soft"
                : "border-border hover:border-border-strong",
            )}
          >
            <ProviderLogo providerId={p.id} label={p.name} className="h-8 w-8" />
            <span className="min-w-0">
              <span className="block truncate text-sm font-medium text-foreground-strong">{p.name}</span>
              <span className="flex items-center gap-1 text-xs text-muted-foreground">
                <span
                  className={cn("h-1.5 w-1.5 rounded-full", p.connected ? "bg-success" : "bg-foreground-faint")}
                  aria-hidden
                />
                {p.connected ? t("computers.account_connected") : t("computers.account_not_connected")}
              </span>
            </span>
          </button>
        ))}
      </div>

      {providers.isLoading && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />}

      {provider && !connected && (
        <div className="space-y-4 rounded-lg border border-border bg-card p-4">
          <div>
            <div className="text-base font-medium text-foreground-strong">
              {t("computers.token_title").replace("{provider}", provider.name)}
            </div>
            <p className="mt-1 text-sm text-muted-foreground">{t("computers.token_body")}</p>
          </div>
          <ol className="space-y-1.5 text-sm text-foreground-secondary">
            <li className="flex gap-2">
              <span className="tabular-nums text-foreground-faint">1</span>
              <span>
                <button
                  type="button"
                  onClick={() => void openExternalUrl(provider.console_url)}
                  className="inline-flex items-center gap-1 font-medium text-accent underline-offset-4 hover:underline"
                >
                  {t("computers.token_open").replace("{provider}", provider.name)}
                  <ExternalLink className="h-3.5 w-3.5" aria-hidden />
                </button>
                <span className="block text-xs text-muted-foreground">{provider.setup_hint}</span>
              </span>
            </li>
            <li className="flex gap-2">
              <span className="tabular-nums text-foreground-faint">2</span>
              <span>{t("computers.token_paste")}</span>
            </li>
          </ol>
          <form
            className="flex gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              void saveToken();
            }}
          >
            <input
              type="password"
              className={cn(inputClass, "font-mono")}
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder={t("computers.token_placeholder")}
              aria-label={t("computers.token_placeholder")}
              autoComplete="off"
            />
            <Button type="submit" disabled={token.trim().length < 8 || busy}>
              {busy ? <Loader2 className="animate-spin" /> : <KeyRound />}
              {t("computers.token_save")}
            </Button>
          </form>
          <p className="text-xs text-muted-foreground">{t("computers.token_storage")}</p>
        </div>
      )}

      {provider && connected && (
        <div className="space-y-4">
          <div className="flex items-center justify-between gap-3">
            <div className="text-sm font-medium text-foreground-secondary">
              {t("computers.pick_server")}
            </div>
            <div className="flex items-center gap-1">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => void servers.refetch()}
                disabled={servers.isFetching}
              >
                {servers.isFetching ? <Loader2 className="animate-spin" /> : <RefreshCw />}
                {t("computers.refresh")}
              </Button>
              <Button type="button" variant="ghost" size="sm" onClick={() => void disconnect()} disabled={busy}>
                {t("computers.disconnect_account")}
              </Button>
            </div>
          </div>
          {servers.isError && <ErrorNote message={errorText(servers.error)} />}
          {servers.data && servers.data.length === 0 && (
            <p className="rounded-md border border-dashed border-border px-4 py-6 text-center text-sm text-muted-foreground">
              {t("computers.no_servers_in_account")}
            </p>
          )}
          {servers.data && servers.data.length > 0 && (
            <ul
              role="radiogroup"
              aria-label={t("computers.pick_server")}
              className="max-h-64 divide-y divide-border overflow-y-auto rounded-lg border border-border scrollbar-jarvis"
            >
              {servers.data.map((server) => (
                <CloudServerRow
                  key={server.id}
                  server={server}
                  selected={selected === server.id}
                  onSelect={() => setSelected(server.id)}
                />
              ))}
            </ul>
          )}

          {selected && (
            <div className="space-y-3 rounded-lg border border-border bg-card p-4">
              {provider.attaches_keys && !usePassword ? (
                <div className="flex items-start gap-3">
                  <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-success" aria-hidden />
                  <div className="min-w-0 text-sm">
                    <div className="font-medium text-foreground-strong">
                      {t("computers.auto_key_title")}
                    </div>
                    <p className="mt-0.5 text-muted-foreground">
                      {t("computers.auto_key_body").replace("{provider}", provider.name)}
                    </p>
                    <button
                      type="button"
                      className="mt-1.5 text-sm text-accent underline-offset-4 hover:underline"
                      onClick={() => setUsePassword(true)}
                    >
                      {t("computers.use_password_instead")}
                    </button>
                  </div>
                </div>
              ) : (
                <div className="grid gap-3 sm:grid-cols-[120px_minmax(0,1fr)]">
                  <Field label={t("computers.field_username")}>
                    <input
                      className={cn(inputClass, "font-mono")}
                      value={username}
                      onChange={(e) => setUsername(e.target.value)}
                    />
                  </Field>
                  <Field label={t("computers.field_password")} hint={t("computers.password_once_hint")}>
                    <input
                      type="password"
                      className={inputClass}
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      autoComplete="new-password"
                    />
                  </Field>
                </div>
              )}
            </div>
          )}

          <ErrorNote message={error} />

          <div className="flex justify-end">
            <Button
              type="button"
              onClick={() => void importServer()}
              disabled={!selected || busy || (needsPassword && !password)}
            >
              {busy ? <Loader2 className="animate-spin" /> : <ShieldCheck />}
              {busy ? t("computers.connecting") : t("computers.connect")}
            </Button>
          </div>
        </div>
      )}

      {!connected && <ErrorNote message={error} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Door 3 — a local virtual machine
// ---------------------------------------------------------------------------

function nextFreeName(taken: string[]): string {
  if (!taken.includes("jarvis-vm")) return "jarvis-vm";
  for (let i = 2; i < 100; i += 1) {
    const candidate = `jarvis-vm-${i}`;
    if (!taken.includes(candidate)) return candidate;
  }
  return "jarvis-vm-x";
}

function LocalForm({ onAdded }: { onAdded: (computer: Computer) => void }) {
  const t = useT();
  const status = useLocalStatus();
  const taken = useMemo(() => (status.data?.instances ?? []).map((i) => i.name), [status.data]);
  const [name, setName] = useState("");
  const [image, setImage] = useState("24.04");
  const [cpus, setCpus] = useState(2);
  const [memory, setMemory] = useState(4);
  const [disk, setDisk] = useState(20);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!name && status.data) setName(nextFreeName(taken));
  }, [name, status.data, taken]);

  const nameValid = /^[a-z][a-z0-9-]{0,38}[a-z0-9]$/.test(name);

  async function create() {
    if (!nameValid || busy) return;
    setBusy(true);
    setError(null);
    try {
      const computer = await computersApi.createLocalVm({
        name,
        cpus,
        memory_gb: memory,
        disk_gb: disk,
        image,
      });
      onAdded(computer);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  if (status.isLoading) {
    return (
      <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" /> {t("computers.local_checking")}
      </div>
    );
  }

  const data = status.data;
  if (!data || !data.available) {
    return (
      <div className="space-y-4 rounded-lg border border-border bg-card p-5">
        <div>
          <div className="text-title font-medium text-foreground-strong">{t("computers.local_missing_title")}</div>
          <p className="mt-1 text-sm text-muted-foreground">{t("computers.local_missing_body")}</p>
        </div>
        {data && (
          <code className="block rounded-md border border-border bg-surface-raised px-3 py-2 font-mono text-xs text-foreground-secondary">
            {data.install_hint}
          </code>
        )}
        <div className="flex flex-wrap gap-2">
          {data && (
            <Button type="button" onClick={() => void openExternalUrl(data.install_url)}>
              <ExternalLink />
              {t("computers.local_get_multipass")}
            </Button>
          )}
          <Button type="button" variant="outline" onClick={() => void status.refetch()}>
            <RefreshCw />
            {t("computers.check_again")}
          </Button>
        </div>
        <p className="text-xs text-muted-foreground">{t("computers.local_missing_foot")}</p>
      </div>
    );
  }

  return (
    <form
      className="space-y-5"
      onSubmit={(e) => {
        e.preventDefault();
        void create();
      }}
    >
      {data.error && <ErrorNote message={data.error} />}
      <Field
        label={t("computers.field_vm_name")}
        hint={nameValid || !name ? t("computers.vm_name_hint") : t("computers.vm_name_invalid")}
      >
        <input
          className={cn(inputClass, "font-mono")}
          value={name}
          onChange={(e) => setName(e.target.value.toLowerCase())}
          spellCheck={false}
          autoFocus
        />
      </Field>
      <div className="grid gap-5 sm:grid-cols-2">
        <div>
          <div className="mb-1.5 text-sm font-medium text-foreground-secondary">{t("computers.field_image")}</div>
          <Choice
            label={t("computers.field_image")}
            value={image}
            onChange={setImage}
            options={data.images.map((i) => ({ id: i, label: `Ubuntu ${i}` }))}
          />
        </div>
        <div>
          <div className="mb-1.5 text-sm font-medium text-foreground-secondary">{t("computers.field_cpus")}</div>
          <Choice
            label={t("computers.field_cpus")}
            value={cpus}
            onChange={setCpus}
            options={[1, 2, 4, 8].map((n) => ({ id: n, label: String(n) }))}
          />
        </div>
        <div>
          <div className="mb-1.5 text-sm font-medium text-foreground-secondary">{t("computers.field_memory")}</div>
          <Choice
            label={t("computers.field_memory")}
            value={memory}
            onChange={setMemory}
            options={[2, 4, 8, 16].map((n) => ({ id: n, label: `${n} GB` }))}
          />
        </div>
        <div>
          <div className="mb-1.5 text-sm font-medium text-foreground-secondary">{t("computers.field_disk")}</div>
          <Choice
            label={t("computers.field_disk")}
            value={disk}
            onChange={setDisk}
            options={[10, 20, 40, 80].map((n) => ({ id: n, label: `${n} GB` }))}
          />
        </div>
      </div>
      <div className="flex items-start gap-3 rounded-lg border border-border bg-card p-4 text-sm">
        <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-success" aria-hidden />
        <p className="text-muted-foreground">{t("computers.local_key_note")}</p>
      </div>
      <ErrorNote message={error} />
      <div className="flex items-center justify-between gap-3">
        <span className="text-xs text-muted-foreground">
          Multipass {data.version ?? ""}
        </span>
        <Button type="submit" disabled={!nameValid || busy}>
          {busy ? <Loader2 className="animate-spin" /> : <MonitorSmartphone />}
          {t("computers.create_vm")}
        </Button>
      </div>
    </form>
  );
}

// ---------------------------------------------------------------------------
// The dialog
// ---------------------------------------------------------------------------

export function AddComputerDialog({
  initialPath,
  onClose,
  onAdded,
}: {
  initialPath: AddPath | null;
  onClose: () => void;
  onAdded: (computer: Computer) => void;
}) {
  const t = useT();
  const upsert = useUpsertComputer();
  const [path, setPath] = useState<AddPath | null>(initialPath);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const titles: Record<AddPath, string> = {
    server: t("computers.path_server_title"),
    cloud: t("computers.path_cloud_title"),
    local: t("computers.path_local_title"),
  };

  const finish = (computer: Computer) => {
    upsert(computer);
    onAdded(computer);
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-scrim/50 p-4 backdrop-blur-sm motion-safe:animate-in motion-safe:fade-in"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={t("computers.add_title")}
        data-testid="computers-add-dialog"
        className={cn(
          "max-h-[90vh] w-full overflow-y-auto rounded-xl bg-popover p-6 shadow-float scrollbar-jarvis",
          path ? "max-w-xl" : "max-w-3xl",
        )}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="mb-5 flex items-start gap-3">
          {path && initialPath === null && (
            <button
              type="button"
              onClick={() => setPath(null)}
              aria-label={t("computers.back")}
              className="mt-0.5 rounded-md p-1 text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
            >
              <ArrowLeft className="h-4 w-4" />
            </button>
          )}
          <div className="min-w-0 flex-1">
            <h2 className="text-lg font-semibold text-foreground-strong">
              {path ? titles[path] : t("computers.add_title")}
            </h2>
            <p className="mt-0.5 text-sm text-muted-foreground">
              {path ? t(`computers.path_${path}_lead`) : t("computers.add_subtitle")}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label={t("computers.close")}
            className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
          >
            <X className="h-4 w-4" />
          </button>
        </header>

        {path === null && <PathChoices onPick={setPath} />}
        {path === "server" && <ServerForm onAdded={finish} />}
        {path === "cloud" && <CloudForm onAdded={finish} />}
        {path === "local" && <LocalForm onAdded={finish} />}
      </div>
    </div>
  );
}
