import { useState } from "react";
import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { Field, inputClass } from "./parts";

/** Collect pairing details without sending them to the unrelated SSH API. */
export function ServerAddressForm() {
  const t = useT();
  const [host, setHost] = useState("");
  const [code, setCode] = useState("");

  function readPairingUrl(value: string): boolean {
    try {
      const url = new URL(value.trim());
      if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) return false;
      const pairingCode = url.searchParams.get("code") ?? url.searchParams.get("pairing_code");
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
    <form className="space-y-5" onSubmit={(event) => event.preventDefault()} data-testid="cx-server-form">
      <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_10rem]">
        <Field label={t("computers.cx_server_host")}>
          <input
            className={cn(inputClass, "h-12 rounded-xl px-4")}
            value={host}
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
      <Button type="submit" variant="outline" className="h-12 w-full rounded-xl text-base" disabled aria-describedby="cx-pairing-unavailable">
        <Plus />
        {t("computers.add")}
      </Button>
      <p id="cx-pairing-unavailable" className="text-sm text-muted-foreground">{t("computers.cx_pairing_unavailable")}</p>
    </form>
  );
}
