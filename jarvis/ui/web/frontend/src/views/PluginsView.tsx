import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEventStore } from "@/store/events";
import {
  Blocks,
  ArrowRight,
  ExternalLink,
  Search,
  RefreshCw,
  RotateCw,
  Plus,
  Check,
  Copy,
  Sparkles,
  X,
  Loader2,
  AlertTriangle,
} from "lucide-react";
import { ViewHeader } from "@/views/ChatsView";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { openExternalUrl } from "@/lib/openExternal";
import { robustCopy } from "@/lib/clipboard";

// Wave hero image restored. The previous "kill image entirely" attempt
// felt too flat; the CSS-only gradient lacked atmospheric depth. Edge
// artefacts are killed by a brutal pair of inset shadows on the section
// (see CarouselBanner): an almost-black 180px shadow eats the visible
// rim, then a faint gold 140px shadow restores premium glow. The wave
// stays vivid in the centre.
const CAROUSEL_BG_URL = "/plugin-assets/carousel-hero.png";

// ---------------------------------------------------------------------------
// Wire types — mirror the JSON shape served by /api/marketplace/plugins.
// ---------------------------------------------------------------------------

type AuthMode =
  | "oauth_device_flow"
  | "pat_paste"
  | "hosted_mcp_oauth_dcr"
  | "oauth_pkce_loopback"
  | "hosted_mcp_allowlist";

type PluginStatus = "not_connected" | "connected" | "needs_reauth" | "error";
type Category = "Developer" | "Productivity" | "Communication";

interface CatalogPlugin {
  id: string;
  display_name: string;
  description: string;
  category: Category;
  logo_slug: string;
  logo_color?: string | null;
  logo_url?: string | null;
  featured?: boolean;
  auth: { mode: AuthMode; [key: string]: unknown };
  status: PluginStatus;
  live_callable?: boolean;
}

interface CatalogResponse {
  version: number;
  schema_version: string;
  plugins: CatalogPlugin[];
  total: number;
  connected: number;
}

interface PatPasteAuthDetail {
  mode: "pat_paste";
  token_creation_url: string;
  token_prefix: string;
  instruction_md: string;
}

interface Plugin {
  id: string;
  name: string;
  description: string;
  category: Category;
  logoSlug: string;
  logoColor?: string;
  logoUrl?: string;
  authMode: AuthMode;
  /** Raw auth config from the catalog. The modal needs `instruction_md`,
   *  `token_creation_url`, and `token_prefix` for `pat_paste`-mode plugins. */
  authConfig: { mode: AuthMode; [key: string]: unknown };
  status: PluginStatus;
  featured?: boolean;
  liveCallable?: boolean;
}

function adapt(p: CatalogPlugin): Plugin {
  return {
    id: p.id,
    name: p.display_name,
    description: p.description,
    category: p.category,
    logoSlug: p.logo_slug,
    logoColor: p.logo_color ?? undefined,
    logoUrl: p.logo_url ?? undefined,
    authMode: p.auth.mode,
    authConfig: p.auth,
    status: p.status,
    featured: p.featured ?? false,
    liveCallable: p.live_callable ?? false,
  };
}

function resolveLogoUrl(p: { logoUrl?: string; logoSlug: string; logoColor?: string }): string {
  if (p.logoUrl) return p.logoUrl;
  return `https://cdn.simpleicons.org/${p.logoSlug}${p.logoColor ? `/${p.logoColor}` : ""}`;
}

async function fetchCatalog(): Promise<CatalogResponse> {
  // `cache: "no-store"` forces the embedded WebView2 (desktop app) to bypass
  // its HTTP cache on every fetch. The server already sends
  // `Cache-Control: no-store`, but that only stops NEW caching — it does not
  // evict an entry WebView2 had already frozen. Result: the desktop window kept
  // serving a stale plugin list (a freshly-connected plugin still showed as
  // "not connected") while a normal browser tab showed the truth. Bypassing the
  // client cache here is the half that actually clears the residual entry.
  const res = await fetch("/api/marketplace/plugins", { cache: "no-store" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

const AUTH_LABELS: Record<AuthMode, string> = {
  oauth_device_flow: "Device Flow",
  pat_paste: "Access Token",
  hosted_mcp_oauth_dcr: "One-Click",
  oauth_pkce_loopback: "Browser Login",
  hosted_mcp_allowlist: "Allowlist",
};

const COMING_SOON = [
  "Linear",
  "Stripe",
  "Cloudflare",
  "Discord",
  "Google Drive",
  "Gmail",
  "Telegram",
  "Asana",
];

// PKCE plugins that ship a placeholder OAuth client: a downloader supplies their
// OWN production client here (the durable fix for provider-side refresh-token
// expiry — e.g. Google revokes a "Testing" app's token after 7 days). The Google
// family shares ONE client pair; slack and asana each have their own. Mirrors
// `marketplace.connect_helpers._OAUTH_CLIENT_FAMILY` — keep in sync.
const OAUTH_CLIENT_FAMILY: Record<string, { family: string; label: string }> = {
  gmail: { family: "google", label: "Google" },
  google_drive: { family: "google", label: "Google" },
  google_calendar: { family: "google", label: "Google" },
  slack: { family: "slack", label: "Slack" },
  asana: { family: "asana", label: "Asana" },
};

// Where the user creates/manages their own OAuth client per family.
const OAUTH_CLIENT_CONSOLE: Record<string, string> = {
  google: "https://console.cloud.google.com/auth/clients",
  slack: "https://api.slack.com/apps",
  asana: "https://app.asana.com/0/my-apps",
};

type TabId = "browse" | "installed";
type FilterId = "all" | Category;

const FILTERS: { id: FilterId; label: string }[] = [
  { id: "all", label: "All" },
  { id: "Developer", label: "Developer" },
  { id: "Productivity", label: "Productivity" },
  { id: "Communication", label: "Communication" },
];

export function PluginsView() {
  const qc = useQueryClient();
  const [tab, setTab] = useState<TabId>("browse");
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<FilterId>("all");
  const [connectingPlugin, setConnectingPlugin] = useState<Plugin | null>(null);
  // PKCE plugin awaiting the pre-connect dialog (own-client + keep-connected hint).
  const [pkceSetupPlugin, setPkceSetupPlugin] = useState<Plugin | null>(null);
  // Plugin awaiting a "really disconnect?" confirmation. Removing a plugin is
  // destructive (tokens dropped, brain tools re-expanded), so it must ask first.
  const [disconnectingPlugin, setDisconnectingPlugin] = useState<Plugin | null>(null);

  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ["marketplace-plugins"],
    queryFn: fetchCatalog,
    refetchInterval: 30_000,
  });

  const connectMutation = useMutation({
    mutationFn: async ({
      pluginId,
      token,
      allowedUserId,
    }: {
      pluginId: string;
      token: string;
      allowedUserId?: number | null;
    }) => {
      const body: { token: string; allowed_user_id?: number } = { token };
      if (allowedUserId != null) body.allowed_user_id = allowedUserId;
      const res = await fetch(`/api/marketplace/plugins/${pluginId}/connect/pat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
        throw new Error(err.detail || `connect failed (HTTP ${res.status})`);
      }
      return res.json();
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["marketplace-plugins"] });
      setConnectingPlugin(null);
    },
  });

  const disconnectMutation = useMutation({
    mutationFn: async (pluginId: string) => {
      const res = await fetch(`/api/marketplace/plugins/${pluginId}`, { method: "DELETE" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json();
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["marketplace-plugins"] });
      setDisconnectingPlugin(null);
    },
  });

  // OAuth-redirect flow (DCR): kick off /connect/start, open URL in browser,
  // long-poll /connect/poll until done.
  const oauthStart = useMutation({
    mutationFn: async (pluginId: string) => {
      const res = await fetch(
        `/api/marketplace/plugins/${pluginId}/connect/start`,
        { method: "POST" },
      );
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
        throw new Error(err.detail || `start failed (HTTP ${res.status})`);
      }
      return res.json() as Promise<{
        flow_id: string;
        plugin_id: string;
        kind: "browser_redirect" | "device_flow";
        open_url: string | null;
        expires_at_ms: number | null;
      }>;
    },
  });

  const [oauthSession, setOauthSession] = useState<{
    flowId: string;
    pluginId: string;
    pluginName: string;
    openUrl: string;
  } | null>(null);

  const [deviceSession, setDeviceSession] = useState<{
    flowId: string;
    pluginId: string;
    pluginName: string;
    userCode: string;
    verificationUri: string;
    verificationUriComplete: string | null;
    expiresAtMs: number | null;
  } | null>(null);

  // Kick off the real OAuth handshake: /connect/start, open the URL, then the
  // dialog long-polls /connect/poll. Shared by the DCR/device path (called
  // directly) and the PKCE path (called by the pre-connect dialog's Continue).
  const startOAuthFlow = async (p: Plugin) => {
    try {
      const r = await oauthStart.mutateAsync(p.id);
      if (r.kind === "device_flow") {
        // GitHub-style: show the user_code in a dedicated dialog,
        // pre-open the verification URL with code embedded if present.
        const verifyUrl = (r as unknown as { verification_uri?: string })
          .verification_uri;
        const verifyUrlComplete = (r as unknown as {
          verification_uri_complete?: string;
        }).verification_uri_complete;
        const userCode = (r as unknown as { user_code?: string }).user_code;
        if (!verifyUrl || !userCode) {
          alert("Backend returned an incomplete device-flow session.");
          return;
        }
        // Auto-open the pre-filled verify URL if available; user lands
        // on the consent page with the code already typed in.
        if (verifyUrlComplete) {
          void openExternalUrl(verifyUrlComplete);
        }
        setDeviceSession({
          flowId: r.flow_id,
          pluginId: r.plugin_id,
          pluginName: p.name,
          userCode,
          verificationUri: verifyUrl,
          verificationUriComplete: verifyUrlComplete ?? null,
          expiresAtMs: r.expires_at_ms,
        });
        return;
      }
      if (!r.open_url) {
        alert("Backend returned no open_url — connect aborted.");
        return;
      }
      void openExternalUrl(r.open_url);
      setOauthSession({
        flowId: r.flow_id,
        pluginId: r.plugin_id,
        pluginName: p.name,
        openUrl: r.open_url,
      });
    } catch (e) {
      alert(
        `Could not start ${p.name} connect flow: ${
          e instanceof Error ? e.message : String(e)
        }`,
      );
    }
  };

  const handleConnect = async (p: Plugin) => {
    if (p.authMode === "pat_paste") {
      setConnectingPlugin(p);
      return;
    }
    if (p.authMode === "oauth_pkce_loopback") {
      // PKCE plugins ship a placeholder client — show the pre-connect dialog so
      // the user can supply their OWN production OAuth client (the durable fix
      // for the 7-day expiry) and sees the keep-connected hint, before the
      // browser sign-in actually starts.
      setPkceSetupPlugin(p);
      return;
    }
    if (
      p.authMode === "hosted_mcp_oauth_dcr" ||
      p.authMode === "oauth_device_flow"
    ) {
      await startOAuthFlow(p);
      return;
    }
    // hosted_mcp_allowlist (Vercel v2) — needs cloud proxy, deferred.
    alert(
      `Connecting via "${AUTH_LABELS[p.authMode]}" needs a cloud proxy that ` +
        `isn't deployed yet. Coming in the Vercel-Cloud-Proxy wave.`,
    );
  };

  const allPlugins = useMemo<Plugin[]>(
    () => data?.plugins.map(adapt) ?? [],
    [data],
  );
  // "Installed" keeps every plugin the user ever connected — including a revoked
  // (needs_reauth) or errored one — so a dead token surfaces a Reconnect prompt
  // here instead of silently dropping back into Browse as a plain "+".
  const installed = useMemo(
    () =>
      allPlugins.filter(
        (p) =>
          p.status === "connected" ||
          p.status === "needs_reauth" ||
          p.status === "error",
      ),
    [allPlugins],
  );
  const connectedCount = useMemo(
    () => allPlugins.filter((p) => p.status === "connected").length,
    [allPlugins],
  );
  const needsAttentionCount = useMemo(
    () =>
      allPlugins.filter(
        (p) => p.status === "needs_reauth" || p.status === "error",
      ).length,
    [allPlugins],
  );

  const visible = useMemo(() => {
    const base = tab === "installed" ? installed : allPlugins;
    const q = query.trim().toLowerCase();
    return base.filter((p) => {
      if (filter !== "all" && p.category !== filter) return false;
      if (q && !p.name.toLowerCase().includes(q)) return false;
      return true;
    });
  }, [tab, query, filter, allPlugins, installed]);

  // Every plugin that needs a reconnect (revoked/expired token) or errored —
  // the ones the "needs attention" banner names and the "Jump to it" button
  // scrolls to.
  const attentionPlugins = useMemo(
    () =>
      allPlugins.filter(
        (p) => p.status === "needs_reauth" || p.status === "error",
      ),
    [allPlugins],
  );

  // "Jump to it": clear any active search/filter so the target row is
  // guaranteed rendered, then hand its id to the effect below, which scrolls to
  // it once React has painted it. Two-step (state + effect) rather than an
  // inline scroll because the row may not exist in the DOM until the filter
  // reset re-renders the list.
  const [scrollTarget, setScrollTarget] = useState<string | null>(null);
  const jumpToFirstProblem = () => {
    const target = attentionPlugins[0];
    if (!target) return;
    setQuery("");
    setFilter("all");
    setScrollTarget(target.id);
  };

  useEffect(() => {
    if (!scrollTarget) return;
    const el = document.getElementById(`plugin-row-${scrollTarget}`);
    // Not painted yet — a later render (after the filter reset) re-runs this
    // effect via the `visible` dependency and finds the row then.
    if (!el) return;
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    // Brief amber flash so the eye lands on the right card.
    const flash = ["ring-2", "ring-amber-500/70", "ring-offset-2", "ring-offset-background"];
    el.classList.add(...flash);
    const timer = window.setTimeout(() => el.classList.remove(...flash), 2000);
    setScrollTarget(null);
    return () => window.clearTimeout(timer);
  }, [scrollTarget, visible]);

  return (
    <div className="flex h-full flex-col bg-background">
      <ViewHeader
        icon={<Blocks className="h-4 w-4 text-primary" />}
        title="Plugins"
        subtitle={
          isLoading
            ? "Loading catalog…"
            : error
              ? "Backend unreachable"
              : `${allPlugins.length} available · ${connectedCount} connected${
                  needsAttentionCount > 0
                    ? ` · ${needsAttentionCount} need reconnect`
                    : ""
                }`
        }
        right={
          <Button
            size="sm"
            variant="ghost"
            onClick={() => refetch()}
            disabled={isFetching}
            title="Refresh catalog"
          >
            <RefreshCw className={cn("h-3.5 w-3.5", isFetching && "animate-spin")} />
          </Button>
        }
      />

      <div className="flex items-center gap-6 border-b border-border px-6">
        <Tab
          label="Browse"
          count={allPlugins.length}
          active={tab === "browse"}
          onClick={() => setTab("browse")}
        />
        <Tab
          label="Installed"
          count={installed.length}
          active={tab === "installed"}
          onClick={() => setTab("installed")}
        />
      </div>

      <ScrollArea className="flex-1">
        <div className="relative mx-auto max-w-3xl px-6 pb-20 pt-14">
          <AttentionBanner plugins={attentionPlugins} onJump={jumpToFirstProblem} />
          {tab === "browse" ? (
            <BrowseLayout
              plugins={visible}
              query={query}
              setQuery={setQuery}
              filter={filter}
              setFilter={setFilter}
              onConnect={handleConnect}
              onDisconnect={(id) =>
                setDisconnectingPlugin(allPlugins.find((p) => p.id === id) ?? null)
              }
            />
          ) : (
            <InstalledLayout
              plugins={visible}
              totalAvailable={allPlugins.length}
              query={query}
              setQuery={setQuery}
              filter={filter}
              setFilter={setFilter}
              onConnect={handleConnect}
              onDisconnect={(id) =>
                setDisconnectingPlugin(allPlugins.find((p) => p.id === id) ?? null)
              }
            />
          )}
        </div>
      </ScrollArea>

      {connectingPlugin && (
        <PatConnectDialog
          plugin={connectingPlugin}
          onClose={() => {
            setConnectingPlugin(null);
            connectMutation.reset();
          }}
          onSubmit={(token, allowedUserId) =>
            connectMutation.mutate({
              pluginId: connectingPlugin.id,
              token,
              allowedUserId,
            })
          }
          isPending={connectMutation.isPending}
          errorMessage={
            connectMutation.error instanceof Error
              ? connectMutation.error.message
              : null
          }
        />
      )}

      {pkceSetupPlugin && (
        <PkceConnectDialog
          plugin={pkceSetupPlugin}
          onClose={() => setPkceSetupPlugin(null)}
          onProceed={() => startOAuthFlow(pkceSetupPlugin)}
        />
      )}

      {disconnectingPlugin && (
        <DisconnectConfirmDialog
          plugin={disconnectingPlugin}
          isPending={disconnectMutation.isPending}
          onCancel={() => {
            setDisconnectingPlugin(null);
            disconnectMutation.reset();
          }}
          onConfirm={() => disconnectMutation.mutate(disconnectingPlugin.id)}
          errorMessage={
            disconnectMutation.error instanceof Error
              ? disconnectMutation.error.message
              : null
          }
        />
      )}

      {oauthSession && (
        <OAuthRedirectDialog
          flowId={oauthSession.flowId}
          pluginId={oauthSession.pluginId}
          pluginName={oauthSession.pluginName}
          openUrl={oauthSession.openUrl}
          onClose={() => setOauthSession(null)}
          onSuccess={() => {
            setOauthSession(null);
            qc.invalidateQueries({ queryKey: ["marketplace-plugins"] });
          }}
        />
      )}

      {deviceSession && (
        <DeviceCodeDialog
          flowId={deviceSession.flowId}
          pluginId={deviceSession.pluginId}
          pluginName={deviceSession.pluginName}
          userCode={deviceSession.userCode}
          verificationUri={deviceSession.verificationUri}
          verificationUriComplete={deviceSession.verificationUriComplete}
          expiresAtMs={deviceSession.expiresAtMs}
          onClose={() => setDeviceSession(null)}
          onSuccess={() => {
            setDeviceSession(null);
            qc.invalidateQueries({ queryKey: ["marketplace-plugins"] });
          }}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Attention banner — plain-language "what's wrong" + a one-click jump to it
// ---------------------------------------------------------------------------

// Shown at the top of the Plugins view whenever a connected plugin's token has
// expired/been revoked (`needs_reauth`) or errored. The sidebar dot proves a
// problem exists app-wide; this banner spells out WHICH plugin and jumps the
// user straight to its card, so they never have to hunt for it.
function AttentionBanner({
  plugins,
  onJump,
}: {
  plugins: Plugin[];
  onJump: () => void;
}) {
  if (plugins.length === 0) return null;
  const names = plugins.map((p) => p.name);
  const one = plugins.length === 1;
  const headline = one
    ? `${names[0]} needs reconnecting`
    : `${plugins.length} connections need reconnecting`;

  return (
    <div className="mb-8 flex items-center gap-3 rounded-xl border border-amber-500/40 bg-amber-500/10 px-4 py-3">
      <AlertTriangle className="h-4 w-4 shrink-0 text-amber-500" />
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium text-foreground">{headline}</p>
        <p className="truncate text-xs text-muted-foreground">
          {one
            ? "Its token expired or was revoked — reconnect to keep it working."
            : `Reconnect to keep them working: ${names.join(", ")}`}
        </p>
      </div>
      <button
        type="button"
        onClick={onJump}
        className="inline-flex shrink-0 items-center gap-1.5 rounded-full border border-amber-500/50 bg-amber-500/10 px-3 py-1.5 text-xs font-semibold text-amber-500 transition-colors hover:bg-amber-500/20"
      >
        {one ? "Jump to it" : "Jump to first"}
        <ArrowRight className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Connect-handler prop type, threaded through the component tree
// ---------------------------------------------------------------------------

interface ConnectHandlers {
  // Returns a promise for the in-flight connect so the per-row button can lock
  // itself (spinner + disabled) until the flow has launched — see
  // ConnectIconButton. A void return (e.g. pat_paste opening a modal) is fine.
  onConnect: (p: Plugin) => void | Promise<void>;
  onDisconnect: (id: string) => void;
}

// ---------------------------------------------------------------------------
// Layout shells
// ---------------------------------------------------------------------------

interface SearchControlsProps {
  query: string;
  setQuery: (v: string) => void;
  filter: FilterId;
  setFilter: (f: FilterId) => void;
}

function BrowseLayout({
  plugins,
  query,
  setQuery,
  filter,
  setFilter,
  onConnect,
  onDisconnect,
}: { plugins: Plugin[] } & SearchControlsProps & ConnectHandlers) {
  return (
    <>
      <Hero query={query} setQuery={setQuery} filter={filter} setFilter={setFilter} />
      <CarouselBanner />
      <CategorizedList
        plugins={plugins}
        query={query}
        onConnect={onConnect}
        onDisconnect={onDisconnect}
      />
      <ComingSoonStrip taken={plugins.map((p) => p.name)} />
    </>
  );
}

function InstalledLayout({
  plugins,
  totalAvailable,
  query,
  setQuery,
  filter,
  setFilter,
  onConnect,
  onDisconnect,
}: { plugins: Plugin[]; totalAvailable: number } & SearchControlsProps & ConnectHandlers) {
  return (
    <>
      <Hero
        title="Your connected services"
        subtitle="The plugins below are linked to your account. Disconnect from each row when you no longer need them."
        query={query}
        setQuery={setQuery}
        filter={filter}
        setFilter={setFilter}
      />
      {plugins.length === 0 ? (
        <EmptyInstalled totalAvailable={totalAvailable} />
      ) : (
        <CategorizedList
          plugins={plugins}
          query={query}
          onConnect={onConnect}
          onDisconnect={onDisconnect}
        />
      )}
    </>
  );
}

function Hero({
  title = "Connect your assistant to your services",
  subtitle,
  query,
  setQuery,
  filter,
  setFilter,
}: { title?: string; subtitle?: string } & SearchControlsProps) {
  return (
    <header className="mb-10 text-center">
      <h1 className="font-display text-2xl font-semibold tracking-tight sm:text-3xl">{title}</h1>
      {subtitle && (
        <p className="mx-auto mt-2 max-w-md text-xs leading-relaxed text-muted-foreground">
          {subtitle}
        </p>
      )}
      <div className="mx-auto mt-6 flex max-w-md items-center gap-2">
        <SearchInput value={query} onChange={setQuery} />
        <FilterMenu filter={filter} setFilter={setFilter} />
      </div>
    </header>
  );
}

function SearchInput({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <div className="relative flex-1">
      <Search className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="Search plugins"
        className="h-9 w-full rounded-full border border-border bg-card/60 pl-9 pr-3 text-xs text-foreground placeholder:text-muted-foreground/60 focus:border-primary/40 focus:outline-none focus:ring-1 focus:ring-primary/30"
      />
    </div>
  );
}

function FilterMenu({ filter, setFilter }: { filter: FilterId; setFilter: (f: FilterId) => void }) {
  return (
    <div className="relative">
      <select
        value={filter}
        onChange={(e) => setFilter(e.target.value as FilterId)}
        className="h-9 cursor-pointer appearance-none rounded-full border border-border bg-card/60 px-4 pr-7 text-xs font-medium text-foreground hover:border-primary/40 focus:border-primary/40 focus:outline-none"
      >
        {FILTERS.map((f) => (
          <option key={f.id} value={f.id}>
            {f.label}
          </option>
        ))}
      </select>
      <span className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-[9px] text-muted-foreground">
        ▾
      </span>
    </div>
  );
}

function Tab({
  label,
  count,
  active,
  onClick,
}: { label: string; count: number; active: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "relative py-3 text-sm font-medium transition-colors",
        active ? "text-foreground" : "text-muted-foreground hover:text-foreground",
      )}
    >
      <span className="flex items-center gap-2">
        {label}
        <span
          className={cn(
            "rounded-full px-1.5 py-0.5 text-[10px] font-semibold tabular-nums",
            active ? "bg-primary/20 text-primary" : "bg-muted text-muted-foreground",
          )}
        >
          {count}
        </span>
      </span>
      {active && (
        <span
          aria-hidden
          className="absolute inset-x-0 bottom-0 h-0.5 rounded-full bg-primary shadow-[0_0_8px_rgba(255,214,10,0.6)]"
        />
      )}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Auto-rotating hero carousel
// ---------------------------------------------------------------------------

interface CarouselSlide {
  pluginId: string;
  pluginName: string;
  example: string;
  iconUrl: string;
  iconBoost?: boolean;
  accent: string;
}

const SLIDES: CarouselSlide[] = [
  {
    pluginId: "google_calendar",
    pluginName: "Google Calendar",
    example: "Schedule a meeting for tomorrow at 3pm",
    iconUrl: "https://cdn.simpleicons.org/googlecalendar/F4F4F5",
    accent: "border-blue-400/40",
  },
  {
    pluginId: "github",
    pluginName: "GitHub",
    example: "Triage open issues on my main repo",
    iconUrl: "https://cdn.simpleicons.org/github/F4F4F5",
    accent: "border-zinc-400/40",
  },
  {
    pluginId: "vercel",
    pluginName: "Vercel",
    example: "Deploy my project to production",
    iconUrl: "https://cdn.simpleicons.org/vercel/F4F4F5",
    accent: "border-primary/50",
  },
  {
    pluginId: "supabase",
    pluginName: "Supabase",
    example: "Snapshot all my Supabase projects",
    iconUrl: "https://cdn.simpleicons.org/supabase",
    accent: "border-emerald-400/40",
  },
  {
    pluginId: "notion",
    pluginName: "Notion",
    example: "Find pages mentioning the Q3 plan",
    iconUrl: "https://cdn.simpleicons.org/notion/F4F4F5",
    accent: "border-zinc-300/40",
  },
  {
    pluginId: "slack",
    pluginName: "Slack",
    example: "Search messages mentioning the launch plan",
    iconUrl: "/plugin-assets/slack-logo.svg",
    iconBoost: true,
    accent: "border-purple-400/40",
  },
];

const SLIDE_INTERVAL_MS = 3500;

function CarouselBanner() {
  const [active, setActive] = useState(0);
  const [manualOverride, setManualOverride] = useState(false);
  const timerRef = useRef<number | null>(null);

  useEffect(() => {
    if (manualOverride) {
      const t = window.setTimeout(() => setManualOverride(false), SLIDE_INTERVAL_MS * 2);
      return () => window.clearTimeout(t);
    }
    timerRef.current = window.setInterval(() => {
      setActive((i) => (i + 1) % SLIDES.length);
    }, SLIDE_INTERVAL_MS);
    return () => {
      if (timerRef.current !== null) window.clearInterval(timerRef.current);
    };
  }, [manualOverride]);

  return (
    <section
      // Wave hero restored. Two stacked inset shadows do the edge-kill:
      //   - 180px black-92% inset → eats the rounded-3xl rim, the wave's
      //     hard edges are gone before they reach the visible boundary.
      //   - 140px gold-10% inset → faint warm glow so the rim doesn't
      //     read as a dead frame.
      // bg-size 180% over-zooms the image deeply so only the brightest
      // mid-band is visible; whatever rim survives the zoom is then
      // swallowed by the black inset. This combo proved the only one
      // that actually kills the artefact — radial gradients alone left
      // a visible diagonal cut, the flat-only version felt sloppy.
      className="relative mb-12 aspect-[16/7] w-full overflow-hidden rounded-3xl shadow-[inset_0_0_180px_rgba(0,0,0,0.92),inset_0_0_140px_rgba(255,214,10,0.10)]"
      aria-roledescription="carousel"
      style={{
        backgroundImage: `url(${CAROUSEL_BG_URL})`,
        backgroundSize: "180%",
        backgroundPosition: "center 60%",
        backgroundRepeat: "no-repeat",
        backgroundColor: "#0a0a0a",
      }}
    >
      {SLIDES.map((slide, i) => (
        <CarouselSlideView key={slide.pluginId} slide={slide} active={active === i} />
      ))}
      <div className="absolute right-3 top-1/2 z-20 flex -translate-y-1/2 flex-col gap-2">
        {SLIDES.map((s, i) => (
          <button
            key={s.pluginId}
            type="button"
            onClick={() => {
              setActive(i);
              setManualOverride(true);
            }}
            aria-label={`Show ${s.pluginName} example`}
            className={cn(
              "h-1.5 w-1.5 rounded-full transition-all duration-300",
              active === i
                ? "scale-125 bg-primary shadow-[0_0_8px_rgba(255,214,10,0.7)]"
                : "bg-white/30 hover:bg-white/60",
            )}
          />
        ))}
      </div>
    </section>
  );
}

function CarouselSlideView({ slide, active }: { slide: CarouselSlide; active: boolean }) {
  return (
    <div
      className={cn(
        "absolute inset-0 flex items-center justify-center",
        "transition-[opacity,transform] duration-500 ease-out",
        active
          ? "translate-y-0 scale-100 opacity-100 z-10"
          : "translate-y-2 scale-[0.96] opacity-0 z-0",
      )}
      aria-hidden={!active}
    >
      <div
        className={cn(
          "flex items-center gap-2 rounded-full border bg-black/55 px-4 py-2 text-xs shadow-[0_8px_24px_rgba(0,0,0,0.45)] backdrop-blur-md",
          slide.accent,
        )}
      >
        <img
          src={slide.iconUrl}
          alt=""
          className={cn(slide.iconBoost ? "h-5 w-5" : "h-3.5 w-3.5")}
          loading="lazy"
        />
        <span className="font-medium text-foreground">{slide.pluginName}</span>
        <span className="text-muted-foreground">·</span>
        <span className="text-muted-foreground">{slide.example}</span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Categorized list
// ---------------------------------------------------------------------------

function CategorizedList({
  plugins,
  query,
  onConnect,
  onDisconnect,
}: { plugins: Plugin[]; query: string } & ConnectHandlers) {
  if (plugins.length === 0) {
    if (!query.trim()) return null;
    return <EmptyHits query={query} />;
  }

  const featured = plugins.filter((p) => p.featured);
  const byCat: Record<Category, Plugin[]> = {
    Developer: [],
    Productivity: [],
    Communication: [],
  };
  for (const p of plugins) {
    if (p.featured) continue;
    byCat[p.category].push(p);
  }

  return (
    <div className="space-y-10">
      {featured.length > 0 && (
        <Section title="Featured">
          {featured.map((p) => (
            <PluginRow
              key={p.id}
              plugin={p}
              onConnect={onConnect}
              onDisconnect={onDisconnect}
            />
          ))}
        </Section>
      )}
      {(Object.keys(byCat) as Category[])
        .filter((c) => byCat[c].length > 0)
        .map((c) => (
          <Section key={c} title={c}>
            {byCat[c].map((p) => (
              <PluginRow
                key={p.id}
                plugin={p}
                onConnect={onConnect}
                onDisconnect={onDisconnect}
              />
            ))}
          </Section>
        ))}
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h2 className="mb-3 font-display text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">
        {title}
      </h2>
      <div className="grid gap-2 sm:grid-cols-2">{children}</div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// PluginRow — bigger tile + bigger icon for multicolor logos
// ---------------------------------------------------------------------------

function PluginRow({
  plugin,
  onConnect,
  onDisconnect,
}: { plugin: Plugin } & ConnectHandlers) {
  const isConnected = plugin.status === "connected";
  const needsReauth = plugin.status === "needs_reauth";
  const isError = plugin.status === "error";
  const isMulticolor = !!plugin.logoUrl;

  return (
    <article
      // Stable DOM id so the "Jump to it" affordance in AttentionBanner can
      // scrollIntoView + flash this exact row. `scroll-mt-24` leaves headroom
      // under the sticky tab bar so the scrolled-to row isn't hidden beneath it.
      id={`plugin-row-${plugin.id}`}
      className={cn(
        "group flex items-center gap-3 rounded-lg border bg-card/40 px-3 py-2.5 transition-[colors,box-shadow] scroll-mt-24",
        isConnected && "border-primary/30",
        needsReauth && "border-amber-500/40",
        isError && "border-destructive/40",
        !isConnected &&
          !needsReauth &&
          !isError &&
          "border-border hover:border-primary/40 hover:bg-card/70",
      )}
    >
      <div className="grid h-10 w-10 shrink-0 place-items-center rounded-md border border-border/60 bg-white">
        <img
          src={resolveLogoUrl(plugin)}
          alt=""
          className={cn(isMulticolor ? "h-7 w-7" : "h-5 w-5")}
          loading="lazy"
        />
      </div>

      <div className="min-w-0 flex-1">
        {/* `flex-wrap` + `shrink-0` badges: when a connected plugin's
            "· Connected · Live" badges don't fit beside the name, they wrap to
            the next line instead of squeezing the (truncating) name down to
            "GitH…"/"Gm…". The name keeps priority and only truncates on a
            genuinely long name. */}
        <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
          <h3 className="min-w-0 max-w-full truncate text-sm font-semibold tracking-tight text-foreground">
            {plugin.name}
          </h3>
          {isConnected && (
            <span className="shrink-0 text-[9px] font-medium uppercase tracking-wider text-primary">
              · Connected
            </span>
          )}
          {isConnected && plugin.liveCallable && (
            <span className="shrink-0 text-[9px] font-medium uppercase tracking-wider text-emerald-400">
              · Live
            </span>
          )}
          {needsReauth && (
            <span className="inline-flex shrink-0 items-center gap-1 text-[9px] font-medium uppercase tracking-wider text-amber-500">
              <AlertTriangle className="h-2.5 w-2.5" />
              <span>Reconnect needed</span>
            </span>
          )}
          {isError && (
            <span className="inline-flex shrink-0 items-center gap-1 text-[9px] font-medium uppercase tracking-wider text-destructive">
              <AlertTriangle className="h-2.5 w-2.5" />
              <span>Error</span>
            </span>
          )}
        </div>
        <p className="truncate text-xs text-muted-foreground">{plugin.description}</p>
      </div>

      <span
        className="hidden text-[9px] font-medium uppercase tracking-wider text-muted-foreground/70 sm:block"
        title={plugin.category}
      >
        {AUTH_LABELS[plugin.authMode]}
      </span>

      <ConnectIconButton
        status={plugin.status}
        onConnect={() => onConnect(plugin)}
        onDisconnect={() => onDisconnect(plugin.id)}
      />
    </article>
  );
}

export function ConnectIconButton({
  status,
  onConnect,
  onDisconnect,
}: {
  status: PluginStatus;
  onConnect: () => void | Promise<void>;
  onDisconnect: () => void;
}) {
  // `/connect/start` (DCR registration) takes ~0.6s with no other feedback, so
  // without a lock the user re-clicks and each click launches its OWN OAuth flow
  // — a burst of browser tabs and stray client registrations. `busyRef` is the
  // SYNCHRONOUS guard (React state is async and would let a fast double-click
  // through before the re-render disables the button); `busy` drives the UI.
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);

  if (status === "connected") {
    return (
      <button
        type="button"
        onClick={onDisconnect}
        className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-primary/15 text-primary transition-colors hover:bg-destructive/20 hover:text-destructive"
        aria-label="Disconnect plugin"
        title="Disconnect"
      >
        <Check className="h-3.5 w-3.5" />
      </button>
    );
  }

  const handleClick = async () => {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    try {
      await onConnect();
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  };

  // A revoked / errored token re-runs the SAME connect flow, but is shown as a
  // distinct amber "Reconnect" affordance so it can never be mistaken for a
  // never-connected "+" (the silent-rot bug this view used to have).
  const needsReconnect = status === "needs_reauth" || status === "error";
  if (needsReconnect) {
    return (
      <button
        type="button"
        onClick={handleClick}
        disabled={busy}
        aria-busy={busy}
        className={cn(
          "grid h-7 w-7 shrink-0 place-items-center rounded-full border border-amber-500/50 bg-amber-500/10 text-amber-500 transition-all hover:bg-amber-500/20 group-hover:scale-105",
          busy && "cursor-not-allowed opacity-60 group-hover:scale-100",
        )}
        aria-label="Reconnect plugin"
        title="Reconnect"
      >
        {busy ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
        ) : (
          <RotateCw className="h-3.5 w-3.5" />
        )}
      </button>
    );
  }

  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={busy}
      aria-busy={busy}
      className={cn(
        "grid h-7 w-7 shrink-0 place-items-center rounded-full border border-border bg-background/60 text-muted-foreground transition-all hover:border-primary/50 hover:bg-primary/10 hover:text-primary group-hover:scale-105",
        busy && "cursor-not-allowed opacity-60 hover:bg-background/60 hover:text-muted-foreground group-hover:scale-100",
      )}
      aria-label="Connect plugin"
    >
      {busy ? (
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
      ) : (
        <Plus className="h-3.5 w-3.5" />
      )}
    </button>
  );
}

function EmptyHits({ query }: { query: string }) {
  return (
    <div className="rounded-xl border border-dashed border-border bg-card/40 px-6 py-12 text-center">
      <p className="text-sm text-muted-foreground">
        No plugin matches <span className="font-mono text-foreground">"{query}"</span>.
      </p>
    </div>
  );
}

function EmptyInstalled({ totalAvailable }: { totalAvailable: number }) {
  const assistantName = useEventStore((s) => s.assistantName);
  return (
    <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-border bg-card/40 px-8 py-14 text-center">
      <div className="mb-4 grid h-11 w-11 place-items-center rounded-full bg-primary/10 text-primary">
        <Sparkles className="h-5 w-5" />
      </div>
      <h3 className="font-display text-base font-semibold tracking-tight">
        Nothing connected yet
      </h3>
      <p className="mt-2 max-w-xs text-xs leading-relaxed text-muted-foreground">
        Pick one of the {totalAvailable} services in the Browse tab to give {assistantName} hands beyond
        your local machine.
      </p>
    </div>
  );
}

function ComingSoonStrip({ taken = [] }: { taken?: string[] }) {
  // Drop any teaser that now has a real catalog entry, so a newly-added
  // connector (e.g. Linear) never shows as both connectable and "coming soon".
  const upcoming = COMING_SOON.filter((name) => !taken.includes(name));
  if (upcoming.length === 0) return null;
  return (
    <section className="mt-16 border-t border-border pt-8 text-center">
      <h3 className="font-display text-[11px] font-semibold uppercase tracking-[0.22em] text-muted-foreground">
        Coming soon
      </h3>
      <div className="mt-4 flex flex-wrap justify-center gap-2">
        {upcoming.map((name) => (
          <span
            key={name}
            className="rounded-full border border-border/60 bg-card/40 px-3 py-1 text-xs text-muted-foreground transition-colors hover:border-primary/30 hover:text-foreground"
          >
            {name}
          </span>
        ))}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// OAuth Redirect Dialog — used by Notion (and any future hosted-MCP plugin
// with DCR + PKCE). The browser tab is already opened by the caller; this
// dialog just shows progress + long-polls the backend.
// ---------------------------------------------------------------------------

// A read-only URL field + Copy button. The manual fallback for every "open a
// page in your browser" step: when the auto-open didn't reach a browser (the
// embedded desktop shell drops window.open, or a popup blocker ate it), the
// user can copy the exact link and paste it into their browser by hand. Uses
// robustCopy so the copy is reliable inside WebView2.
function CopyableUrl({ url, hint }: { url: string; hint?: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    if (await robustCopy(url)) {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1400);
    }
  };
  return (
    <div className="w-full text-left">
      <div className="flex items-stretch gap-2">
        <input
          type="text"
          readOnly
          value={url}
          onFocus={(e) => e.currentTarget.select()}
          aria-label="Authorization link"
          className="min-w-0 flex-1 rounded-md border border-border bg-background/60 px-2.5 py-1.5 font-mono text-[11px] text-muted-foreground focus:border-primary/40 focus:outline-none"
        />
        <button
          type="button"
          onClick={copy}
          className="inline-flex shrink-0 items-center gap-1.5 rounded-md border border-border bg-card px-3 text-[11px] font-medium text-foreground transition-colors hover:border-primary/50 hover:text-primary"
          title="Copy link"
        >
          {copied ? <Check className="h-3 w-3 text-primary" /> : <Copy className="h-3 w-3" />}
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      {hint && (
        <p className="mt-1.5 text-[11px] leading-relaxed text-muted-foreground/80">{hint}</p>
      )}
    </div>
  );
}

function OAuthRedirectDialog({
  flowId,
  pluginId,
  pluginName,
  openUrl,
  onClose,
  onSuccess,
}: {
  flowId: string;
  pluginId: string;
  pluginName: string;
  openUrl: string;
  onClose: () => void;
  onSuccess: () => void;
}) {
  const poll = useQuery({
    queryKey: ["marketplace-oauth-poll", flowId],
    queryFn: async () => {
      const res = await fetch(
        `/api/marketplace/plugins/${pluginId}/connect/poll/${flowId}`,
        { cache: "no-store" },
      );
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
        throw new Error(err.detail || `poll failed (HTTP ${res.status})`);
      }
      return res.json() as Promise<{
        state: "pending" | "connected" | "error";
        error?: string;
      }>;
    },
    refetchInterval: (q) => {
      const s = q.state.data?.state;
      // Stop polling once we've reached a terminal state.
      return s === "connected" || s === "error" ? false : 1500;
    },
    refetchIntervalInBackground: true,
  });

  // Auto-close on success after a short pause so the user sees the
  // "Connected" tick.
  useEffect(() => {
    if (poll.data?.state === "connected") {
      const t = window.setTimeout(onSuccess, 800);
      return () => window.clearTimeout(t);
    }
  }, [poll.data?.state, onSuccess]);

  const state = poll.data?.state ?? "pending";
  const errorMessage =
    state === "error"
      ? poll.data?.error ?? "Unknown error"
      : poll.error instanceof Error
        ? poll.error.message
        : null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="relative w-full max-w-md overflow-hidden rounded-2xl border border-border bg-card shadow-[0_20px_60px_rgba(0,0,0,0.6)]">
        <header className="flex items-center justify-between border-b border-border px-5 py-4">
          <div>
            <h2 className="font-display text-base font-semibold tracking-tight">
              Connecting {pluginName}
            </h2>
            <p className="text-[11px] uppercase tracking-wider text-muted-foreground">
              Browser login
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="grid h-7 w-7 place-items-center rounded-full text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            aria-label="Close"
          >
            <X className="h-4 w-4" />
          </button>
        </header>

        <div className="space-y-5 px-5 py-6">
          {state === "pending" && (
            <div className="flex flex-col items-center gap-4 py-4 text-center">
              <Loader2 className="h-8 w-8 animate-spin text-primary" />
              <div>
                <p className="text-sm font-medium text-foreground">
                  Authorize Personal Jarvis in your browser
                </p>
                <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                  A browser tab opened for {pluginName}. Sign in if prompted,
                  click "Authorize", then come back here.
                </p>
              </div>
              <a
                href={openUrl}
                target="_blank"
                rel="noopener noreferrer"
                onClick={(e) => {
                  e.preventDefault();
                  void openExternalUrl(openUrl);
                }}
                className="text-[11px] uppercase tracking-wider text-muted-foreground/80 underline-offset-4 hover:text-primary hover:underline"
              >
                Tab didn't open? Click here
              </a>
              <CopyableUrl
                url={openUrl}
                hint="Still nothing? Copy this link and paste it into your browser's address bar."
              />
            </div>
          )}

          {state === "connected" && (
            <div className="flex flex-col items-center gap-3 py-6 text-center">
              <div className="grid h-12 w-12 place-items-center rounded-full bg-primary/15 text-primary">
                <Check className="h-6 w-6" />
              </div>
              <p className="font-display text-base font-semibold tracking-tight text-foreground">
                {pluginName} connected
              </p>
            </div>
          )}

          {state === "error" && errorMessage && (
            <div className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
              {errorMessage}
            </div>
          )}
        </div>

        <footer className="flex items-center justify-end gap-2 border-t border-border px-5 py-3">
          <button
            type="button"
            onClick={onClose}
            className="rounded-md border border-border px-3 py-1.5 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            {state === "pending" ? "Cancel" : "Close"}
          </button>
        </footer>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Device Code Dialog — used by GitHub Device Flow. Shows the user_code
// large+copyable, a countdown timer, and long-polls the backend.
// ---------------------------------------------------------------------------

function DeviceCodeDialog({
  flowId,
  pluginId,
  pluginName,
  userCode,
  verificationUri,
  verificationUriComplete,
  expiresAtMs,
  onClose,
  onSuccess,
}: {
  flowId: string;
  pluginId: string;
  pluginName: string;
  userCode: string;
  verificationUri: string;
  verificationUriComplete: string | null;
  expiresAtMs: number | null;
  onClose: () => void;
  onSuccess: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const [secondsLeft, setSecondsLeft] = useState(() =>
    expiresAtMs ? Math.max(0, Math.floor((expiresAtMs - Date.now()) / 1000)) : 0,
  );

  useEffect(() => {
    if (!expiresAtMs) return;
    const t = window.setInterval(() => {
      setSecondsLeft(Math.max(0, Math.floor((expiresAtMs - Date.now()) / 1000)));
    }, 1000);
    return () => window.clearInterval(t);
  }, [expiresAtMs]);

  const poll = useQuery({
    queryKey: ["marketplace-device-poll", flowId],
    queryFn: async () => {
      const res = await fetch(
        `/api/marketplace/plugins/${pluginId}/connect/poll/${flowId}`,
        { cache: "no-store" },
      );
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
        throw new Error(err.detail || `poll failed (HTTP ${res.status})`);
      }
      return res.json() as Promise<{
        state: "pending" | "connected" | "error";
        error?: string;
      }>;
    },
    refetchInterval: (q) => {
      const s = q.state.data?.state;
      return s === "connected" || s === "error" ? false : 2000;
    },
    refetchIntervalInBackground: true,
  });

  useEffect(() => {
    if (poll.data?.state === "connected") {
      const t = window.setTimeout(onSuccess, 800);
      return () => window.clearTimeout(t);
    }
  }, [poll.data?.state, onSuccess]);

  const copyCode = async () => {
    try {
      await navigator.clipboard.writeText(userCode);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    } catch {
      // Ignore — user can still type the code manually.
    }
  };

  const state = poll.data?.state ?? "pending";
  const errorMessage =
    state === "error"
      ? poll.data?.error ?? "Unknown error"
      : poll.error instanceof Error
        ? poll.error.message
        : null;

  const mins = Math.floor(secondsLeft / 60);
  const secs = secondsLeft % 60;

  return (
    <div
      role="dialog"
      aria-modal="true"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="relative w-full max-w-md overflow-hidden rounded-2xl border border-border bg-card shadow-[0_20px_60px_rgba(0,0,0,0.6)]">
        <header className="flex items-center justify-between border-b border-border px-5 py-4">
          <div>
            <h2 className="font-display text-base font-semibold tracking-tight">
              Connect {pluginName}
            </h2>
            <p className="text-[11px] uppercase tracking-wider text-muted-foreground">
              Device flow
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="grid h-7 w-7 place-items-center rounded-full text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            aria-label="Close"
          >
            <X className="h-4 w-4" />
          </button>
        </header>

        <div className="space-y-5 px-5 py-6">
          {state === "pending" && (
            <>
              <div>
                <p className="text-[11px] uppercase tracking-wider text-muted-foreground">
                  Step 1 — copy this code
                </p>
                <button
                  type="button"
                  onClick={copyCode}
                  className="group mt-2 flex w-full items-center justify-between gap-2 rounded-lg border border-border bg-background/60 px-4 py-3 transition-colors hover:border-primary/50 hover:bg-primary/5"
                  title="Copy"
                >
                  <span className="font-mono text-2xl font-semibold tracking-[0.3em] tabular-nums text-foreground">
                    {userCode}
                  </span>
                  <span className="text-[10px] uppercase tracking-wider text-muted-foreground group-hover:text-primary">
                    {copied ? "Copied!" : "Copy"}
                  </span>
                </button>
              </div>

              <div>
                <p className="text-[11px] uppercase tracking-wider text-muted-foreground">
                  Step 2 — paste it on {pluginName}
                </p>
                <a
                  href={verificationUriComplete ?? verificationUri}
                  target="_blank"
                  rel="noopener noreferrer"
                  onClick={(e) => {
                    e.preventDefault();
                    void openExternalUrl(verificationUriComplete ?? verificationUri);
                  }}
                  className="mt-2 inline-flex items-center gap-1.5 rounded-full bg-primary px-3.5 py-1.5 text-xs font-semibold text-primary-foreground transition-all hover:bg-primary/90 hover:shadow-[0_0_16px_rgba(255,214,10,0.4)]"
                >
                  Open {pluginName}
                  <ExternalLink className="h-3 w-3" />
                </a>
                <div className="mt-2">
                  <CopyableUrl
                    url={verificationUri}
                    hint="Or copy this link, open it in your browser, and enter the code above."
                  />
                </div>
              </div>

              <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
                <Loader2 className="h-3 w-3 animate-spin text-primary" />
                Waiting for authorization…
                {secondsLeft > 0 && (
                  <span className="ml-auto font-mono tabular-nums">
                    {String(mins).padStart(2, "0")}:
                    {String(secs).padStart(2, "0")}
                  </span>
                )}
              </div>
            </>
          )}

          {state === "connected" && (
            <div className="flex flex-col items-center gap-3 py-6 text-center">
              <div className="grid h-12 w-12 place-items-center rounded-full bg-primary/15 text-primary">
                <Check className="h-6 w-6" />
              </div>
              <p className="font-display text-base font-semibold tracking-tight text-foreground">
                {pluginName} connected
              </p>
            </div>
          )}

          {state === "error" && errorMessage && (
            <div className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
              {errorMessage}
            </div>
          )}
        </div>

        <footer className="flex items-center justify-end gap-2 border-t border-border px-5 py-3">
          <button
            type="button"
            onClick={onClose}
            className="rounded-md border border-border px-3 py-1.5 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            {state === "pending" ? "Cancel" : "Close"}
          </button>
        </footer>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// PKCE pre-connect dialog — the in-app path to run your OWN production OAuth
// client (no env vars, no catalog edits) plus the honest provider-side hint.
// Shown before the browser sign-in for Google / Slack / Asana.
// ---------------------------------------------------------------------------

export function PkceConnectDialog({
  plugin,
  onClose,
  onProceed,
}: {
  plugin: Plugin;
  onClose: () => void;
  onProceed: () => void | Promise<void>;
}) {
  const fam = OAUTH_CLIENT_FAMILY[plugin.id];
  const isGoogle = fam?.family === "google";
  const [showClient, setShowClient] = useState(false);
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const busyRef = useRef(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !busyRef.current) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const writeSecret = async (key: string, value: string) => {
    const res = await fetch(`/api/secrets/${key}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ value }),
    });
    if (!res.ok) {
      const e = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
      throw new Error(e.detail || `could not save ${key}`);
    }
  };

  const handleContinue = async () => {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setErr(null);
    try {
      const cid = clientId.trim();
      const csec = clientSecret.trim();
      // Only write when the user actually entered a client — an empty field must
      // never clobber an already-stored secret or the catalog default.
      if (fam && cid) {
        await writeSecret(`${fam.family}_oauth_client_id`, cid);
        if (csec) await writeSecret(`${fam.family}_oauth_client_secret`, csec);
      }
      await onProceed();
      onClose();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="pkce-connect-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm"
      onClick={(e) => {
        if (e.target === e.currentTarget && !busy) onClose();
      }}
    >
      <div className="relative w-full max-w-md overflow-hidden rounded-2xl border border-border bg-card shadow-[0_20px_60px_rgba(0,0,0,0.6)]">
        <header className="flex items-center gap-3 border-b border-border px-5 py-4">
          <div className="grid h-10 w-10 shrink-0 place-items-center rounded-md border border-border/60 bg-white">
            <img
              src={resolveLogoUrl(plugin)}
              alt=""
              className={cn(plugin.logoUrl ? "h-7 w-7" : "h-5 w-5")}
            />
          </div>
          <div className="min-w-0">
            <h2
              id="pkce-connect-title"
              className="font-display text-sm font-semibold tracking-tight"
            >
              Connect {plugin.name}
            </h2>
            <p className="text-[11px] text-muted-foreground">
              You'll sign in with your {fam?.label ?? "provider"} account in the
              browser.
            </p>
          </div>
        </header>

        <div className="space-y-3 px-5 py-4">
          {isGoogle && (
            <div className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2.5 text-[11px] leading-relaxed">
              <p className="font-medium text-amber-300">
                Keep it connected permanently
              </p>
              <p className="mt-1 text-amber-200/90">
                Google drops the connection every 7 days while your OAuth app is
                in "Testing". Publish your app to <strong>In production</strong>{" "}
                (it can stay unverified for personal use) so it never expires.
              </p>
              <a
                href="https://console.cloud.google.com/auth/audience"
                target="_blank"
                rel="noreferrer"
                className="mt-1.5 inline-flex items-center gap-1 font-medium text-amber-300 underline underline-offset-2 hover:text-amber-200"
              >
                Open Google Cloud Console <ExternalLink className="h-3 w-3" />
              </a>
            </div>
          )}

          {fam && (
            <div>
              <button
                type="button"
                onClick={() => setShowClient((v) => !v)}
                className="text-[11px] font-medium text-muted-foreground underline underline-offset-2 hover:text-foreground"
              >
                Use your own OAuth client (advanced)
              </button>
              {showClient && (
                <div className="mt-2 space-y-2">
                  <p className="text-[10px] leading-relaxed text-muted-foreground">
                    Optional. Paste a client from your own {fam.label}{" "}
                    {OAUTH_CLIENT_CONSOLE[fam.family] && (
                      <a
                        href={OAUTH_CLIENT_CONSOLE[fam.family]}
                        target="_blank"
                        rel="noreferrer"
                        className="underline underline-offset-2 hover:text-foreground"
                      >
                        console
                      </a>
                    )}
                    .{" "}
                    {fam.family === "google" &&
                      "One client covers Gmail, Drive and Calendar."}
                  </p>
                  <div>
                    <label
                      htmlFor="pkce-client-id"
                      className="block text-[10px] font-medium uppercase tracking-wider text-muted-foreground"
                    >
                      Client ID
                    </label>
                    <input
                      id="pkce-client-id"
                      value={clientId}
                      onChange={(e) => setClientId(e.target.value)}
                      className="mt-1 h-8 w-full rounded-md border border-border bg-background/60 px-2 text-xs text-foreground placeholder:text-muted-foreground/60 focus:border-primary/40 focus:outline-none"
                      placeholder="…apps.googleusercontent.com"
                    />
                  </div>
                  <div>
                    <label
                      htmlFor="pkce-client-secret"
                      className="block text-[10px] font-medium uppercase tracking-wider text-muted-foreground"
                    >
                      Client Secret
                    </label>
                    <input
                      id="pkce-client-secret"
                      type="password"
                      value={clientSecret}
                      onChange={(e) => setClientSecret(e.target.value)}
                      className="mt-1 h-8 w-full rounded-md border border-border bg-background/60 px-2 text-xs text-foreground placeholder:text-muted-foreground/60 focus:border-primary/40 focus:outline-none"
                      placeholder="optional for some providers"
                    />
                  </div>
                </div>
              )}
            </div>
          )}

          {err && (
            <div className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
              {err}
            </div>
          )}
        </div>

        <footer className="flex items-center justify-end gap-2 border-t border-border px-5 py-3">
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            className="rounded-md border border-border px-3 py-1.5 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-60"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleContinue}
            disabled={busy}
            className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-60"
          >
            {busy && <Loader2 className="h-3 w-3 animate-spin" />}
            Continue
          </button>
        </footer>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Disconnect Confirm Dialog — guards the destructive "remove plugin" action.
// Clicking the connected ✓ no longer disconnects immediately; it asks first.
// ---------------------------------------------------------------------------

function DisconnectConfirmDialog({
  plugin,
  isPending,
  onCancel,
  onConfirm,
  errorMessage,
}: {
  plugin: Plugin;
  isPending: boolean;
  onCancel: () => void;
  onConfirm: () => void;
  errorMessage: string | null;
}) {
  const assistantName = useEventStore((s) => s.assistantName);
  // Close on Escape (unless a removal is in flight).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !isPending) onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel, isPending]);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="disconnect-dialog-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm"
      onClick={(e) => {
        if (e.target === e.currentTarget && !isPending) onCancel();
      }}
    >
      <div className="relative w-full max-w-sm overflow-hidden rounded-2xl border border-border bg-card shadow-[0_20px_60px_rgba(0,0,0,0.6)]">
        <header className="flex items-center justify-between border-b border-border px-5 py-4">
          <div className="flex items-center gap-3">
            <div className="grid h-10 w-10 shrink-0 place-items-center rounded-md border border-border/60 bg-white">
              <img
                src={resolveLogoUrl(plugin)}
                alt=""
                className={cn(plugin.logoUrl ? "h-7 w-7" : "h-5 w-5")}
              />
            </div>
            <div>
              <h2
                id="disconnect-dialog-title"
                className="font-display text-base font-semibold tracking-tight"
              >
                Remove {plugin.name}?
              </h2>
              <p className="text-[11px] uppercase tracking-wider text-muted-foreground">
                Disconnect plugin
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onCancel}
            disabled={isPending}
            className="grid h-7 w-7 place-items-center rounded-full text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-30"
            aria-label="Cancel"
          >
            <X className="h-4 w-4" />
          </button>
        </header>

        <div className="px-5 py-5">
          <p className="text-sm leading-relaxed text-muted-foreground">
            This disconnects{" "}
            <span className="font-medium text-foreground">{plugin.name}</span> and
            deletes its stored credentials. {assistantName} loses access until you reconnect it.
          </p>
          {errorMessage && (
            <div className="mt-3 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
              {errorMessage}
            </div>
          )}
        </div>

        <footer className="flex items-center justify-end gap-2 border-t border-border px-5 py-3">
          <button
            type="button"
            onClick={onCancel}
            disabled={isPending}
            className="rounded-md border border-border px-3 py-1.5 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-30"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={isPending}
            className="inline-flex items-center gap-1.5 rounded-md bg-destructive px-3.5 py-1.5 text-xs font-semibold text-destructive-foreground transition-all hover:bg-destructive/90 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {isPending ? (
              <>
                <Loader2 className="h-3 w-3 animate-spin" />
                Removing…
              </>
            ) : (
              "Remove"
            )}
          </button>
        </footer>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Pat-Paste Connect Dialog — used by Vercel & Supabase. Opens the token-
// creation page in a new tab, takes a paste, sends it to the backend.
// ---------------------------------------------------------------------------

// Channel plugins (bidirectional chat over Telegram/Discord) can be locked to
// the owner: connecting captures the owner's numeric user id so the bot obeys
// only them. Other plugins never show the field.
const OWNER_LOCK_PLUGIN_IDS = new Set(["telegram", "discord"]);

export function PatConnectDialog({
  plugin,
  onClose,
  onSubmit,
  isPending,
  errorMessage,
}: {
  plugin: Plugin;
  onClose: () => void;
  onSubmit: (token: string, allowedUserId: number | null) => void;
  isPending: boolean;
  errorMessage: string | null;
}) {
  const [token, setToken] = useState("");
  const [userId, setUserId] = useState("");
  const ownerLock = OWNER_LOCK_PLUGIN_IDS.has(plugin.id);
  const auth = plugin.authConfig as unknown as PatPasteAuthDetail;
  const expectedPrefix = auth.token_prefix ?? "";
  const prefixOk = !expectedPrefix || token.trim().startsWith(`${expectedPrefix}_`);
  const userIdTrimmed = userId.trim();
  const userIdOk = !ownerLock || userIdTrimmed === "" || /^\d+$/.test(userIdTrimmed);
  const parsedUserId =
    ownerLock && /^\d+$/.test(userIdTrimmed) ? Number(userIdTrimmed) : null;
  const canSubmit = token.trim().length > 0 && prefixOk && userIdOk && !isPending;
  const submit = () => onSubmit(token.trim(), parsedUserId);

  // Close on Escape — small but expected affordance.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !isPending) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, isPending]);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="pat-dialog-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm"
      onClick={(e) => {
        if (e.target === e.currentTarget && !isPending) onClose();
      }}
    >
      <div className="relative w-full max-w-md overflow-hidden rounded-2xl border border-border bg-card shadow-[0_20px_60px_rgba(0,0,0,0.6)]">
        <header className="flex items-center justify-between border-b border-border px-5 py-4">
          <div className="flex items-center gap-3">
            <div className="grid h-10 w-10 shrink-0 place-items-center rounded-md border border-border/60 bg-white">
              <img
                src={resolveLogoUrl(plugin)}
                alt=""
                className={cn(plugin.logoUrl ? "h-7 w-7" : "h-5 w-5")}
              />
            </div>
            <div>
              <h2
                id="pat-dialog-title"
                className="font-display text-base font-semibold tracking-tight"
              >
                Connect {plugin.name}
              </h2>
              <p className="text-[11px] uppercase tracking-wider text-muted-foreground">
                Access token · {AUTH_LABELS[plugin.authMode]}
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={isPending}
            className="grid h-7 w-7 place-items-center rounded-full text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-30"
            aria-label="Close"
          >
            <X className="h-4 w-4" />
          </button>
        </header>

        <div className="space-y-5 px-5 py-5">
          <Step
            num={1}
            title={`Generate a token at ${plugin.name}`}
            body={auth.instruction_md}
          >
            <a
              href={auth.token_creation_url}
              target="_blank"
              rel="noopener noreferrer"
              onClick={(e) => {
                e.preventDefault();
                void openExternalUrl(auth.token_creation_url);
              }}
              className="mt-2 inline-flex items-center gap-1.5 rounded-full bg-primary px-3.5 py-1.5 text-xs font-semibold text-primary-foreground transition-all hover:bg-primary/90 hover:shadow-[0_0_16px_rgba(255,214,10,0.4)]"
            >
              Open {plugin.name} tokens
              <ExternalLink className="h-3 w-3" />
            </a>
            <div className="mt-2">
              <CopyableUrl
                url={auth.token_creation_url}
                hint="Or copy this link and open it in your browser yourself."
              />
            </div>
          </Step>

          <Step num={2} title="Paste the token below">
            <input
              type="password"
              autoComplete="off"
              spellCheck={false}
              value={token}
              onChange={(e) => setToken(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && canSubmit) submit();
              }}
              placeholder={expectedPrefix ? `${expectedPrefix}_…` : "Token"}
              className="mt-2 w-full rounded-md border border-border bg-input px-3 py-2 font-mono text-xs text-foreground placeholder:text-muted-foreground/40 focus:border-primary/50 focus:outline-none focus:ring-1 focus:ring-primary/30"
              autoFocus
              disabled={isPending}
            />
            {token && expectedPrefix && !prefixOk && (
              <p className="mt-1.5 text-[11px] text-amber-400">
                Should start with{" "}
                <span className="font-mono">{expectedPrefix}_</span>
              </p>
            )}
          </Step>

          {ownerLock && (
            <Step num={3} title="Lock the bot to you (recommended)">
              <input
                type="text"
                inputMode="numeric"
                autoComplete="off"
                spellCheck={false}
                aria-label="Your numeric user id"
                value={userId}
                onChange={(e) => setUserId(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && canSubmit) submit();
                }}
                placeholder="123456789"
                className="mt-2 w-full rounded-md border border-border bg-input px-3 py-2 font-mono text-xs text-foreground placeholder:text-muted-foreground/40 focus:border-primary/50 focus:outline-none focus:ring-1 focus:ring-primary/30"
                disabled={isPending}
              />
              <p className="mt-1.5 text-[11px] text-muted-foreground">
                Only this user id can command the bot. Leave blank to let the
                first person who messages it claim access instead.
              </p>
              {userIdTrimmed !== "" && !userIdOk && (
                <p className="mt-1 text-[11px] text-amber-400">
                  User id must be digits only.
                </p>
              )}
            </Step>
          )}

          {errorMessage && (
            <div className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
              {errorMessage}
            </div>
          )}
        </div>

        <footer className="flex items-center justify-end gap-2 border-t border-border px-5 py-3">
          <button
            type="button"
            onClick={onClose}
            disabled={isPending}
            className="rounded-md border border-border px-3 py-1.5 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-30"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={submit}
            disabled={!canSubmit}
            className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3.5 py-1.5 text-xs font-semibold text-primary-foreground transition-all hover:bg-primary/90 hover:shadow-[0_0_16px_rgba(255,214,10,0.4)] disabled:cursor-not-allowed disabled:opacity-40"
          >
            {isPending ? (
              <>
                <Loader2 className="h-3 w-3 animate-spin" />
                Validating…
              </>
            ) : (
              <>
                Connect
                <ArrowRight className="h-3 w-3" />
              </>
            )}
          </button>
        </footer>
      </div>
    </div>
  );
}

function Step({
  num,
  title,
  body,
  children,
}: {
  num: number;
  title: string;
  body?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex gap-3">
      <span className="grid h-6 w-6 shrink-0 place-items-center rounded-full border border-border text-[11px] font-semibold text-muted-foreground">
        {num}
      </span>
      <div className="min-w-0 flex-1">
        <h3 className="text-sm font-semibold text-foreground">{title}</h3>
        {body && (
          <p className="mt-0.5 whitespace-pre-line text-xs leading-relaxed text-muted-foreground">
            {body}
          </p>
        )}
        {children}
      </div>
    </div>
  );
}
