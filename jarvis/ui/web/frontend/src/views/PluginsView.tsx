import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEventStore } from "@/store/events";
import {
  Blocks,
  ArrowRight,
  ExternalLink,
  Search,
  RefreshCw,
  Plus,
  Check,
  Sparkles,
  X,
  Loader2,
} from "lucide-react";
import { ViewHeader } from "@/views/ChatsView";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

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
  const res = await fetch("/api/marketplace/plugins");
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

  const handleConnect = async (p: Plugin) => {
    if (p.authMode === "pat_paste") {
      setConnectingPlugin(p);
      return;
    }
    if (
      p.authMode === "hosted_mcp_oauth_dcr" ||
      p.authMode === "oauth_pkce_loopback" ||
      p.authMode === "oauth_device_flow"
    ) {
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
            window.open(verifyUrlComplete, "_blank", "noopener,noreferrer");
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
        window.open(r.open_url, "_blank", "noopener,noreferrer");
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
  const installed = useMemo(
    () => allPlugins.filter((p) => p.status === "connected"),
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
              : `${allPlugins.length} available · ${installed.length} connected`
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
// Connect-handler prop type, threaded through the component tree
// ---------------------------------------------------------------------------

interface ConnectHandlers {
  onConnect: (p: Plugin) => void;
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
  const isMulticolor = !!plugin.logoUrl;

  return (
    <article
      className={cn(
        "group flex items-center gap-3 rounded-lg border bg-card/40 px-3 py-2.5 transition-colors",
        isConnected
          ? "border-primary/30"
          : "border-border hover:border-primary/40 hover:bg-card/70",
      )}
    >
      <div className="grid h-10 w-10 shrink-0 place-items-center rounded-md border border-border/60 bg-background/60">
        <img
          src={resolveLogoUrl(plugin)}
          alt=""
          className={cn(isMulticolor ? "h-7 w-7" : "h-5 w-5")}
          loading="lazy"
        />
      </div>

      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <h3 className="truncate text-sm font-semibold tracking-tight text-foreground">
            {plugin.name}
          </h3>
          {isConnected && (
            <span className="text-[9px] font-medium uppercase tracking-wider text-primary">
              · Connected
            </span>
          )}
          {isConnected && plugin.liveCallable && (
            <span className="text-[9px] font-medium uppercase tracking-wider text-emerald-400">
              · Live
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

function ConnectIconButton({
  status,
  onConnect,
  onDisconnect,
}: {
  status: PluginStatus;
  onConnect: () => void;
  onDisconnect: () => void;
}) {
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
  return (
    <button
      type="button"
      onClick={onConnect}
      className="grid h-7 w-7 shrink-0 place-items-center rounded-full border border-border bg-background/60 text-muted-foreground transition-all hover:border-primary/50 hover:bg-primary/10 hover:text-primary group-hover:scale-105"
      aria-label="Connect plugin"
    >
      <Plus className="h-3.5 w-3.5" />
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
                className="text-[11px] uppercase tracking-wider text-muted-foreground/80 underline-offset-4 hover:text-primary hover:underline"
              >
                Tab didn't open? Click here
              </a>
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
                  className="mt-2 inline-flex items-center gap-1.5 rounded-full bg-primary px-3.5 py-1.5 text-xs font-semibold text-primary-foreground transition-all hover:bg-primary/90 hover:shadow-[0_0_16px_rgba(255,214,10,0.4)]"
                >
                  Open {pluginName}
                  <ExternalLink className="h-3 w-3" />
                </a>
                <p className="mt-1.5 text-[11px] text-muted-foreground/80">
                  Or visit{" "}
                  <span className="font-mono">{verificationUri}</span> manually.
                </p>
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
            <div className="grid h-10 w-10 shrink-0 place-items-center rounded-md border border-border/60 bg-background/60">
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
            <div className="grid h-10 w-10 shrink-0 place-items-center rounded-md border border-border/60 bg-background/60">
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
              className="mt-2 inline-flex items-center gap-1.5 rounded-full bg-primary px-3.5 py-1.5 text-xs font-semibold text-primary-foreground transition-all hover:bg-primary/90 hover:shadow-[0_0_16px_rgba(255,214,10,0.4)]"
            >
              Open {plugin.name} tokens
              <ExternalLink className="h-3 w-3" />
            </a>
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
