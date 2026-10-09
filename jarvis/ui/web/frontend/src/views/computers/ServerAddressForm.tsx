import { useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { Field, inputClass } from "./parts";
import { pairedServersApi } from "@/lib/computersApi";
import { pairedServerKey } from "./PairedServers";
import { PairingAccess } from "./PairingAccess";
import { errorText } from "./wizard/shared";

/** Pair a real independent server and persist only its public metadata in the UI cache. */
export function ServerAddressForm({ onConnected }: { onConnected: () => void }) {
  const t = useT();
  const [host, setHost] = useState("");
  const [code, setCode] = useState("");
  const queryClient = useQueryClient();
  const submitting = useRef(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function connect() {
    if (!host.trim() || !code.trim() || submitting.current) return;
    submitting.current = true;
    setBusy(true);
    setError(null);
    try {
      await pairedServersApi.add(host.trim(), code.trim());
      setCode("");
      await queryClient.invalidateQueries({ queryKey: pairedServerKey });
      onConnected();
    } catch (cause) { setError(errorText(cause)); }
    finally { submitting.current = false; setBusy(false); }
  }

  function readPairingUrl(value: string): boolean {
    try {
      const url = new URL(value.trim());
      if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) return false;
      const fragment = new URLSearchParams(url.hash.slice(1));
      const pairingCode = fragment.get("pairing_code") ?? url.searchParams.get("code") ?? url.searchParams.get("pairing_code");
      if (!pairingCode?.trim()) return false;
      setHost(url.origin);
      setCode(pairingCode);
      return true;
    } catch {
      // Plain host names are valid field input, but are not pairing URLs.
      return false;
    }
  }

  return (
    <form className="space-y-5" onSubmit={(event) => { event.preventDefault(); void connect(); }} data-testid="cx-server-form">
      <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_10rem]">
        <Field label={t("computers.cx_server_host")}>
          <input
            className={cn(inputClass, "h-12 rounded-xl px-4")}
            value={host}
            disabled={busy}
            onChange={(event) => setHost(event.target.value)}
            onBlur={() => readPairingUrl(host)}
            onPaste={(event) => {
              if (readPairingUrl(event.clipboardData.getData("text"))) event.preventDefault();
            }}
            placeholder="backend.example.com"
            autoComplete="off"
            spellCheck={false}
            aria-describedby="cx-pairing-hint"
            data-testid="cx-server-host"
          />
        </Field>
        <Field label={t("computers.cx_pairing_code")}>
          <input
            className={cn(inputClass, "h-12 rounded-xl px-4")}
            value={code}
            disabled={busy}
            onChange={(event) => setCode(event.target.value)}
            placeholder="PAIRCODE"
            autoComplete="off"
            autoCapitalize="none"
            spellCheck={false}
            data-testid="cx-pairing-code"
          />
        </Field>
      </div>
      <p id="cx-pairing-hint" className="text-sm text-muted-foreground">{t("computers.cx_pairing_hint")}</p>
      <Button type="submit" variant="outline" className="h-12 w-full rounded-xl text-base" disabled={busy || !host.trim() || !code.trim()}>
        {busy ? <Loader2 className="animate-spin" /> : <Plus />}
        {t("computers.add")}
      </Button>
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      <p className="text-xs text-muted-foreground">{t("computers.paired_connection_hint")}</p>
      <PairingAccess />
    </form>
  );
}
