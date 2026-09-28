/**
 * "Connect a computer" — one screen, because SSH needs only two things: where
 * the server is, and a way in (the password once, or an SSH key). Which
 * company runs the server does not matter for that.
 *
 * Two optional doors sit below the form: connect a hosting account to pick
 * from all its servers (no IP to copy), or create a virtual machine here.
 *
 * "Connect" tests first and saves only a connection that works, so a typo
 * never leaves a broken entry behind. A password is used once to plant the
 * assistant's own key and is then forgotten, unless "keep" is ticked.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, ChevronDown, KeyRound, Loader2, Lock, MonitorSmartphone, Plug, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ProviderLogo } from "@/components/providers/ProviderLogo";
import { useIdentity, useProviderCatalog, useUpsertComputer } from "@/hooks/useComputers";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { computersApi, type AddServerInput, type Computer, type ProviderInfo } from "@/lib/computersApi";
import { CopyField, Field, inputClass } from "./parts";
import { ApiImportStep } from "./wizard/ApiImportStep";
import { LocalVmStep } from "./wizard/LocalVmStep";
import { ErrorNote, errorText } from "./wizard/shared";

type Screen = { kind: "form" } | { kind: "account"; provider: ProviderInfo } | { kind: "vm" };
type Login = "password" | "private_key" | "key";

/** "root@203.0.113.10:2222" → user, host, port (each optional in the input). */
export function parseAddress(raw: string): { user: string | null; host: string; port: number | null } {
  let rest = raw.trim().replace(/^ssh\s+/i, "");
  let user: string | null = null;
  const at = rest.lastIndexOf("@");
  if (at > 0) {
    user = rest.slice(0, at);
    rest = rest.slice(at + 1);
  }
  let port: number | null = null;
  const bracket = rest.match(/^\[(.+)\](?::(\d+))?$/);
  if (bracket) {
    rest = bracket[1];
    port = bracket[2] ? Number(bracket[2]) : null;
  } else if ((rest.match(/:/g) ?? []).length === 1) {
    const [h, p] = rest.split(":");
    if (/^\d+$/.test(p)) {
      rest = h;
      port = Number(p);
    }
  }
  return { user, host: rest, port };
}

export function ConnectDialog({
  onClose,
  onOpen,
}: {
  onClose: () => void;
  onOpen: (computer: Computer, tab: "overview" | "agents") => void;
}) {
  const t = useT();
  const upsert = useUpsertComputer();
  const catalog = useProviderCatalog();
  const identity = useIdentity();
  const [screen, setScreen] = useState<Screen>({ kind: "form" });
  const [address, setAddress] = useState("");
  const [login, setLogin] = useState<Login>("password");
  const [password, setPassword] = useState("");
  const [privateKey, setPrivateKey] = useState("");
  const [passphrase, setPassphrase] = useState("");
  const [more, setMore] = useState(false);
  const [name, setName] = useState("");
  const [username, setUsername] = useState("");
  const [port, setPort] = useState("");
  const [keepPassword, setKeepPassword] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const accounts = useMemo(() => (catalog.data ?? []).filter((p) => p.api), [catalog.data]);
  const parsed = parseAddress(address);
  const effectiveUser = username.trim() || parsed.user || "root";
  const effectivePort = Number(port) || parsed.port || 22;
  const ready =
    parsed.host.length > 0 &&
    (login === "key" || (login === "password" ? password.length > 0 : privateKey.trim().length > 0));

  const finish = (computer: Computer) => {
    upsert(computer);
    onOpen(computer, "overview");
  };

  async function connect() {
    if (!ready || busy) return;
    setBusy(true);
    setError(null);
    const input: AddServerInput = {
      name: name.trim() || parsed.host,
      host: parsed.host,
      port: effectivePort,
      username: effectiveUser,
      auth: login,
      password: login === "password" ? password : undefined,
      keep_password: login === "password" ? keepPassword : undefined,
      private_key: login === "private_key" ? privateKey : undefined,
      passphrase: login === "private_key" && passphrase ? passphrase : undefined,
      provider: "generic",
    };
    try {
      const test = await computersApi.test(input);
      if (!test.ok) {
        setError(test.message || t(`computers.cx_fail_${test.kind ?? "protocol"}`));
        return;
      }
      finish(await computersApi.add(input));
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  async function pickKeyFile(file: File | undefined) {
    if (!file) return;
    if (file.size > 64 * 1024) {
      setError(t("computers.cx_key_too_big"));
      return;
    }
    setPrivateKey(await file.text());
  }

  const loginTabs: { id: Login; label: string; icon: typeof Lock }[] = [
    { id: "password", label: t("computers.cx_login_password"), icon: Lock },
    { id: "private_key", label: t("computers.cx_login_key"), icon: KeyRound },
  ];

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-scrim/50 p-4 backdrop-blur-sm">
      <div
        role="dialog"
        aria-modal="true"
        aria-label={t("computers.cx_title")}
        data-testid="computers-connect-dialog"
        className="flex max-h-[90vh] w-full max-w-lg flex-col overflow-hidden rounded-xl border border-border bg-popover shadow-float"
      >
        <header className="flex items-start gap-3 border-b border-border px-6 py-5">
          {screen.kind !== "form" && (
            <button
              type="button"
              onClick={() => setScreen({ kind: "form" })}
              aria-label={t("computers.back")}
              className="mt-0.5 rounded-md p-1 text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
            >
              <ArrowLeft className="h-4 w-4" />
            </button>
          )}
          <div className="min-w-0 flex-1">
            <h2 className="text-lg font-semibold text-foreground-strong">
              {screen.kind === "account"
                ? t("computers.cx_account_title").replace("{provider}", screen.provider.name)
                : screen.kind === "vm"
                  ? t("computers.cx_vm_title")
                  : t("computers.cx_title")}
            </h2>
            {screen.kind === "form" && (
              <p className="mt-0.5 text-sm text-muted-foreground">{t("computers.cx_subtitle")}</p>
            )}
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

        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5 scrollbar-jarvis">
          {screen.kind === "account" && (
            <ApiImportStep
              provider={screen.provider}
              onBack={() => setScreen({ kind: "form" })}
              onAdded={finish}
              onProviderChanged={() => void catalog.refetch()}
            />
          )}
          {screen.kind === "vm" && <LocalVmStep onBack={() => setScreen({ kind: "form" })} onAdded={finish} />}

          {screen.kind === "form" && (
            <form
              className="space-y-5"
              onSubmit={(e) => {
                e.preventDefault();
                void connect();
              }}
            >
              <Field label={t("computers.cx_address")} hint={t("computers.cx_address_hint")}>
                <input
                  className={cn(inputClass, "h-10 font-mono")}
                  value={address}
                  onChange={(e) => setAddress(e.target.value)}
                  placeholder="root@203.0.113.10"
                  autoFocus
                  spellCheck={false}
                  autoComplete="off"
                  data-testid="cx-address"
                />
              </Field>

              <div>
                <div role="tablist" aria-label={t("computers.cx_login")} className="flex gap-1 rounded-lg bg-secondary p-1">
                  {loginTabs.map((tab) => {
                    const Icon = tab.icon;
                    const active = login === tab.id;
                    return (
                      <button
                        key={tab.id}
                        type="button"
                        role="tab"
                        aria-selected={active}
                        onClick={() => setLogin(tab.id)}
                        className={cn(
                          "flex h-8 flex-1 items-center justify-center gap-1.5 rounded-md text-sm font-medium transition-colors",
                          active ? "bg-popover text-foreground-strong shadow-sm" : "text-muted-foreground hover:text-foreground",
                        )}
                      >
                        <Icon className="h-3.5 w-3.5" aria-hidden />
                        {tab.label}
                      </button>
                    );
                  })}
                </div>

                <div className="mt-4">
                  {login === "password" && (
                    <Field label={t("computers.cx_password")} hint={t("computers.cx_password_hint")}>
                      <input
                        type="password"
                        className={cn(inputClass, "h-10")}
                        value={password}
                        onChange={(e) => setPassword(e.target.value)}
                        autoComplete="new-password"
                        data-testid="cx-password"
                      />
                    </Field>
                  )}
                  {login === "private_key" && (
                    <div className="space-y-3">
                      <Field label={t("computers.cx_private_key")} hint={t("computers.cx_private_key_hint")}>
                        <textarea
                          className={cn(inputClass, "h-24 resize-none py-2 font-mono text-xs")}
                          value={privateKey}
                          onChange={(e) => setPrivateKey(e.target.value)}
                          placeholder="-----BEGIN OPENSSH PRIVATE KEY-----"
                          spellCheck={false}
                          data-testid="cx-private-key"
                        />
                      </Field>
                      <div className="flex items-center gap-3">
                        <Button type="button" variant="outline" size="sm" onClick={() => fileInput.current?.click()}>
                          {t("computers.cx_key_file")}
                        </Button>
                        <input
                          ref={fileInput}
                          type="file"
                          className="hidden"
                          onChange={(e) => void pickKeyFile(e.target.files?.[0])}
                        />
                        <input
                          type="password"
                          className={cn(inputClass, "h-8 flex-1 text-sm")}
                          value={passphrase}
                          onChange={(e) => setPassphrase(e.target.value)}
                          placeholder={t("computers.cx_passphrase")}
                          aria-label={t("computers.cx_passphrase")}
                          autoComplete="off"
                        />
                      </div>
                    </div>
                  )}
                  {login === "key" && identity.data && (
                    <CopyField
                      multiline
                      label={t("computers.cx_own_key_hint")}
                      value={identity.data.public_key}
                      copyLabel={t("computers.copy")}
                      copiedLabel={t("computers.copied")}
                    />
                  )}
                  <button
                    type="button"
                    onClick={() => setLogin(login === "key" ? "password" : "key")}
                    className="mt-2 text-xs text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
                  >
                    {login === "key" ? t("computers.cx_use_password") : t("computers.cx_key_already_there")}
                  </button>
                </div>
              </div>

              <div>
                <button
                  type="button"
                  onClick={() => setMore((v) => !v)}
                  aria-expanded={more}
                  className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
                >
                  <ChevronDown className={cn("h-4 w-4 transition-transform", more && "rotate-180")} aria-hidden />
                  {t("computers.cx_more")}
                </button>
                {more && (
                  <div className="mt-3 grid gap-3 sm:grid-cols-[minmax(0,1fr)_110px_80px]">
                    <Field label={t("computers.field_name")}>
                      <input className={inputClass} value={name} onChange={(e) => setName(e.target.value)} placeholder={parsed.host || t("computers.field_name_placeholder")} />
                    </Field>
                    <Field label={t("computers.field_username")}>
                      <input className={cn(inputClass, "font-mono")} value={username} onChange={(e) => setUsername(e.target.value)} placeholder={parsed.user || "root"} spellCheck={false} />
                    </Field>
                    <Field label={t("computers.field_port")}>
                      <input className={cn(inputClass, "font-mono")} value={port} inputMode="numeric" onChange={(e) => setPort(e.target.value.replace(/[^0-9]/g, ""))} placeholder={String(parsed.port ?? 22)} />
                    </Field>
                    {login === "password" && (
                      <label className="col-span-full flex items-center gap-2 text-sm text-foreground-secondary">
                        <input type="checkbox" checked={keepPassword} onChange={(e) => setKeepPassword(e.target.checked)} className="h-4 w-4" />
                        {t("computers.cx_keep_password")}
                      </label>
                    )}
                  </div>
                )}
              </div>

              <ErrorNote message={error} />

              <Button type="submit" className="h-10 w-full" disabled={!ready || busy} data-testid="cx-connect">
                {busy ? <Loader2 className="animate-spin" /> : <Plug />}
                {busy ? t("computers.connecting") : t("computers.cx_connect")}
              </Button>
            </form>
          )}
        </div>

        {screen.kind === "form" && (
          <footer className="space-y-3 border-t border-border bg-secondary/30 px-6 py-4">
            {accounts.length > 0 && (
              <div>
                <div className="text-sm font-medium text-foreground-secondary">{t("computers.cx_accounts_title")}</div>
                <p className="text-xs text-muted-foreground">{t("computers.cx_accounts_body")}</p>
                <div className="mt-2.5 flex flex-wrap gap-1.5">
                  {accounts.map((provider) => (
                    <button
                      key={provider.id}
                      type="button"
                      data-testid={`cx-account-${provider.id}`}
                      onClick={() => setScreen({ kind: "account", provider })}
                      className="inline-flex h-8 items-center gap-2 rounded-md border border-border bg-popover px-2.5 text-sm text-foreground transition-colors hover:border-border-strong"
                    >
                      <ProviderLogo providerId={provider.id} label={provider.name} size="sm" />
                      {provider.name}
                      {provider.api?.connected && <span className="h-1.5 w-1.5 rounded-full bg-success" aria-hidden />}
                    </button>
                  ))}
                </div>
              </div>
            )}
            <button
              type="button"
              onClick={() => setScreen({ kind: "vm" })}
              className="inline-flex items-center gap-1.5 text-xs text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
            >
              <MonitorSmartphone className="h-3.5 w-3.5" aria-hidden />
              {t("computers.cx_vm_link")}
            </button>
          </footer>
        )}
      </div>
    </div>
  );
}
