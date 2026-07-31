import { useEffect, useState } from "react";
import { Eye, Loader2, Shield, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { useT } from "@/i18n";
import { useEventStore } from "@/store/events";

interface ScreenContextSettings {
  enabled: boolean;
  denylist: string[];
  sensitive_patterns: string[];
  include_default_patterns: boolean;
  max_text_chars: number;
  ttl_s: number;
  ocr_enabled: boolean;
}

interface ScreenContextStatus {
  available: boolean;
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

function lines(value: string): string[] {
  return value
    .split("\n")
    .map((item) => item.trim())
    .filter(Boolean);
}

export function ScreenContextGroup() {
  const t = useT();
  const pushToast = useEventStore((state) => state.pushToast);
  const [settings, setSettings] = useState<ScreenContextSettings | null>(null);
  const [status, setStatus] = useState<ScreenContextStatus | null>(null);
  const [denylist, setDenylist] = useState("");
  const [patterns, setPatterns] = useState("");
  const [ttl, setTtl] = useState("120");
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
        setDenylist(nextSettings.denylist.join("\n"));
        setPatterns(nextSettings.sensitive_patterns.join("\n"));
        setTtl(String(nextSettings.ttl_s));
      })
      .catch((error: Error) => pushToast("error", error.message))
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [pushToast]);

  async function save(patch: Partial<ScreenContextSettings>) {
    if (!settings) return;
    setSaving(true);
    try {
      await jsonRequest("/api/screen-context/settings", {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(patch),
      });
      setSettings({ ...settings, ...patch });
      pushToast("success", t("settings_view.screen_context.saved"));
    } catch (error) {
      pushToast("error", (error as Error).message);
    } finally {
      setSaving(false);
    }
  }

  async function discard() {
    setSaving(true);
    try {
      await jsonRequest("/api/screen-context", { method: "DELETE" });
      pushToast("success", t("settings_view.screen_context.discarded"));
    } catch (error) {
      pushToast("error", (error as Error).message);
    } finally {
      setSaving(false);
    }
  }

  const ttlValue = Number(ttl);
  const ttlValid = Number.isFinite(ttlValue) && ttlValue >= 1 && ttlValue <= 600;

  return (
    <section className="mt-2 rounded-lg border border-border bg-card/60 p-4">
      <div className="flex items-start gap-3">
        <Eye className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-4">
            <h4 className="font-medium">{t("settings_view.screen_context.title")}</h4>
            {loading || !settings ? (
              <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
            ) : (
              <Switch
                checked={settings.enabled}
                disabled={saving}
                onCheckedChange={(enabled) => void save({ enabled })}
              />
            )}
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            {t("settings_view.screen_context.description")}
          </p>
          {status && (
            <p
              className={`mt-2 text-[11px] ${
                status.available ? "text-emerald-400" : "text-amber-400"
              }`}
              aria-live="polite"
            >
              {status.available
                ? t("settings_view.screen_context.available").replace(
                    "{0}",
                    String(status.monitor_count),
                  )
                : t("settings_view.screen_context.unavailable")}
            </p>
          )}

          {settings && (
            <div className="mt-4 space-y-3 border-t border-border/70 pt-4">
              <label className="block text-xs font-medium">
                {t("settings_view.screen_context.denylist")}
                <textarea
                  value={denylist}
                  disabled={saving}
                  onChange={(event) => setDenylist(event.target.value)}
                  placeholder={t("settings_view.screen_context.denylist_placeholder")}
                  className="mt-1.5 min-h-20 w-full resize-y rounded-md border border-border bg-background px-3 py-2 font-mono text-xs"
                />
              </label>
              <label className="block text-xs font-medium">
                {t("settings_view.screen_context.patterns")}
                <textarea
                  value={patterns}
                  disabled={saving}
                  onChange={(event) => setPatterns(event.target.value)}
                  placeholder={t("settings_view.screen_context.patterns_placeholder")}
                  className="mt-1.5 min-h-20 w-full resize-y rounded-md border border-border bg-background px-3 py-2 font-mono text-xs"
                />
              </label>
              <div className="flex items-center justify-between gap-4 text-xs">
                <span>{t("settings_view.screen_context.default_patterns")}</span>
                <Switch
                  checked={settings.include_default_patterns}
                  disabled={saving}
                  onCheckedChange={(include_default_patterns) =>
                    void save({ include_default_patterns })
                  }
                />
              </div>
              <div className="flex items-center justify-between gap-4 text-xs">
                <span>{t("settings_view.screen_context.ocr")}</span>
                <Switch
                  checked={settings.ocr_enabled}
                  disabled={saving}
                  onCheckedChange={(ocr_enabled) => void save({ ocr_enabled })}
                />
              </div>
              <label className="flex items-center justify-between gap-4 text-xs">
                <span>{t("settings_view.screen_context.retention_label")}</span>
                <input
                  type="number"
                  min={1}
                  max={600}
                  step={1}
                  value={ttl}
                  disabled={saving}
                  onChange={(event) => setTtl(event.target.value)}
                  className="w-24 rounded-md border border-border bg-background px-3 py-1.5 text-right text-xs"
                />
              </label>
              <div className="flex flex-wrap gap-2">
                <Button
                  size="sm"
                  disabled={saving || !ttlValid}
                  onClick={() =>
                    void save({
                      denylist: lines(denylist),
                      sensitive_patterns: lines(patterns),
                      ttl_s: ttlValue,
                    })
                  }
                >
                  <Shield className="mr-1.5 h-3.5 w-3.5" />
                  {t("settings_view.screen_context.save_privacy")}
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={saving}
                  onClick={() => void discard()}
                >
                  <Trash2 className="mr-1.5 h-3.5 w-3.5" />
                  {t("settings_view.screen_context.discard")}
                </Button>
              </div>
              <p className="text-[11px] text-muted-foreground">
                {t("settings_view.screen_context.retention").replace(
                  "{0}",
                  String(settings.ttl_s),
                )}
              </p>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
