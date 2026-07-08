import { useEffect, useState } from "react";
import { Check, Loader2, Users } from "lucide-react";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";
import { Switch } from "@/components/ui/switch";
import { Button } from "@/components/ui/button";
import {
  SettingsBlock,
  SettingsField,
  settingsInputCls,
} from "@/views/settings/SettingsBlock";

const TEAM_PROXY_ENDPOINT = "/api/settings/team-proxy";
const TOKEN_SECRET_ENDPOINT = "/api/secrets/team_proxy_token";

// Providers that CAN be routed through the key proxy today. The checkboxes pick
// which to keep LOCAL/direct (the exception list) — e.g. local Whisper that
// must never leave the machine.
const PROXIABLE_PROVIDERS = [
  "claude-api",
  "openai",
  "openrouter",
  "gemini",
  "groq-api",
] as const;

interface TeamProxyState {
  enabled: boolean;
  url: string;
  local_providers: string[];
  token_configured: boolean;
}

/**
 * "Team Mode" group inside the Settings view (2026-06-20 team-proxy spec). One
 * global switch: when enabled with a proxy URL, every provider not in the
 * "keep local" list is routed through the shared key proxy using a per-user
 * token instead of a real vendor key. The token is a secret stored via the
 * normal /secrets route; the rest persists to [team_proxy] in jarvis.toml.
 */
export function TeamProxyGroup() {
  const t = useT();
  const [enabled, setEnabled] = useState(false);
  const [url, setUrl] = useState("");
  const [local, setLocal] = useState<Set<string>>(new Set());
  const [token, setToken] = useState("");
  const [tokenConfigured, setTokenConfigured] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const res = await fetch(TEAM_PROXY_ENDPOINT);
        if (!res.ok) return;
        const data: TeamProxyState = await res.json();
        if (cancelled) return;
        setEnabled(data.enabled);
        setUrl(data.url ?? "");
        setLocal(new Set(data.local_providers ?? []));
        setTokenConfigured(Boolean(data.token_configured));
      } catch {
        /* settings unreachable (headless) — leave defaults */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const toggleLocal = (provider: string) => {
    setLocal((prev) => {
      const next = new Set(prev);
      if (next.has(provider)) next.delete(provider);
      else next.add(provider);
      return next;
    });
  };

  const save = async () => {
    setSaving(true);
    setSaved(false);
    setError(null);
    try {
      // Persist the token first (only when the user typed a new one).
      if (token.trim()) {
        const tokRes = await fetch(TOKEN_SECRET_ENDPOINT, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ value: token.trim() }),
        });
        if (!tokRes.ok) throw new Error(`token ${tokRes.status}`);
        setTokenConfigured(true);
        setToken("");
      }
      const res = await fetch(TEAM_PROXY_ENDPOINT, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          enabled,
          url: url.trim(),
          local_providers: Array.from(local),
        }),
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail?.detail ?? `settings ${res.status}`);
      }
      setSaved(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <SettingsBlock
      icon={Users}
      title={t("settings_view.team_proxy.title")}
      description={t("settings_view.team_proxy.hint")}
      headerRight={
        <Switch
          checked={enabled}
          onCheckedChange={setEnabled}
          aria-label={t("settings_view.team_proxy.enable_label")}
        />
      }
    >
      <div className="space-y-4">
        {enabled && (
          <div className="space-y-4">
            <div className="grid gap-4 sm:grid-cols-2">
              <SettingsField label={t("settings_view.team_proxy.url_label")}>
                <input
                  type="url"
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  placeholder="https://keys.example.dev"
                  className={settingsInputCls}
                />
              </SettingsField>

              <SettingsField label={t("settings_view.team_proxy.token_label")}>
                <input
                  type="password"
                  value={token}
                  onChange={(e) => setToken(e.target.value)}
                  placeholder={
                    tokenConfigured
                      ? t("settings_view.team_proxy.token_set_placeholder")
                      : t("settings_view.team_proxy.token_placeholder")
                  }
                  className={settingsInputCls}
                />
              </SettingsField>
            </div>

            <div>
              <div className="mb-2 text-[10px] uppercase tracking-wider text-muted-foreground">
                {t("settings_view.team_proxy.local_label")}
              </div>
              <div className="flex flex-wrap gap-2">
                {PROXIABLE_PROVIDERS.map((p) => {
                  const on = local.has(p);
                  return (
                    <button
                      key={p}
                      type="button"
                      onClick={() => toggleLocal(p)}
                      aria-pressed={on}
                      className={cn(
                        "inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs transition-colors",
                        on
                          ? "border-primary/50 bg-primary/10 text-primary"
                          : "border-border text-muted-foreground hover:text-foreground",
                      )}
                    >
                      <span
                        className={cn(
                          "flex h-3.5 w-3.5 items-center justify-center rounded border",
                          on
                            ? "border-primary bg-primary text-primary-foreground"
                            : "border-muted-foreground/50",
                        )}
                      >
                        {on && <Check className="h-2.5 w-2.5" />}
                      </span>
                      {p}
                    </button>
                  );
                })}
              </div>
            </div>
          </div>
        )}

        <div className="flex items-center gap-3">
          <Button size="sm" onClick={() => void save()} disabled={saving}>
            {saving && <Loader2 className="mr-1 h-4 w-4 animate-spin" />}
            {saved && !saving && <Check className="mr-1 h-4 w-4" />}
            {t("settings_view.team_proxy.save")}
          </Button>
          {error && <span className="text-xs text-destructive">{error}</span>}
        </div>
      </div>
    </SettingsBlock>
  );
}
