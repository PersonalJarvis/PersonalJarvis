import { useCallback, useEffect, useState } from "react";
import { QRCodeSVG } from "qrcode.react";
import { Loader2, QrCode, Smartphone } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { useEventStore } from "@/store/events";
import { useT } from "@/i18n";
import { SettingsBlock } from "@/views/settings/SettingsBlock";

interface LanState {
  enabled: boolean;
  running: boolean;
  lan_ip: string | null;
  lan_port: number;
}

/**
 * "Phone access" — opens Jarvis to a phone on the same home network.
 *
 * The backend serves the app over HTTPS on this PC's private LAN address
 * (jarvis/ui/web/lan_access.py). The switch is saved and takes effect on the
 * next start; once the listener runs, "Pair a phone" shows a QR code with a
 * ONE-TIME sign-in link. A phone that scans it gets its own session; the
 * link is useless afterwards.
 */
export function PhoneAccessGroup() {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const [state, setState] = useState<LanState | null>(null);
  const [busy, setBusy] = useState(false);
  const [pairUrl, setPairUrl] = useState<string | null>(null);

  const refetch = useCallback(async () => {
    try {
      const res = await fetch("/api/settings/lan-access");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setState((await res.json()) as LanState);
    } catch (e) {
      pushToast("error", (e as Error).message);
    }
  }, [pushToast]);

  useEffect(() => {
    void refetch();
  }, [refetch]);

  async function onToggle(next: boolean) {
    setBusy(true);
    try {
      const res = await fetch("/api/settings/lan-access", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: next }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail ?? `HTTP ${res.status}`);
      setPairUrl(null);
      await refetch();
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function onPair() {
    setBusy(true);
    try {
      const res = await fetch("/api/control/lan/pair");
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail ?? `HTTP ${res.status}`);
      setPairUrl(body.url ?? null);
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const enabled = state?.enabled ?? false;
  const running = state?.running ?? false;

  return (
    <SettingsBlock
      icon={Smartphone}
      title={t("settings_view.phone_access.title")}
      description={t("settings_view.phone_access.description")}
    >
      <div className="space-y-3">
        <div className="flex items-start gap-3 rounded-lg border border-border bg-card p-4">
          <div className="min-w-0 flex-1">
            <div className="text-sm font-medium">{t("settings_view.phone_access.switch_title")}</div>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {state?.lan_ip
                ? t("settings_view.phone_access.switch_description").replace(
                    "{ip}",
                    `${state.lan_ip}:${state.lan_port}`,
                  )
                : t("settings_view.phone_access.no_network")}
            </p>
          </div>
          <Switch
            checked={enabled}
            disabled={state === null || busy || !state.lan_ip}
            aria-label={t("settings_view.phone_access.switch_title")}
            onCheckedChange={(next) => void onToggle(next)}
          />
        </div>

        {enabled !== running && (
          <p className="text-xs text-muted-foreground" role="status">
            {t("settings_view.phone_access.restart_hint")}
          </p>
        )}

        {enabled && running && (
          <div className="space-y-3">
            <Button size="sm" variant="outline" disabled={busy} onClick={() => void onPair()}>
              {busy ? (
                <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
              ) : (
                <QrCode className="mr-1 h-3.5 w-3.5" />
              )}
              {t("settings_view.phone_access.pair_button")}
            </Button>
            {pairUrl && (
              <div className="space-y-2">
                <div className="flex items-center justify-center">
                  {/* QR codes need dark-on-light in both themes to scan. */}
                  <div className="rounded-md bg-white p-2">
                    <QRCodeSVG value={pairUrl} size={180} level="M" />
                  </div>
                </div>
                <p className="text-xs text-muted-foreground">
                  {t("settings_view.phone_access.pair_hint")}
                </p>
                <p className="text-xs text-muted-foreground">
                  {t("settings_view.phone_access.certificate_hint")}
                </p>
              </div>
            )}
          </div>
        )}
      </div>
    </SettingsBlock>
  );
}
