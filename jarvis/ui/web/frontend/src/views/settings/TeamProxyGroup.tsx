import { useEffect, useState } from "react";
import { Check, Loader2, Users } from "lucide-react";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";

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
    <section className="space-y-4">
      <h3 className="mb-3 inline-flex items-center gap-2 text-[10px] uppercase tracking-wider text-muted-foreground">
        <Users className="h-3.5 w-3.5" /> {t("settings_view.team_proxy.title")}
      </h3>
      <p className="text-xs text-muted-foreground">
        {t("settings_view.team_proxy.hint")}
      </p>

      <label className="flex cursor-pointer items-center gap-3 rounded-lg border border-border bg-card/60 px-4 py-3">
        <input
          type="checkbox"
          checked={enabled}
          onChange={(e) => setEnabled(e.target.checked)}
          className="h-4 w-4"
        />
        <span className="text-sm font-medium">
          {t("settings_view.team_proxy.enable_label")}
        </span>
      </label>

      {enabled && (
        <div className="space-y-4 rounded-lg border border-border bg-card/40 p-4">
          <div>
            <label className="mb-1 block text-[10px] uppercase tracking-wider text-muted-foreground">
              {t("settings_view.team_proxy.url_label")}
            </label>
            <input
              type="url"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://keys.example.dev"
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
            />
          </div>

          <div>
            <label className="mb-1 block text-[10px] uppercase tracking-wider text-muted-foreground">
              {t("settings_view.team_proxy.token_label")}
            </label>
            <input
              type="password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder={
                tokenConfigured
                  ? t("settings_view.team_proxy.token_set_placeholder")
                  : t("settings_view.team_proxy.token_placeholder")
              }
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
            />
          </div>

          <div>
            <div className="mb-2 text-[10px] uppercase tracking-wider text-muted-foreground">
              {t("settings_view.team_proxy.local_label")}
            </div>
            <div className="grid grid-cols-2 gap-2">
              {PROXIABLE_PROVIDERS.map((p) => (
                <label
                  key={p}
                  className="flex cursor-pointer items-center gap-2 rounded-md border border-border bg-background px-3 py-2 text-xs"
                >
                  <input
                    type="checkbox"
                    checked={local.has(p)}
                    onChange={() => toggleLocal(p)}
                    className="h-3.5 w-3.5"
                  />
                  <span>{p}</span>
                </label>
              ))}
            </div>
          </div>
        </div>
      )}

      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={() => void save()}
          disabled={saving}
          className={cn(
            "inline-flex items-center gap-2 rounded-lg border border-primary/40 bg-primary/10 px-4 py-2 text-sm font-medium transition-colors hover:bg-primary/20",
            saving && "opacity-60",
          )}
        >
          {saving && <Loader2 className="h-4 w-4 animate-spin" />}
          {saved && !saving && <Check className="h-4 w-4 text-primary" />}
          {t("settings_view.team_proxy.save")}
        </button>
        {error && <span className="text-xs text-destructive">{error}</span>}
      </div>
    </section>
  );
}
