import { useState } from "react";
import { Copy, Eye, EyeOff, KeyRound, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useJarvisApi } from "@/hooks/useJarvisApi";
import { robustCopy } from "@/lib/clipboard";
import { useEventStore } from "@/store/events";
import { useT } from "@/i18n";
import { SettingsBlock } from "@/views/settings/SettingsBlock";

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
    <SettingsBlock
      icon={KeyRound}
      title={t("settings_view.jarvis_api.title")}
      description={t("settings_view.jarvis_api.description")}
    >
      <div className="space-y-3">
        <div className="flex items-center gap-2">
          <code className="min-w-0 flex-1 truncate rounded-lg bg-muted px-3 py-2 font-mono text-xs">
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
          <Button size="sm" variant="ghost" disabled={busy || loading} onClick={onRotate}>
            <RefreshCw className="mr-1 h-3.5 w-3.5" />
            {t("settings_view.jarvis_api.regenerate_button")}
          </Button>
        </div>

        <p className="text-xs text-muted-foreground">
          {t("settings_view.jarvis_api.usage_hint")}
        </p>

        {error && <p className="text-xs text-destructive">{error}</p>}
      </div>
    </SettingsBlock>
  );
}
