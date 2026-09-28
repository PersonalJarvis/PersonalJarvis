/**
 * Step 3 (SSH) — the address, the login and the credential for the chosen
 * method, pre-filled from the provider, then a real connection test.
 *
 * "Add computer" becomes the primary action only after the test succeeded:
 * the user sees that {name} got in (and which system answered) before
 * anything is saved. Every failure comes back as one plain sentence with the
 * fix, keyed on the kind of failure.
 */
import { useEffect, useRef, useState } from "react";
import { CheckCircle2, ExternalLink, FileUp, Loader2, PlugZap, XCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useIdentity } from "@/hooks/useComputers";
import { useT } from "@/i18n";
import { openExternalUrl } from "@/lib/openExternal";
import { cn } from "@/lib/utils";
import {
  computersApi,
  type AddServerInput,
  type Computer,
  type ConnectionTest,
  type ProviderInfo,
} from "@/lib/computersApi";
import { CopyField, Field, inputClass } from "../parts";
import { ErrorNote, errorText, type Method } from "./shared";
import { StepActions } from "./StepActions";

const MAX_KEY_FILE_BYTES = 64 * 1024;

export function DetailsStep({
  provider,
  method,
  onBack,
  onAdded,
}: {
  provider: ProviderInfo;
  method: Exclude<Method, "api">;
  onBack: () => void;
  onAdded: (computer: Computer) => void;
}) {
  const t = useT();
  const identity = useIdentity();
  const fileRef = useRef<HTMLInputElement>(null);
  const [host, setHost] = useState("");
  const [port, setPort] = useState("22");
  const [username, setUsername] = useState(provider.ssh.default_username || "root");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [keepPassword, setKeepPassword] = useState(false);
  const [privateKey, setPrivateKey] = useState("");
  const [passphrase, setPassphrase] = useState("");
  const [testing, setTesting] = useState(false);
  const [adding, setAdding] = useState(false);
  const [result, setResult] = useState<ConnectionTest | null>(null);
  const [error, setError] = useState<string | null>(null);

  const portNumber = Number(port);
  const input: AddServerInput = {
    name: name.trim() || host.trim(),
    host: host.trim(),
    port: portNumber,
    username: username.trim(),
    auth: method === "password" ? "password" : method === "private_key" ? "private_key" : "key",
    password: method === "password" ? password : undefined,
    keep_password: method === "password" ? keepPassword : undefined,
    private_key: method === "private_key" ? privateKey : undefined,
    passphrase: method === "private_key" && passphrase ? passphrase : undefined,
    provider: provider.id,
  };
  const valid =
    input.host !== "" &&
    input.username !== "" &&
    Number.isInteger(portNumber) &&
    portNumber >= 1 &&
    portNumber <= 65535 &&
    (method !== "password" || password !== "") &&
    (method !== "private_key" || privateKey.trim() !== "");
  const fingerprint = JSON.stringify(input);

  // Any change after a test invalidates it: the next "Add" must be proven again.
  useEffect(() => {
    setResult(null);
  }, [fingerprint]);

  async function runTest() {
    if (!valid || testing) return;
    setTesting(true);
    setError(null);
    try {
      setResult(await computersApi.test(input));
    } catch (e) {
      setError(errorText(e));
    } finally {
      setTesting(false);
    }
  }

  async function add() {
    if (!result?.ok || adding) return;
    setAdding(true);
    setError(null);
    try {
      onAdded(await computersApi.add(input));
    } catch (e) {
      setError(errorText(e));
    } finally {
      setAdding(false);
    }
  }

  async function readKeyFile(file: File | undefined) {
    if (!file) return;
    if (file.size > MAX_KEY_FILE_BYTES) {
      setError(t("computers.wz_key_file_too_big"));
      return;
    }
    setPrivateKey(await file.text());
  }

  return (
    <div className="flex h-full flex-col">
      <form
        className="flex-1 space-y-5"
        onSubmit={(e) => {
          e.preventDefault();
          void runTest();
        }}
      >
        <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_96px]">
          <Field label={t("computers.field_host")} hint={provider.ssh.ip_hint || t("computers.field_host_hint")}>
            <input
              className={cn(inputClass, "font-mono")}
              value={host}
              onChange={(e) => setHost(e.target.value)}
              placeholder="203.0.113.10"
              autoFocus
              spellCheck={false}
              autoComplete="off"
              data-testid="wz-host"
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
          <Field label={t("computers.field_username")}>
            <input
              className={cn(inputClass, "font-mono")}
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              spellCheck={false}
              autoComplete="off"
            />
          </Field>
          <Field label={t("computers.field_name")}>
            <input
              className={inputClass}
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={provider.name}
            />
          </Field>
        </div>

        {method === "password" && (
          <div className="space-y-2">
            <Field label={t("computers.field_password")} hint={provider.ssh.password_hint || t("computers.password_once_hint")}>
              <input
                type="password"
                className={inputClass}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="new-password"
                data-testid="wz-password"
              />
            </Field>
            <label className="flex items-center gap-2 text-sm text-muted-foreground">
              <input
                type="checkbox"
                checked={keepPassword}
                onChange={(e) => setKeepPassword(e.target.checked)}
                className="h-4 w-4 accent-[hsl(var(--accent))]"
              />
              {t("computers.wz_keep_password")}
            </label>
          </div>
        )}

        {method === "private_key" && (
          <div className="space-y-3">
            <Field label={t("computers.wz_private_key")} hint={t("computers.wz_private_key_hint")}>
              <textarea
                value={privateKey}
                onChange={(e) => setPrivateKey(e.target.value)}
                rows={5}
                spellCheck={false}
                placeholder="-----BEGIN OPENSSH PRIVATE KEY-----"
                data-testid="wz-private-key"
                className="w-full rounded-md border border-border-strong bg-input px-3 py-2 font-mono text-xs text-foreground placeholder:text-foreground-faint focus:border-accent focus:outline-none focus:ring-2 focus:ring-ring"
              />
            </Field>
            <div className="flex flex-wrap items-end gap-4">
              <Button type="button" variant="outline" size="sm" onClick={() => fileRef.current?.click()}>
                <FileUp />
                {t("computers.wz_pick_key_file")}
              </Button>
              <input
                ref={fileRef}
                type="file"
                className="hidden"
                onChange={(e) => void readKeyFile(e.target.files?.[0])}
              />
              <Field label={t("computers.wz_passphrase")} className="min-w-[200px] flex-1">
                <input
                  type="password"
                  className={inputClass}
                  value={passphrase}
                  onChange={(e) => setPassphrase(e.target.value)}
                  placeholder={t("computers.wz_passphrase_placeholder")}
                  autoComplete="off"
                />
              </Field>
            </div>
          </div>
        )}

        {method === "jarvis_key" && (
          <div className="space-y-3 rounded-lg border border-border bg-card p-4">
            <p className="text-sm text-foreground-secondary">
              {provider.ssh.key_hint || t("computers.key_paste_hint")}
            </p>
            {identity.data && (
              <CopyField
                multiline
                label={t("computers.jarvis_key")}
                value={identity.data.public_key}
                copyLabel={t("computers.copy")}
                copiedLabel={t("computers.copied")}
              />
            )}
            {provider.ssh.key_url && (
              <button
                type="button"
                onClick={() => void openExternalUrl(provider.ssh.key_url ?? "")}
                className="inline-flex items-center gap-1 text-sm font-medium text-accent underline-offset-4 hover:underline"
              >
                {t("computers.wz_open_key_page").replace("{provider}", provider.name)}
                <ExternalLink className="h-3.5 w-3.5" aria-hidden />
              </button>
            )}
          </div>
        )}

        <TestResultRow result={result} />
        <ErrorNote message={error} />
      </form>

      <StepActions onBack={onBack}>
        <Button
          type="button"
          variant={result?.ok ? "outline" : "default"}
          disabled={!valid || testing}
          onClick={() => void runTest()}
          data-testid="wz-test"
        >
          {testing ? <Loader2 className="animate-spin" /> : <PlugZap />}
          {testing ? t("computers.connecting") : t("computers.wz_test")}
        </Button>
        <Button
          type="button"
          variant={result?.ok ? "default" : "outline"}
          disabled={!result?.ok || adding}
          onClick={() => void add()}
          data-testid="wz-add"
        >
          {adding && <Loader2 className="animate-spin" />}
          {t("computers.add")}
        </Button>
      </StepActions>
    </div>
  );
}

function TestResultRow({ result }: { result: ConnectionTest | null }) {
  const t = useT();
  if (!result) return null;
  if (result.ok) {
    const facts = [result.facts?.os_name, result.latency_ms !== null ? `${result.latency_ms} ms` : null]
      .filter(Boolean)
      .join(" · ");
    return (
      <div
        role="status"
        data-testid="wz-test-ok"
        className="flex items-start gap-3 rounded-lg border border-success/40 bg-success/10 px-4 py-3"
      >
        <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-success" aria-hidden />
        <div className="min-w-0 text-sm">
          <div className="font-medium text-foreground-strong">
            {t("computers.wz_test_ok")}
            {facts && <span className="font-normal text-muted-foreground"> · {facts}</span>}
          </div>
          {result.host_fingerprint && (
            <div className="mt-0.5 truncate font-mono text-xs text-muted-foreground" title={result.host_fingerprint}>
              {t("computers.server_identity")}: {result.host_fingerprint}
            </div>
          )}
        </div>
      </div>
    );
  }
  return (
    <div
      role="alert"
      data-testid="wz-test-failed"
      className="flex items-start gap-3 rounded-lg border border-destructive/40 bg-destructive/10 px-4 py-3"
    >
      <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" aria-hidden />
      <div className="min-w-0 text-sm">
        <div className="font-medium text-foreground-strong">{result.message || t("computers.wz_test_failed")}</div>
        {result.kind && <div className="mt-0.5 text-muted-foreground">{t(`computers.wz_fix_${result.kind}`)}</div>}
      </div>
    </div>
  );
}
