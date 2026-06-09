import { useState } from "react";
import { Copy, Eye, EyeOff, KeyRound, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useJarvisApi } from "@/hooks/useJarvisApi";
import { robustCopy } from "@/lib/clipboard";
import { useEventStore } from "@/store/events";
import { useT } from "@/i18n";

/**
 * "Jarvis API" group inside the Settings view. Shows the per-user Control API
 * key (masked by default, with a Show/Hide toggle), a one-click Copy, and a
 * Regenerate button. The key authenticates the local Control API
 * (``/api/control/*``) so local coding agents (Codex CLI, Claude Code) can drive
 * Jarvis — change settings, switch providers, switch language — over HTTP
 * instead of via Computer-Use. Reveal/rotate are loopback-permitted so this
 * panel works before the user possesses the key.
 */
export function JarvisApiGroup() {
  const t = useT();
  const { data, loading, error, rotate } = useJarvisApi();
  const pushToast = useEventStore((s) => s.pushToast);
  const [revealed, setRevealed] = useState(false);
  const [busy, setBusy] = useState(false);

  const key = data?.key ?? "";
  const display = revealed ? key : (data?.masked ?? "…");

  async function onCopy() {
    if (!key) return;
    const ok = await robustCopy(key);
    pushToast(
      ok ? "success" : "error",
      ok
        ? t("settings_view.jarvis_api.copied_toast")
        : t("settings_view.jarvis_api.copy_failed_toast"),
    );
  }

  async function onRotate() {
    setBusy(true);
    try {
      await rotate();
      setRevealed(false);
      pushToast("success", t("settings_view.jarvis_api.regenerated_toast"));
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-8 space-y-4">
      <h3 className="font-display text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {t("settings_view.jarvis_api.title")}
      </h3>
      <div className="rounded-lg border border-border bg-card/60 p-4">
        <div className="flex items-start gap-3">
          <KeyRound className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
          <div className="min-w-0 flex-1">
            <h4 className="font-medium">{t("settings_view.jarvis_api.key_label")}</h4>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {t("settings_view.jarvis_api.description")}
            </p>

            <div className="mt-3 flex items-center gap-2">
              <code className="min-w-0 flex-1 truncate rounded bg-muted px-2 py-1 font-mono text-xs">
                {display}
              </code>
              <Button
                size="sm"
                variant="outline"
                disabled={loading || !key}
                onClick={() => setRevealed((v) => !v)}
              >
                {revealed ? (
                  <>
                    <EyeOff className="mr-1 h-3.5 w-3.5" />
                    {t("settings_view.jarvis_api.hide")}
                  </>
                ) : (
                  <>
                    <Eye className="mr-1 h-3.5 w-3.5" />
                    {t("settings_view.jarvis_api.show")}
                  </>
                )}
              </Button>
              <Button size="sm" variant="outline" disabled={loading || !key} onClick={onCopy}>
                <Copy className="mr-1 h-3.5 w-3.5" />
                {t("settings_view.jarvis_api.copy_button")}
              </Button>
            </div>

            <div className="mt-3 flex items-center gap-2">
              <Button size="sm" variant="ghost" disabled={busy || loading} onClick={onRotate}>
                <RefreshCw className="mr-1 h-3.5 w-3.5" />
                {t("settings_view.jarvis_api.regenerate_button")}
              </Button>
            </div>

            <p className="mt-3 text-xs text-muted-foreground">
              {t("settings_view.jarvis_api.usage_hint")}
            </p>

            {error && <p className="mt-3 text-xs text-destructive">{error}</p>}
          </div>
        </div>
      </div>
    </div>
  );
}
