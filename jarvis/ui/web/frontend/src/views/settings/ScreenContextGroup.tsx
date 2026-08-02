import { useEffect, useState } from "react";
import { Eye, Loader2 } from "lucide-react";

import { Switch } from "@/components/ui/switch";
import { useT } from "@/i18n";
import { useEventStore } from "@/store/events";

/**
 * Screen Context is deliberately a ONE-SWITCH feature (maintainer mandate
 * 2026-08-02). The privacy rules it used to expose here — denylist, extra
 * patterns, OCR, retention seconds — are still enforced at their shipped
 * defaults and remain editable through `jarvis api screen-context put-settings`
 * for anyone who wants them, but a settings card that asks a non-technical user
 * to write regular expressions is a card they switch off instead of using.
 */
interface ScreenContextSettings {
  enabled: boolean;
}

interface ScreenContextStatus {
  enabled: boolean;
  available: boolean;
  blocked_reason: string | null;
  monitor_count: number;
}

async function jsonRequest<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  const body = (await response.json().catch(() => null)) as
    | T
    | { detail?: string }
    | null;
  if (!response.ok) {
    const detail =
      body !== null &&
      typeof body === "object" &&
      "detail" in body &&
      typeof body.detail === "string"
        ? body.detail
        : "";
    throw new Error(
      detail || `Screen Context request failed (${response.status}).`,
    );
  }
  return body as T;
}

export function ScreenContextGroup() {
  const t = useT();
  const pushToast = useEventStore((state) => state.pushToast);
  const [settings, setSettings] = useState<ScreenContextSettings | null>(null);
  const [status, setStatus] = useState<ScreenContextStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let active = true;
    void Promise.all([
      jsonRequest<ScreenContextSettings>("/api/screen-context/settings"),
      jsonRequest<ScreenContextStatus>("/api/screen-context/status"),
    ])
      .then(([nextSettings, nextStatus]) => {
        if (!active) return;
        setSettings(nextSettings);
        setStatus(nextStatus);
      })
      .catch((error: Error) => pushToast("error", error.message))
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [pushToast]);

  async function toggle(enabled: boolean) {
    setSaving(true);
    try {
      await jsonRequest("/api/screen-context/settings", {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ enabled }),
      });
      setSettings((current) => (current === null ? current : { enabled }));
      setStatus(
        await jsonRequest<ScreenContextStatus>("/api/screen-context/status"),
      );
      pushToast("success", t("settings_view.screen_context.saved"));
    } catch (error) {
      pushToast("error", (error as Error).message);
    } finally {
      setSaving(false);
    }
  }

  // A blocker is only worth showing while the feature is meant to be running:
  // "switched off" is not a problem to report, it is what the switch says.
  const showBlocker = Boolean(settings?.enabled && status && !status.available);

  // Layout is deliberately byte-for-byte the neighbouring RealtimeVoiceGroup
  // shell: same wrapper, same icon placement, same spacing. The card carried a
  // `max-w-5xl` from its multi-field days, which capped it at half the window
  // and parked its switch mid-row while every other card ran full width — a
  // mismatch that only became visible once the card got short. Do not
  // reintroduce a width cap here; the settings column owns the width.
  return (
    <div className="mt-2 rounded-lg border border-border bg-card/60 p-4">
      <div className="flex items-start gap-3">
        <Eye className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-4">
            <h4 className="font-medium">
              {t("settings_view.screen_context.title")}
            </h4>
            {loading || !settings ? (
              <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
            ) : (
              <Switch
                checked={settings.enabled}
                disabled={saving}
                onCheckedChange={(enabled) => void toggle(enabled)}
              />
            )}
          </div>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {t("settings_view.screen_context.description")}
          </p>
          {settings?.enabled && status && (
            <p
              className={`mt-1.5 text-[11px] ${
                status.available ? "text-emerald-400" : "text-amber-400"
              }`}
              aria-live="polite"
            >
              {showBlocker
                ? status.blocked_reason ||
                  t("settings_view.screen_context.unavailable")
                : t("settings_view.screen_context.available").replace(
                    "{0}",
                    String(status.monitor_count),
                  )}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
