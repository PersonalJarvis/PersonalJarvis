/**
 * "Known on this PC": the servers this PC's own ssh already knows, offered at
 * the top of the add dialog (backend: ``jarvis.computers.ssh_config``). A
 * click fills the form — address, login, port — and remembers the alias, so
 * the automatic login also offers the key file ``~/.ssh/config`` names for it.
 */
import { KeyRound, TerminalSquare } from "lucide-react";
import { useSshHosts } from "@/hooks/useComputers";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import type { SshConfigHost } from "@/lib/computersApi";

/** At most this many: a long known_hosts is history, not a menu. */
const MAX_SHOWN = 8;

export function KnownHosts({
  onPick,
  selected,
}: {
  onPick: (host: SshConfigHost) => void;
  /** The alias currently filled into the form. */
  selected?: string | null;
}) {
  const t = useT();
  const hosts = useSshHosts(true);
  const rows = (hosts.data ?? []).slice(0, MAX_SHOWN);
  if (rows.length === 0) return null;

  return (
    <section aria-labelledby="cx-known-title" data-testid="cx-known-hosts" className="space-y-2">
      <div>
        <h3 id="cx-known-title" className="text-sm font-medium text-foreground-secondary">
          {t("computers.known_title")}
        </h3>
        <p className="text-xs text-muted-foreground">{t("computers.known_body")}</p>
      </div>
      <ul className="overflow-hidden rounded-lg border border-border [&>li+li]:border-t [&>li+li]:border-border">
        {rows.map((host) => {
          const blocked = host.needs_proxy || Boolean(host.added_as);
          const note = host.needs_proxy
            ? t("computers.known_proxy")
            : host.added_as
              ? t("computers.known_added")
              : host.source === "ssh_config"
                ? t("computers.known_from_config")
                : t("computers.known_from_history");
          return (
            <li key={`${host.alias}:${host.port}`}>
              <button
                type="button"
                disabled={blocked}
                onClick={() => onPick(host)}
                aria-pressed={selected === host.alias}
                data-testid={`cx-known-${host.alias}`}
                className={cn(
                  "flex w-full items-center gap-3 px-3 py-2 text-left transition-colors",
                  "hover:bg-secondary/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
                  "disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:bg-transparent",
                  selected === host.alias && "bg-accent-soft",
                )}
              >
                <TerminalSquare aria-hidden className="h-4 w-4 shrink-0 text-muted-foreground" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium text-foreground">{host.alias}</span>
                  {/* A known_hosts entry IS its address: the second line would repeat it. */}
                  {(host.alias !== host.host || host.username || host.port !== 22) && (
                    <span className="block truncate font-mono text-xs text-muted-foreground">
                      {host.username ? `${host.username}@` : ""}
                      {host.host}
                      {host.port !== 22 ? `:${host.port}` : ""}
                    </span>
                  )}
                </span>
                {host.has_identity_file && !blocked && (
                  <KeyRound aria-label={t("computers.known_key")} className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                )}
                <span className="shrink-0 text-xs text-foreground-faint">{note}</span>
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
