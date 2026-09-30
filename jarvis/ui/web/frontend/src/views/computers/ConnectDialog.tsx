/**
 * "Connect a computer" — one screen, and usually one field: the address.
 *
 * The address box understands a bare IP, user@host, a whole ssh command, a
 * block with a private key, or the one line our setup instructions end with,
 * so the user never splits anything into fields.
 *
 * The way in is automatic by default: the app's own key and the keys this
 * PC's ``ssh`` already uses are tried, and one that works plants the app's key
 * (the user's own key never leaves this PC). Only when none opens the server
 * does the form ask for exactly what is missing — the password once, or, for
 * a keys-only server, one line to run there. Password, a pasted key and the
 * agent route stay reachable under "Other ways to log in"; pasting a key or
 * the agent's line switches there by itself.
 *
 * "Connect" does not just spin: it opens a visible check (ConnectCheck) that
 * reaches the server, logs in, runs a test command and only then saves, so a
 * typo never leaves a broken entry behind and a success shows the machine's
 * own facts as proof. A password is used once to plant the assistant's own
 * key and is then forgotten, unless "keep" is ticked.
 *
 * Two optional doors sit below the form: connect a hosting account to pick
 * from all its servers (no IP to copy), or create a virtual machine here.
 */
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  ArrowLeft,
  Check,
  ChevronDown,
  Copy,
  FileKey,
  KeyRound,
  Lock,
  MonitorSmartphone,
  Plug,
  SquareTerminal,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { ProviderLogo } from "@/components/providers/ProviderLogo";
import { useIdentity, useProviderCatalog, useUpsertComputer } from "@/hooks/useComputers";
import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { robustCopy } from "@/lib/clipboard";
import { useEventStore } from "@/store/events";
import { computersApi, type AddServerInput, type Computer, type ProviderInfo } from "@/lib/computersApi";
import { parseConnection, setupPrompt, PRIVATE_KEY_PLACEHOLDER } from "./connection";
import { checkFailed, ConnectCheck, startCheck, type CheckState, type StepId } from "./ConnectCheck";
import { CopyField, Field, inputClass } from "./parts";
import { ApiImportStep } from "./wizard/ApiImportStep";
import { LocalVmStep } from "./wizard/LocalVmStep";
import { errorText } from "./wizard/shared";

type Screen = { kind: "form" } | { kind: "check" } | { kind: "account"; provider: ProviderInfo } | { kind: "vm" };
type Method = "auto" | "password" | "ssh_key" | "agent";
type KeySource = "own" | "assistant";
type Login = "auto" | "password" | "private_key" | "key";
/** What an automatic attempt found missing, shown in the form. */
type Missing = "password" | "key_only" | null;

const REACH_FAILURES = new Set(["unreachable", "timeout", "protocol", "host_key_changed"]);

/** One tab of the "how to log in" switch. */
function MethodTab({
  active,
  onClick,
  icon,
  label,
  testId,
}: {
  active: boolean;
  onClick: () => void;
  icon: ReactNode;
  label: string;
  testId: string;
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      data-testid={testId}
      className={cn(
        "flex min-w-0 flex-1 items-center justify-center gap-2 rounded-md px-2 py-2 text-sm font-medium transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring [&>svg]:h-4 [&>svg]:w-4 [&>svg]:shrink-0",
        active
          ? "bg-popover text-foreground-strong shadow-sm ring-1 ring-border"
          : "text-muted-foreground hover:text-foreground",
      )}
    >
      {icon}
      <span className="truncate">{label}</span>
    </button>
  );
}

/** A numbered instruction row for the agent route. */
function AgentStep({ n, children }: { n: number; children: ReactNode }) {
  return (
    <li className="flex gap-3">
      <span
        aria-hidden
        className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-border-strong text-xs font-medium tabular-nums text-foreground-secondary"
      >
        {n}
      </span>
      <div className="min-w-0 flex-1 pt-0.5 text-sm text-foreground-secondary">{children}</div>
    </li>
  );
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
  const assistantName = useEventStore((s) => s.assistantName) || "Jarvis";
  const [pasted, setPasted] = useState("");
  const [method, setMethod] = useState<Method>("auto");
  const [missing, setMissing] = useState<Missing>(null);
  const passwordInput = useRef<HTMLInputElement>(null);
  const [keySource, setKeySource] = useState<KeySource>("own");
  const [password, setPassword] = useState("");
  const [privateKey, setPrivateKey] = useState("");
  const [passphrase, setPassphrase] = useState("");
  const [more, setMore] = useState(false);
  const [name, setName] = useState("");
  const [username, setUsername] = useState("");
  const [port, setPort] = useState("");
  const [keepPassword, setKeepPassword] = useState(false);
  const [keyError, setKeyError] = useState<string | null>(null);
  const [promptCopied, setPromptCopied] = useState(false);
  const [showPrompt, setShowPrompt] = useState(false);
  const [check, setCheck] = useState<CheckState | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const accounts = useMemo(() => (catalog.data ?? []).filter((p) => p.api), [catalog.data]);
  // Everything the address box understood: address, login, port, a key.
  const detected = useMemo(() => parseConnection(pasted), [pasted]);
  const pastedKey = detected.privateKey;

  // What was pasted picks the tab: a key block means "SSH key", the agent's
  // closing line means the agent route is done.
  useEffect(() => {
    if (pastedKey) {
      setMethod("ssh_key");
      setKeySource("own");
    } else if (detected.fromSetupPrompt) {
      setMethod("agent");
    }
  }, [pastedKey, detected.fromSetupPrompt]);

  const login: Login =
    method === "auto"
      ? "auto"
      : method === "password"
        ? "password"
        : method === "ssh_key" && keySource === "own"
          ? "private_key"
          : "key";
  const effectiveUser = username.trim() || detected.user || "root";
  const effectivePort = Number(port) || detected.port || 22;
  const keyText = pastedKey ?? privateKey;
  const ready =
    Boolean(detected.host) &&
    (login === "key" ||
      login === "auto" ||
      (login === "password" ? password.length > 0 : keyText.trim().length > 0));
  const plantsKey = login === "auto" || (login === "password" && !keepPassword);

  // A different server starts over: what one was missing says nothing about the next.
  useEffect(() => {
    setMissing(null);
  }, [detected.host]);
  useEffect(() => {
    if (missing === "password" && method === "password") passwordInput.current?.focus();
  }, [missing, method]);

  const chooseMethod = (next: Method) => {
    setMethod(next);
    setMissing(null);
  };

  const stepLabels: Record<StepId, string> = {
    reach: fill(t("computers.cx_step_reach"), {
      host: `${detected.host ?? ""}${effectivePort !== 22 ? `:${effectivePort}` : ""}`,
    }),
    login: fill(t("computers.cx_step_login"), { user: effectiveUser }),
    probe: t("computers.cx_step_probe"),
    save: plantsKey ? t("computers.cx_step_save_key") : t("computers.cx_step_save"),
  };

  async function copyPrompt() {
    if (!identity.data) return;
    const ok = await robustCopy(setupPrompt(assistantName, identity.data.public_key));
    if (!ok) return;
    setPromptCopied(true);
    window.setTimeout(() => setPromptCopied(false), 2000);
  }

  async function connect() {
    if (!ready || !detected.host) return;
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
    let state = startCheck();
    const show = (next: CheckState) => {
      state = next;
      setCheck(next);
    };
    show(state);
    setScreen({ kind: "check" });

    // Steps 1-3: a dry-run login that saves and plants nothing.
    try {
      const test = await computersApi.test(input);
      if (!test.ok && login === "auto" && (test.kind === "needs_password" || test.kind === "key_only")) {
        // Not a failure to show: the form asks for exactly what is missing.
        setMissing(test.kind === "needs_password" ? "password" : "key_only");
        if (test.kind === "needs_password") setMethod("password");
        setScreen({ kind: "form" });
        return;
      }
      if (!test.ok) {
        const note = test.message || t(`computers.cx_fail_${test.kind ?? "protocol"}`);
        const atReach = !test.kind || REACH_FAILURES.has(test.kind);
        show({
          ...state,
          steps: atReach
            ? { reach: "fail", login: "wait", probe: "wait", save: "wait" }
            : { reach: "ok", login: "fail", probe: "wait", save: "wait" },
          notes: { [atReach ? "reach" : "login"]: note },
        });
        return;
      }
      show({
        ...state,
        steps: { reach: "ok", login: "ok", probe: test.facts ? "ok" : "warn", save: "run" },
        notes: test.facts ? {} : { probe: t("computers.cx_step_probe_warn") },
        facts: test.facts,
        latencyMs: test.latency_ms,
      });
    } catch (e) {
      show({ ...state, steps: { ...state.steps, reach: "fail" }, notes: { reach: errorText(e) } });
      return;
    }

    // Step 4: save for real (and plant the key when a password was used).
    try {
      const computer = await computersApi.add(input);
      upsert(computer);
      const healthy = computer.health.status === "online";
      show({
        ...state,
        steps: { ...state.steps, save: healthy ? "ok" : "warn" },
        notes: healthy
          ? state.notes
          : { ...state.notes, save: computer.health.message || t("computers.cx_step_save_warn") },
        facts: state.facts ?? computer.facts,
        latencyMs: state.latencyMs ?? computer.health.latency_ms,
        computer,
      });
    } catch (e) {
      show({ ...state, steps: { ...state.steps, save: "fail" }, notes: { ...state.notes, save: errorText(e) } });
    }
  }

  async function pickKeyFile(file: File | undefined) {
    if (!file) return;
    if (file.size > 64 * 1024) {
      setKeyError(t("computers.cx_key_too_big"));
      return;
    }
    setKeyError(null);
    setPrivateKey(await file.text());
  }

  const title =
    screen.kind === "account"
      ? t("computers.cx_account_title").replace("{provider}", screen.provider.name)
      : screen.kind === "vm"
        ? t("computers.cx_vm_title")
        : screen.kind === "check"
          ? check?.computer
            ? fill(t("computers.wz_done_title"), { computer: check.computer.name })
            : check && checkFailed(check)
              ? t("computers.cx_check_failed")
              : t("computers.cx_check_title")
          : t("computers.cx_title");

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
          {screen.kind !== "form" && !(screen.kind === "check" && check?.computer) && (
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
            <h2 className="text-lg font-semibold text-foreground-strong">{title}</h2>
            {screen.kind === "form" && (
              <p className="mt-0.5 text-sm text-muted-foreground">{t("computers.cx_subtitle")}</p>
            )}
            {screen.kind === "check" && check?.computer && (
              <p className="mt-0.5 text-sm text-muted-foreground">{t("computers.cx_verified_body")}</p>
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
              onAdded={(computer) => {
                upsert(computer);
                onOpen(computer, "overview");
              }}
              onProviderChanged={() => void catalog.refetch()}
            />
          )}
          {screen.kind === "vm" && (
            <LocalVmStep
              onBack={() => setScreen({ kind: "form" })}
              onAdded={(computer) => {
                upsert(computer);
                onOpen(computer, "overview");
              }}
            />
          )}
          {screen.kind === "check" && check && (
            <ConnectCheck
              check={check}
              labels={stepLabels}
              onEdit={() => setScreen({ kind: "form" })}
              onRetry={() => void connect()}
              onOpen={onOpen}
            />
          )}

          {screen.kind === "form" && (
            <form
              className="space-y-5"
              onSubmit={(e) => {
                e.preventDefault();
                void connect();
              }}
            >
              <div>
                <Field label={t("computers.cx_paste")} hint={t("computers.cx_paste_hint")}>
                  <textarea
                    className={cn(inputClass, "h-auto min-h-[40px] resize-none py-2 font-mono text-sm")}
                    rows={pasted.includes("\n") ? 4 : 1}
                    value={pasted}
                    onChange={(e) => setPasted(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && !e.shiftKey && !pasted.includes("\n")) {
                        e.preventDefault();
                        void connect();
                      }
                    }}
                    placeholder="203.0.113.10"
                    autoFocus
                    spellCheck={false}
                    autoComplete="off"
                    data-testid="cx-address"
                  />
                </Field>
                {detected.host && (
                  <div className="mt-2 flex flex-wrap items-center gap-1.5 text-xs" data-testid="cx-detected">
                    <span className="text-muted-foreground">{t("computers.cx_detected")}</span>
                    <span className="rounded bg-secondary px-1.5 py-0.5 font-mono text-foreground-secondary">
                      {effectiveUser}@{detected.host}
                      {effectivePort !== 22 ? `:${effectivePort}` : ""}
                    </span>
                    {pastedKey && (
                      <span className="inline-flex items-center gap-1 rounded bg-success/15 px-1.5 py-0.5 text-success">
                        <KeyRound className="h-3 w-3" aria-hidden />
                        {t("computers.cx_key_found")}
                      </span>
                    )}
                  </div>
                )}
              </div>

              {method === "auto" && (
                <div className="space-y-3" data-testid="cx-auto">
                  {missing === "key_only" ? (
                    <div className="space-y-3 rounded-lg border border-border bg-secondary/40 p-4" data-testid="cx-key-only">
                      <div className="flex items-center gap-2 text-sm font-medium text-foreground-strong">
                        <KeyRound className="h-4 w-4 text-muted-foreground" aria-hidden />
                        {t("computers.cx_key_only_title")}
                      </div>
                      <p className="text-sm text-foreground-secondary">{t("computers.cx_key_only_body")}</p>
                      {identity.data?.install_command && (
                        <CopyField
                          value={identity.data.install_command}
                          label={t("computers.cx_key_only_line")}
                          copyLabel={t("computers.copy")}
                          copiedLabel={t("computers.copied")}
                        />
                      )}
                      <button
                        type="button"
                        onClick={() => chooseMethod("agent")}
                        className="text-xs text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
                      >
                        {t("computers.cx_key_only_agent")}
                      </button>
                    </div>
                  ) : (
                    <p className="flex items-start gap-2 text-sm text-muted-foreground">
                      <KeyRound className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
                      {t("computers.cx_auto_hint")}
                    </p>
                  )}
                  <button
                    type="button"
                    onClick={() => chooseMethod("password")}
                    className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
                    data-testid="cx-other-ways"
                  >
                    <ChevronDown className="h-3.5 w-3.5" aria-hidden />
                    {t("computers.cx_other_ways")}
                  </button>
                </div>
              )}

              {method !== "auto" && (
              <div>
                <div className="mb-1.5 flex items-center justify-between gap-3">
                  <div className="text-sm font-medium text-foreground-secondary" id="cx-login-label">
                    {t("computers.cx_login_title")}
                  </div>
                  <button
                    type="button"
                    onClick={() => chooseMethod("auto")}
                    className="text-xs text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
                    data-testid="cx-auto-back"
                  >
                    {t("computers.cx_auto_back")}
                  </button>
                </div>
                <div
                  role="tablist"
                  aria-labelledby="cx-login-label"
                  className="flex gap-1 rounded-lg bg-secondary p-1"
                >
                  <MethodTab
                    active={method === "password"}
                    onClick={() => chooseMethod("password")}
                    icon={<Lock />}
                    label={t("computers.cx_method_password")}
                    testId="cx-method-password"
                  />
                  <MethodTab
                    active={method === "ssh_key"}
                    onClick={() => chooseMethod("ssh_key")}
                    icon={<KeyRound />}
                    label={t("computers.cx_method_key")}
                    testId="cx-method-key"
                  />
                  <MethodTab
                    active={method === "agent"}
                    onClick={() => chooseMethod("agent")}
                    icon={<SquareTerminal />}
                    label={t("computers.cx_method_agent")}
                    testId="cx-method-agent"
                  />
                </div>

                <div className="mt-4" role="tabpanel">
                  {method === "password" && (
                    <div className="space-y-3">
                      {missing === "password" && (
                        <p className="rounded-md bg-secondary/60 px-3 py-2.5 text-sm text-foreground-secondary" data-testid="cx-ask-password">
                          {t("computers.cx_ask_password")}
                        </p>
                      )}
                      <Field label={t("computers.cx_password")} hint={t("computers.cx_password_hint")}>
                        <input
                          ref={passwordInput}
                          type="password"
                          className={cn(inputClass, "h-10")}
                          value={password}
                          onChange={(e) => setPassword(e.target.value)}
                          autoComplete="new-password"
                          data-testid="cx-password"
                        />
                      </Field>
                      <label className="flex items-center gap-2 text-sm text-foreground-secondary">
                        <input
                          type="checkbox"
                          checked={keepPassword}
                          onChange={(e) => setKeepPassword(e.target.checked)}
                          className="h-4 w-4"
                        />
                        {t("computers.cx_keep_password")}
                      </label>
                    </div>
                  )}

                  {method === "ssh_key" && (
                    <div className="space-y-4">
                      <div role="radiogroup" className="grid grid-cols-2 gap-2">
                        {(["own", "assistant"] as const).map((source) => (
                          <button
                            key={source}
                            type="button"
                            role="radio"
                            aria-checked={keySource === source}
                            onClick={() => setKeySource(source)}
                            data-testid={`cx-key-${source}`}
                            className={cn(
                              "rounded-md border px-3 py-2 text-left text-sm transition-colors",
                              keySource === source
                                ? "border-accent bg-accent-soft text-foreground-strong"
                                : "border-border text-foreground-secondary hover:border-border-strong",
                            )}
                          >
                            <span className="block font-medium">
                              {source === "own" ? t("computers.cx_key_own") : t("computers.cx_key_assistant")}
                            </span>
                            <span className="mt-0.5 block text-xs text-muted-foreground">
                              {source === "own"
                                ? t("computers.cx_key_own_body")
                                : t("computers.cx_key_assistant_body")}
                            </span>
                          </button>
                        ))}
                      </div>

                      {keySource === "own" && pastedKey && (
                        <p className="flex items-center gap-2 text-sm text-foreground-secondary">
                          <Check className="h-4 w-4 text-success" aria-hidden />
                          {t("computers.cx_key_from_paste")}
                        </p>
                      )}

                      {keySource === "own" && !pastedKey && (
                        <div className="space-y-2">
                          <Field label={t("computers.cx_private_key")} hint={t("computers.cx_private_key_hint")}>
                            <textarea
                              className={cn(inputClass, "h-24 resize-none py-2 font-mono text-xs")}
                              value={privateKey}
                              onChange={(e) => setPrivateKey(e.target.value)}
                              placeholder={PRIVATE_KEY_PLACEHOLDER}
                              spellCheck={false}
                              data-testid="cx-private-key"
                            />
                          </Field>
                          <Button type="button" variant="outline" size="sm" onClick={() => fileInput.current?.click()}>
                            <FileKey />
                            {t("computers.cx_key_file")}
                          </Button>
                          <input
                            ref={fileInput}
                            type="file"
                            className="hidden"
                            onChange={(e) => void pickKeyFile(e.target.files?.[0])}
                          />
                          {keyError && (
                            <p role="alert" className="text-sm text-destructive">
                              {keyError}
                            </p>
                          )}
                        </div>
                      )}

                      {keySource === "own" && (
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

                      {keySource === "assistant" && identity.data && (
                        <div className="space-y-2" data-testid="cx-assistant-key">
                          <CopyField
                            value={identity.data.public_key}
                            label={t("computers.jarvis_key")}
                            copyLabel={t("computers.copy")}
                            copiedLabel={t("computers.copied")}
                          />
                          <p className="text-xs text-muted-foreground">{t("computers.cx_key_assistant_hint")}</p>
                        </div>
                      )}
                    </div>
                  )}

                  {method === "agent" && (
                    <div className="space-y-4" data-testid="cx-agent">
                      {detected.fromSetupPrompt ? (
                        <p className="flex items-start gap-2 rounded-md bg-success/10 px-3 py-2.5 text-sm text-foreground">
                          <Check className="mt-0.5 h-4 w-4 shrink-0 text-success" aria-hidden />
                          {t("computers.cx_agent_done")}
                        </p>
                      ) : (
                        <ol className="space-y-3">
                          <AgentStep n={1}>
                            <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                              <span>{t("computers.cx_agent_step1")}</span>
                              <Button
                                type="button"
                                variant="outline"
                                size="sm"
                                disabled={!identity.data}
                                onClick={() => void copyPrompt()}
                                data-testid="cx-copy-prompt"
                              >
                                {promptCopied ? <Check className="text-success" /> : <Copy />}
                                {promptCopied ? t("computers.copied") : t("computers.cx_agent_copy")}
                              </Button>
                              <button
                                type="button"
                                onClick={() => setShowPrompt((v) => !v)}
                                aria-expanded={showPrompt}
                                className="text-xs text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
                              >
                                {showPrompt ? t("computers.cx_agent_hide") : t("computers.cx_agent_show")}
                              </button>
                            </div>
                            {showPrompt && identity.data && (
                              <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap rounded-md border border-border bg-surface-raised p-3 font-mono text-xs text-foreground-secondary scrollbar-jarvis">
                                {setupPrompt(assistantName, identity.data.public_key)}
                              </pre>
                            )}
                          </AgentStep>
                          <AgentStep n={2}>{t("computers.cx_agent_step2")}</AgentStep>
                          <AgentStep n={3}>{t("computers.cx_agent_step3")}</AgentStep>
                        </ol>
                      )}
                    </div>
                  )}
                </div>
              </div>
              )}

              <div>
                <button
                  type="button"
                  onClick={() => setMore((v) => !v)}
                  aria-expanded={more}
                  className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
                >
                  <ChevronDown className={cn("h-3.5 w-3.5 transition-transform", more && "rotate-180")} aria-hidden />
                  {t("computers.cx_more")}
                </button>
                {more && (
                  <div className="mt-3 grid gap-3 sm:grid-cols-[minmax(0,1fr)_110px_80px]">
                    <Field label={t("computers.field_name")}>
                      <input
                        className={inputClass}
                        value={name}
                        onChange={(e) => setName(e.target.value)}
                        placeholder={detected.host || t("computers.field_name_placeholder")}
                      />
                    </Field>
                    <Field label={t("computers.field_username")}>
                      <input
                        className={cn(inputClass, "font-mono")}
                        value={username}
                        onChange={(e) => setUsername(e.target.value)}
                        placeholder={detected.user || "root"}
                        spellCheck={false}
                      />
                    </Field>
                    <Field label={t("computers.field_port")}>
                      <input
                        className={cn(inputClass, "font-mono")}
                        value={port}
                        inputMode="numeric"
                        onChange={(e) => setPort(e.target.value.replace(/[^0-9]/g, ""))}
                        placeholder={String(detected.port ?? 22)}
                      />
                    </Field>
                  </div>
                )}
              </div>

              <div>
                <Button type="submit" className="h-10 w-full" disabled={!ready} data-testid="cx-connect">
                  <Plug />
                  {missing === "key_only" && method === "auto" ? t("computers.cx_try_again") : t("computers.cx_connect")}
                </Button>
                <p className="mt-2 text-center text-xs text-muted-foreground">{t("computers.cx_connect_hint")}</p>
              </div>
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
