/**
 * "Connect a computer" — one screen, because SSH needs only two things: where
 * the server is, and a way in. The easiest way in is the copyable setup
 * prompt: a coding agent on that computer installs the assistant's PUBLIC key
 * and prints one line to paste back. The paste box also understands a bare IP,
 * user@host, a whole ssh command or a block with a private key, so the user
 * never has to split anything into fields. The password field appears only
 * when nothing pasted says how to log in.
 *
 * Two optional doors sit below the form: connect a hosting account to pick
 * from all its servers (no IP to copy), or create a virtual machine here.
 *
 * "Connect" tests first and saves only a connection that works, so a typo
 * never leaves a broken entry behind. A password is used once to plant the
 * assistant's own key and is then forgotten, unless "keep" is ticked.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, Check, ChevronDown, Copy, KeyRound, Loader2, MonitorSmartphone, Plug, Sparkles, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ProviderLogo } from "@/components/providers/ProviderLogo";
import { useIdentity, useProviderCatalog, useUpsertComputer } from "@/hooks/useComputers";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { robustCopy } from "@/lib/clipboard";
import { useEventStore } from "@/store/events";
import { computersApi, type AddServerInput, type Computer, type ProviderInfo } from "@/lib/computersApi";
import { parseConnection, setupPrompt } from "./connection";
import { Field, inputClass } from "./parts";
import { ApiImportStep } from "./wizard/ApiImportStep";
import { LocalVmStep } from "./wizard/LocalVmStep";
import { ErrorNote, errorText } from "./wizard/shared";

type Screen = { kind: "form" } | { kind: "account"; provider: ProviderInfo } | { kind: "vm" };
type Login = "password" | "private_key" | "key";

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
  const assistantName = useEventStore((s) => s.assistantName) || "Jarvis";
  const [pasted, setPasted] = useState("");
  const [manualKey, setManualKey] = useState(false);
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
  const [promptCopied, setPromptCopied] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const accounts = useMemo(() => (catalog.data ?? []).filter((p) => p.api), [catalog.data]);
  // Everything the paste box understood: address, login, port, a key.
  const detected = useMemo(() => parseConnection(pasted), [pasted]);
  const pastedKey = detected.privateKey;
  // The login follows what was pasted: a key block wins, then our own setup
  // prompt's result (the assistant's key is installed), else the password.
  const login: Login = pastedKey || manualKey ? "private_key" : detected.fromSetupPrompt ? "key" : "password";
  const effectiveUser = username.trim() || detected.user || "root";
  const effectivePort = Number(port) || detected.port || 22;
  const keyText = pastedKey ?? privateKey;
  const ready =
    Boolean(detected.host) &&
    (login === "key" || (login === "password" ? password.length > 0 : keyText.trim().length > 0));

  const finish = (computer: Computer) => {
    upsert(computer);
    onOpen(computer, "overview");
  };

  async function copyPrompt() {
    if (!identity.data) return;
    const ok = await robustCopy(setupPrompt(assistantName, identity.data.public_key));
    if (!ok) return;
    setPromptCopied(true);
    window.setTimeout(() => setPromptCopied(false), 2000);
  }

  async function connect() {
    if (!ready || busy || !detected.host) return;
    setBusy(true);
    setError(null);
    const input: AddServerInput = {
      name: name.trim() || detected.host,
      host: detected.host,
      port: effectivePort,
      username: effectiveUser,
      auth: login,
      password: login === "password" ? password : undefined,
      keep_password: login === "password" ? keepPassword : undefined,
      private_key: login === "private_key" ? keyText : undefined,
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
              <div className="rounded-lg border border-border bg-secondary/40 p-4" data-testid="cx-setup">
                <div className="flex items-start gap-3">
                  <Sparkles className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
                  <div className="min-w-0 flex-1">
                    <div className="text-sm font-medium text-foreground-strong">{t("computers.cx_setup_title")}</div>
                    <p className="mt-0.5 text-xs text-muted-foreground">{t("computers.cx_setup_body")}</p>
                  </div>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={!identity.data}
                    onClick={() => void copyPrompt()}
                    data-testid="cx-copy-prompt"
                  >
                    {promptCopied ? <Check className="text-success" /> : <Copy />}
                    {promptCopied ? t("computers.copied") : t("computers.cx_setup_copy")}
                  </Button>
                </div>
              </div>

              <Field label={t("computers.cx_paste")} hint={t("computers.cx_paste_hint")}>
                <textarea
                  className={cn(inputClass, "h-auto min-h-[40px] resize-none py-2 font-mono text-sm")}
                  rows={pasted.includes("\n") ? 4 : 1}
                  value={pasted}
                  onChange={(e) => setPasted(e.target.value)}
                  placeholder="203.0.113.10"
                  autoFocus
                  spellCheck={false}
                  autoComplete="off"
                  data-testid="cx-address"
                />
              </Field>

              {detected.host && (
                <div className="flex flex-wrap items-center gap-1.5 text-xs" data-testid="cx-detected">
                  <span className="text-muted-foreground">{t("computers.cx_detected")}</span>
                  <span className="rounded bg-secondary px-1.5 py-0.5 font-mono text-foreground-secondary">
                    {effectiveUser}@{detected.host}
                    {effectivePort !== 22 ? `:${effectivePort}` : ""}
                  </span>
                  {login === "private_key" && (
                    <span className="inline-flex items-center gap-1 rounded bg-success/15 px-1.5 py-0.5 text-success">
                      <KeyRound className="h-3 w-3" aria-hidden />
                      {t("computers.cx_key_found")}
                    </span>
                  )}
                  {login === "key" && (
                    <span className="inline-flex items-center gap-1 rounded bg-success/15 px-1.5 py-0.5 text-success">
                      <Check className="h-3 w-3" aria-hidden />
                      {t("computers.cx_setup_done")}
                    </span>
                  )}
                </div>
              )}

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

              {manualKey && login === "private_key" && !pastedKey && (
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
                  <Button type="button" variant="outline" size="sm" onClick={() => fileInput.current?.click()}>
                    {t("computers.cx_key_file")}
                  </Button>
                  <input ref={fileInput} type="file" className="hidden" onChange={(e) => void pickKeyFile(e.target.files?.[0])} />
                </div>
              )}

              {login === "private_key" && (
                <input
                  type="password"
                  className={cn(inputClass, "h-9 text-sm")}
                  value={passphrase}
                  onChange={(e) => setPassphrase(e.target.value)}
                  placeholder={t("computers.cx_passphrase")}
                  aria-label={t("computers.cx_passphrase")}
                  autoComplete="off"
                />
              )}

              <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
                {!pastedKey && !detected.fromSetupPrompt && (
                  <button
                    type="button"
                    onClick={() => setManualKey((v) => !v)}
                    className="text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
                  >
                    {manualKey ? t("computers.cx_use_password") : t("computers.cx_have_key")}
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => setMore((v) => !v)}
                  aria-expanded={more}
                  className="inline-flex items-center gap-1 text-muted-foreground hover:text-foreground"
                >
                  <ChevronDown className={cn("h-3.5 w-3.5 transition-transform", more && "rotate-180")} aria-hidden />
                  {t("computers.cx_more")}
                </button>
              </div>

              {more && (
                <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_110px_80px]">
                  <Field label={t("computers.field_name")}>
                    <input className={inputClass} value={name} onChange={(e) => setName(e.target.value)} placeholder={detected.host || t("computers.field_name_placeholder")} />
                  </Field>
                  <Field label={t("computers.field_username")}>
                    <input className={cn(inputClass, "font-mono")} value={username} onChange={(e) => setUsername(e.target.value)} placeholder={detected.user || "root"} spellCheck={false} />
                  </Field>
                  <Field label={t("computers.field_port")}>
                    <input className={cn(inputClass, "font-mono")} value={port} inputMode="numeric" onChange={(e) => setPort(e.target.value.replace(/[^0-9]/g, ""))} placeholder={String(detected.port ?? 22)} />
                  </Field>
                  {login === "password" && (
                    <label className="col-span-full flex items-center gap-2 text-sm text-foreground-secondary">
                      <input type="checkbox" checked={keepPassword} onChange={(e) => setKeepPassword(e.target.checked)} className="h-4 w-4" />
                      {t("computers.cx_keep_password")}
                    </label>
                  )}
                </div>
              )}

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
