import { useState } from "react";
import { QRCodeSVG } from "qrcode.react";
import { Copy, KeyRound, Loader2, X } from "lucide-react";
import {
  usePairAcceptFromFriend,
  usePairInitiate,
} from "@/hooks/useFederation";
import { cn } from "@/lib/utils";
import { robustCopy } from "@/lib/clipboard";
import { useT } from "@/i18n";

interface PairDialogProps {
  onClose: () => void;
}

/**
 * Pair-Dialog mit zwei Modi:
 * - "Generate"     — Owner erzeugt Token, zeigt URL + QR.
 * - "Accept"       — Friend hat URL bekommen, fuegt sie ein.
 *
 * Modal ist non-blocking — schliesst auf Backdrop-Click. Plan §D §0:
 * KEIN Pop-Up das Feed-Scrolling unterbricht; aber dieses Modal wird
 * vom User explizit geoeffnet.
 */
export function PairDialog({ onClose }: PairDialogProps) {
  const t = useT();
  const [mode, setMode] = useState<"generate" | "accept">("generate");
  const initiate = usePairInitiate();
  const accept = usePairAcceptFromFriend();
  const [acceptUrl, setAcceptUrl] = useState("");
  const [copied, setCopied] = useState(false);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-xl border border-border bg-card p-6 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="mb-4 flex items-center gap-3">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg border border-primary/40 bg-primary/10 text-primary">
            <KeyRound className="h-4 w-4" />
          </div>
          <h3 className="font-display text-base font-semibold flex-1">{t("pair_dialog.title")}</h3>
          <button type="button" onClick={onClose} aria-label={t("common.close")}
                  className="text-muted-foreground hover:text-foreground">
            <X className="h-4 w-4" />
          </button>
        </header>

        <div className="mb-4 flex gap-1.5 rounded-md border border-border bg-background/40 p-1">
          <button
            onClick={() => setMode("generate")}
            className={cn(
              "flex-1 rounded px-3 py-1.5 text-xs transition-colors",
              mode === "generate" ? "bg-primary/15 text-primary" : "text-muted-foreground"
            )}
          >{t("pair_dialog.mode_invite")}</button>
          <button
            onClick={() => setMode("accept")}
            className={cn(
              "flex-1 rounded px-3 py-1.5 text-xs transition-colors",
              mode === "accept" ? "bg-primary/15 text-primary" : "text-muted-foreground"
            )}
          >{t("pair_dialog.mode_follow")}</button>
        </div>

        {mode === "generate" && (
          <div className="space-y-3">
            <p className="text-xs text-muted-foreground">
              {t("pair_dialog.generate_hint")}
            </p>
            <button
              type="button"
              disabled={initiate.isPending}
              onClick={() => initiate.mutate()}
              className="inline-flex items-center gap-2 rounded-md border border-primary/40 bg-primary/10 px-3 py-1.5 text-xs font-medium text-primary hover:bg-primary/20"
            >
              {initiate.isPending && <Loader2 className="h-3 w-3 animate-spin" />}
              {t("pair_dialog.generate_button")}
            </button>
            {initiate.data && (
              <div className="space-y-3 rounded-lg border border-border bg-background/40 p-3">
                <div className="flex items-center justify-center">
                  <div className="rounded-md bg-white p-2">
                    <QRCodeSVG value={initiate.data.url} size={160} level="M" />
                  </div>
                </div>
                <div className="flex items-center gap-2 rounded-md border border-border bg-card/50 px-2 py-1.5">
                  <code className="flex-1 truncate font-mono text-[10px] text-muted-foreground">
                    {initiate.data.url}
                  </code>
                  <button
                    type="button"
                    onClick={() => {
                      robustCopy(initiate.data!.url)
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
                    className="inline-flex items-center gap-1 text-[10px] text-muted-foreground hover:text-foreground"
                  >
                    <Copy className="h-3 w-3" />{copied ? t("pair_dialog.copied") : t("pair_dialog.copy")}
                  </button>
                </div>
                <div className="text-[10px] text-muted-foreground">
                  {`${t("pair_dialog.valid_until")} ${new Date(initiate.data.expires_at).toLocaleString("de-DE")}`}
                </div>
              </div>
            )}
            {initiate.isError && (
              <div className="text-xs text-destructive">
                {`${t("pair_dialog.error_prefix")} ${(initiate.error as Error).message}`}
              </div>
            )}
          </div>
        )}

        {mode === "accept" && (
          <div className="space-y-3">
            <p className="text-xs text-muted-foreground">
              {t("pair_dialog.accept_hint")}
            </p>
            <input
              type="url"
              value={acceptUrl}
              onChange={(e) => setAcceptUrl(e.target.value)}
              placeholder="https://board.friend.tld/api/v1/pair/redeem?token=..."
              className="w-full rounded-md border border-border bg-background/40 px-3 py-1.5 text-xs"
            />
            <button
              type="button"
              disabled={accept.isPending || !acceptUrl}
              onClick={() => accept.mutate(acceptUrl)}
              className="inline-flex items-center gap-2 rounded-md border border-primary/40 bg-primary/10 px-3 py-1.5 text-xs font-medium text-primary hover:bg-primary/20 disabled:opacity-50"
            >
              {accept.isPending && <Loader2 className="h-3 w-3 animate-spin" />}
              {t("pair_dialog.accept_button")}
            </button>
            {accept.data && (
              <div className="rounded-md border border-emerald-500/30 bg-emerald-500/5 p-2 text-xs text-emerald-200">
                {`${t("pair_dialog.paired_with")} ${accept.data.owner_display_name}.`}
              </div>
            )}
            {accept.isError && (
              <div className="text-xs text-destructive">
                {`${t("pair_dialog.error_prefix")} ${(accept.error as Error).message}`}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
