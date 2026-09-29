/**
 * The Access tab: how {name} logs in to this computer, switching that
 * method (its key, a kept password, or the user's own SSH key), and the two
 * identities involved — the server's fingerprint and {name}'s public key.
 */
import { useRef, useState } from "react";
import { FileKey2, FileUp, KeyRound, Loader2, LockKeyhole, ShieldCheck } from "lucide-react";
import { Panel } from "@/components/extensions/primitives";
import { Button } from "@/components/ui/button";
import { useIdentity, useUpsertComputer } from "@/hooks/useComputers";
import { useT } from "@/i18n";
import { computersApi, type AuthMethod, type Computer } from "@/lib/computersApi";
import { CopyField, Field, inputClass } from "./parts";
import { ErrorNote, OptionCard, errorText } from "./wizard/shared";
import { PRIVATE_KEY_PLACEHOLDER } from "./connection";

export function AccessPanel({ computer }: { computer: Computer }) {
  const t = useT();
  const identity = useIdentity();
  const upsert = useUpsertComputer();
  const fileRef = useRef<HTMLInputElement>(null);
  const [method, setMethod] = useState<AuthMethod>(computer.auth);
  const [password, setPassword] = useState("");
  const [privateKey, setPrivateKey] = useState("");
  const [passphrase, setPassphrase] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const changed = method !== computer.auth || method !== "key";
  // To {name}'s key: the current login plants it, nothing to type.
  const valid =
    method === "key"
      ? computer.auth !== "key"
      : method === "password"
        ? password !== ""
        : privateKey.trim() !== "";

  async function save() {
    if (!valid || busy) return;
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      const updated = await computersApi.setCredentials(computer.id, {
        auth: method,
        password: password || undefined,
        keep_password: method === "password" ? true : undefined,
        private_key: method === "private_key" ? privateKey : undefined,
        passphrase: method === "private_key" && passphrase ? passphrase : undefined,
      });
      upsert(updated);
      setPassword("");
      setPrivateKey("");
      setPassphrase("");
      setSaved(true);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  const current = {
    key: t("computers.auth_key"),
    password: t("computers.auth_password"),
    private_key: t("computers.auth_private_key"),
  }[computer.auth];

  return (
    <div className="grid gap-5 xl:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
      <Panel className="p-5">
        <div className="mb-1 flex items-center gap-2.5">
          <ShieldCheck className="h-4 w-4 text-muted-foreground" aria-hidden />
          <h3 className="text-base font-semibold text-foreground-strong">{t("computers.access_method")}</h3>
        </div>
        <p className="mb-4 text-sm text-muted-foreground">
          {t("computers.access_current").replace("{method}", current)}
        </p>
        <div role="radiogroup" aria-label={t("computers.access_method")} className="space-y-2">
          <OptionCard
            testId="access-key"
            active={method === "key"}
            onClick={() => setMethod("key")}
            icon={<KeyRound />}
            title={t("computers.auth_key")}
            body={t("computers.auth_key_body")}
            badge={t("computers.recommended")}
          />
          <OptionCard
            testId="access-private-key"
            active={method === "private_key"}
            onClick={() => setMethod("private_key")}
            icon={<FileKey2 />}
            title={t("computers.auth_private_key")}
            body={t("computers.auth_private_key_body")}
          />
          <OptionCard
            testId="access-password"
            active={method === "password"}
            onClick={() => setMethod("password")}
            icon={<LockKeyhole />}
            title={t("computers.auth_password")}
            body={t("computers.auth_password_body")}
          />
        </div>

        {changed && (
          <div className="mt-4 space-y-3">
            {method === "key" && (
              <p className="text-sm text-muted-foreground">{t("computers.access_key_via_login")}</p>
            )}
            {method === "password" && (
              <Field label={t("computers.field_password")} hint={t("computers.password_keep_hint")}>
                <input
                  type="password"
                  className={inputClass}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoComplete="off"
                />
              </Field>
            )}
            {method === "private_key" && (
              <>
                <Field label={t("computers.wz_private_key")} hint={t("computers.wz_private_key_hint")}>
                  <textarea
                    value={privateKey}
                    onChange={(e) => setPrivateKey(e.target.value)}
                    rows={4}
                    spellCheck={false}
                    placeholder={PRIVATE_KEY_PLACEHOLDER}
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
                    onChange={(e) => {
                      const file = e.target.files?.[0];
                      if (file && file.size <= 64 * 1024) void file.text().then(setPrivateKey);
                    }}
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
              </>
            )}
            <ErrorNote message={error} />
            <div className="flex items-center justify-end gap-3">
              {saved && <span className="text-sm text-success">{t("computers.access_saved")}</span>}
              <Button type="button" disabled={!valid || busy} onClick={() => void save()} data-testid="access-save">
                {busy && <Loader2 className="animate-spin" />}
                {t("computers.access_save")}
              </Button>
            </div>
          </div>
        )}
      </Panel>

      <Panel className="space-y-5 p-5">
        <div>
          <h3 className="text-base font-semibold text-foreground-strong">{t("computers.access_identities")}</h3>
          <p className="mt-1 text-sm text-muted-foreground">{t("computers.access_identities_body")}</p>
        </div>
        {computer.host_fingerprint ? (
          <CopyField
            label={t("computers.server_identity")}
            value={computer.host_fingerprint}
            copyLabel={t("computers.copy")}
            copiedLabel={t("computers.copied")}
          />
        ) : (
          <p className="text-sm text-muted-foreground">{t("computers.access_no_fingerprint")}</p>
        )}
        {identity.data && (
          <CopyField
            multiline
            label={t("computers.jarvis_key")}
            value={identity.data.public_key}
            copyLabel={t("computers.copy")}
            copiedLabel={t("computers.copied")}
          />
        )}
      </Panel>
    </div>
  );
}
