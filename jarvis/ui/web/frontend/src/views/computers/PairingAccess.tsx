import { useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";
import { pairedServersApi } from "@/lib/computersApi";
import { CopyField } from "./parts";
import { errorText } from "./wizard/shared";

/** Codes and revocation are explicit user actions; opening the dialog grants nothing. */
export function PairingAccess() {
  const t = useT();
  const [expanded, setExpanded] = useState(false);
  const [invitation, setInvitation] = useState<{ code: string; expires_at: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const acting = useRef(false);
  const clients = useQuery({ queryKey: ["computers", "paired-clients"], queryFn: pairedServersApi.clients, enabled: expanded, retry: false });

  async function act(id?: string) {
    if (acting.current) return;
    acting.current = true;
    setBusy(true);
    setError(null);
    try {
      if (id) { await pairedServersApi.revoke(id); await clients.refetch(); }
      else setInvitation(await pairedServersApi.code());
    } catch (cause) { setError(errorText(cause)); }
    finally { acting.current = false; setBusy(false); }
  }

  return (
    <div className="space-y-3 border-t border-border pt-4">
      <button type="button" aria-expanded={expanded} onClick={() => setExpanded((value) => !value)} className="text-sm text-muted-foreground hover:text-foreground">{t("computers.pair_this_server")}</button>
      {expanded && <div className="space-y-3">
        <p className="text-sm text-muted-foreground">{t("computers.pair_access_hint")}</p>
        <Button type="button" variant="outline" disabled={busy} onClick={() => void act()}>{t("computers.pair_generate")}</Button>
        {invitation && <>
          <CopyField value={invitation.code} label={t("computers.cx_pairing_code")} copyLabel={t("computers.copy")} copiedLabel={t("computers.copied")} />
          <CopyField value={`${window.location.origin}/#pairing_code=${encodeURIComponent(invitation.code)}`} label={t("computers.pair_link")} copyLabel={t("computers.copy")} copiedLabel={t("computers.copied")} />
          <p className="text-xs text-muted-foreground">{t("computers.pair_expires")} {new Date(invitation.expires_at * 1000).toLocaleTimeString()}</p>
        </>}
        {clients.data?.map((client) => <div key={client.id} className="flex items-center justify-between gap-2 text-sm">
          <span>{client.name}</span><Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => void act(client.id)}>{t("computers.pair_revoke")}</Button>
        </div>)}
        {(error || clients.isError) && <p role="alert" className="text-sm text-destructive">{error || t("computers.paired_load_failed")}</p>}
      </div>}
    </div>
  );
}
