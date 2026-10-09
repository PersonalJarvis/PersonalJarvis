import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, Loader2, RefreshCw, Unplug } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";
import { pairedServersApi } from "@/lib/computersApi";
import { openExternalUrl } from "@/lib/openExternal";
import { Section } from "./surface";
import { errorText } from "./wizard/shared";

export const pairedServerKey = ["computers", "paired-servers"] as const;

export function usePairedServers() {
  return useQuery({ queryKey: pairedServerKey, queryFn: pairedServersApi.list, retry: false });
}

export function PairedServers() {
  const t = useT();
  const queryClient = useQueryClient();
  const servers = usePairedServers();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [openingLink, setOpeningLink] = useState<string | null>(null);
  const acting = useRef(false);

  async function act(id: string, action: "open" | "check" | "remove") {
    if (acting.current) return;
    acting.current = true;
    setBusy(id);
    setError(null);
    setOpeningLink(null);
    try {
      if (action === "open") {
        const result = await pairedServersApi.open(id);
        // Keep a clickable fallback for browsers that block asynchronous popups.
        if (!await openExternalUrl(result.url)) setOpeningLink(result.url);
      } else {
        if (action === "check") await pairedServersApi.check(id);
        else await pairedServersApi.remove(id);
        await queryClient.invalidateQueries({ queryKey: pairedServerKey });
      }
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      acting.current = false;
      setBusy(null);
    }
  }

  if (servers.isError) return <p role="alert" className="text-sm text-destructive">{t("computers.paired_load_failed")}</p>;
  if (!servers.data?.length) return null;
  return (
    <Section title={t("computers.paired_servers")}>
      <ul className="divide-y divide-border" data-testid="paired-servers">
        {servers.data.map((server) => (
          <li key={server.id} className="flex flex-wrap items-center gap-3 px-4 py-3">
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium text-foreground">{server.name}</p>
              <p className="truncate text-xs text-muted-foreground">{server.url} · {server.platform}</p>
              <p className="text-xs text-muted-foreground">{t(server.online ? "computers.paired_verified" : "computers.paired_offline")}</p>
            </div>
            <Button variant="outline" size="sm" onClick={() => void act(server.id, "open")} disabled={busy !== null}>
              {busy === server.id ? <Loader2 className="animate-spin" /> : <ExternalLink />}{t("computers.paired_open")}
            </Button>
            <Button variant="ghost" size="icon" aria-label={t("computers.check_again")} disabled={busy !== null} onClick={() => void act(server.id, "check")}><RefreshCw /></Button>
            <Button variant="ghost" size="icon" aria-label={t("computers.paired_disconnect")} disabled={busy !== null} onClick={() => void act(server.id, "remove")}><Unplug /></Button>
          </li>
        ))}
      </ul>
      {error && <p role="alert" className="px-4 py-3 text-sm text-destructive">{error}</p>}
      {openingLink && <a href={openingLink} target="_blank" rel="noopener noreferrer" className="block px-4 py-3 text-sm underline">{t("computers.paired_open")}</a>}
    </Section>
  );
}
