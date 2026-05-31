import { useState } from "react";
import { Copy, Loader2, Plug, PlugZap } from "lucide-react";
import { useDisconnect, useFederationStatus } from "@/hooks/useFederation";
import { robustCopy } from "@/lib/clipboard";
import { useT } from "@/i18n";

/**
 * Settings-Section: Backend-Connection (Plan §D-Spec).
 *
 * - Disconnect-Button (Local-Only-Mode): toggled board.federation.enabled in-memory.
 * - Backend-URL: read-only Anzeige; permanente Aenderung erfolgt in jarvis.toml.
 * - Pubkey: anzeigen + kopieren.
 *
 * Plan §D §0: KEIN Online-Indikator. Wir zeigen nur ob Federation
 * aktiviert ist (ein Setting), nicht ob die Backend-Verbindung gerade
 * "live" ist.
 */
export function BackendConnectionSection() {
  const t = useT();
  const status = useFederationStatus();
  const disconnect = useDisconnect();
  const [copied, setCopied] = useState(false);

  return (
    <div className="rounded-lg border border-border bg-card/60 p-4">
      <header className="mb-3 flex items-center gap-2">
        {status.data?.enabled ? (
          <PlugZap className="h-4 w-4 text-emerald-400" />
        ) : (
          <Plug className="h-4 w-4 text-muted-foreground" />
        )}
        <h4 className="font-display text-sm font-semibold flex-1">
          {t("board_view.backend_section_title")}
        </h4>
        {status.data?.enabled && (
          <button
            type="button"
            onClick={() => disconnect.mutate()}
            disabled={disconnect.isPending}
            className="rounded-md border border-border px-2.5 py-1 text-[11px] text-muted-foreground hover:text-foreground disabled:opacity-50"
            title={t("board_view.backend_disconnect_tooltip")}
          >
            {disconnect.isPending && <Loader2 className="mr-1 inline h-3 w-3 animate-spin" />}
            {t("board_view.backend_disconnect")}
          </button>
        )}
      </header>

      {status.isLoading && (
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2 className="h-3 w-3 animate-spin" /> {t("board_view.backend_status_loading")}
        </div>
      )}

      {status.data && !status.data.enabled && (
        <p className="text-xs text-muted-foreground">
          {t("board_view.backend_local_only")}
        </p>
      )}

      {status.data?.enabled && (
        <div className="space-y-2 text-xs">
          <Row label={t("board_view.backend_url_label")}>
            <code className="font-mono text-[11px]">
              {status.data.backend_url || t("board_view.backend_url_unset")}
            </code>
            <p className="mt-1 text-[10px] text-muted-foreground">
              {t("board_view.backend_url_hint")}
            </p>
          </Row>

          {status.data.pubkey && (
            <Row label={t("board_view.backend_pubkey_label")}>
              <button
                type="button"
                onClick={() => {
                  robustCopy(status.data!.pubkey!)
                    .then((ok) => {
                      if (ok) {
                        setCopied(true);
                        setTimeout(() => setCopied(false), 1500);
                      }
                    })
                    .catch((err) => {
                      console.warn("Clipboard write failed:", err);
                    });
                }}
                className="inline-flex items-center gap-1 font-mono text-[10px] text-muted-foreground hover:text-foreground"
                title={t("board_view.backend_pubkey_copy_tooltip")}
              >
                <code>{status.data.pubkey.slice(0, 20)}…{status.data.pubkey.slice(-8)}</code>
                <Copy className="h-3 w-3" />
                {copied && <span className="ml-1 text-primary">{t("board_view.backend_pubkey_copied")}</span>}
              </button>
            </Row>
          )}
        </div>
      )}
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1 sm:flex-row sm:items-baseline sm:gap-3">
      <div className="w-32 shrink-0 text-[10px] uppercase tracking-wider text-muted-foreground">
        {label}
      </div>
      <div className="flex-1 min-w-0">{children}</div>
    </div>
  );
}
