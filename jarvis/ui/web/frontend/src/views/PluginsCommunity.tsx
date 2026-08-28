import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Check,
  ExternalLink,
  Loader2,
  RefreshCw,
  Search,
  Store,
  Trash2,
  UploadCloud,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { InstallTerminal } from "@/components/InstallTerminal";
import { cn } from "@/lib/utils";
import { installBlock } from "@/lib/installStandard";
import { openExternalUrl } from "@/lib/openExternal";
import { PRODUCT_NAME } from "@/lib/branding";
import { useEventStore } from "@/store/events";

// ---------------------------------------------------------------------------
// Community marketplace tab.
//
// Everything here is UNREVIEWED third-party content: the registry auto-merges
// submissions that pass automated checks, so the consent dialog below is the
// trust boundary — it shows verbatim where data would flow (hosted MCP URL)
// or what command would run (stdio argv) BEFORE anything is installed.
// ---------------------------------------------------------------------------

/** The storefront's submit page — where "Publish your own" sends authors. */
export const MARKETPLACE_SUBMIT_URL = "https://personaljarvis.ai/marketplace/submit";

// Wire types — mirror /api/marketplace/community (see marketplace_routes.py).
export interface CommunityPluginWire {
  name: string;
  valid: boolean;
  error?: string;
  publisher?: string | null;
  version?: string | null;
  published_at?: string | null;
  source_url?: string | null;
  // Present only when valid — the converted PluginSpec fields.
  id?: string;
  display_name?: string;
  description?: string;
  category?: string;
  logo_slug?: string;
  logo_color?: string | null;
  logo_url?: string | null;
  auth?: { mode: string; [key: string]: unknown };
  mcp_server?: {
    transport?: string;
    url?: string;
    install?: string[];
  } | null;
  installed?: boolean;
  installed_version?: string | null;
  seed_conflict?: boolean;
  has_usage_card?: boolean;
  post_install_hint_md?: string | null;
}

export interface CommunitySkillWire {
  name: string;
  title: string;
  description: string;
  publisher?: string | null;
  version?: string | null;
  categories: string[];
  source_url?: string | null;
  raw_url?: string | null;
  installed: boolean;
  /**
   * Which frontmatter the published SKILL.md carries: `"jarvis"` uses this
   * app's schema (triggers, risk policy), `"portable"` is a plain Agent Skill
   * written for the open ecosystem. Absent on an older index — treated as
   * `"jarvis"`, which is what every entry published before the split was.
   */
  flavor?: "jarvis" | "portable" | null;
  /** Agents the publisher states this skill works in, for a portable entry. */
  compatible_agents?: string[] | null;
}

/** The one-line "runs in other agents too" note, or null for a Jarvis skill. */
function portableNote(skill: CommunitySkillWire): string | null {
  if (skill.flavor !== "portable") return null;
  const agents = (skill.compatible_agents ?? []).filter(Boolean);
  return agents.length > 0
    ? `Portable skill · also runs in ${agents.join(", ")}`
    : "Portable skill · also runs in other agents";
}

export interface CommunityWallpaperWire {
  name: string;
  title: string;
  description: string;
  publisher?: string | null;
  version?: string | null;
  categories: string[];
  source_url?: string | null;
  raw_url?: string | null;
  /** The small preview the registry publishes next to the full image. */
  thumb_url?: string | null;
  /** SPDX id the publisher declared for the artwork. */
  license?: string | null;
  theme?: string | null;
  installed: boolean;
}

export interface CommunityResponse {
  status: "fresh" | "fetched" | "stale" | "unavailable" | "disabled";
  revision?: number | null;
  generated_at?: string | null;
  plugins: CommunityPluginWire[];
  skills: CommunitySkillWire[];
  wallpapers?: CommunityWallpaperWire[];
}

/** One readable file of a published entry — mirrors ``_text_file`` server-side. */
export interface EntryFileWire {
  path: string;
  size: number;
  text: string;
  truncated: boolean;
}

export interface EntryContentsWire {
  kind: "skill" | "plugin" | "wallpaper";
  name: string;
  title: string;
  root: string;
  files: EntryFileWire[];
  image_url?: string | null;
  error?: string | null;
}

async function fetchCommunity(): Promise<CommunityResponse> {
  const res = await fetch("/api/marketplace/community", { cache: "no-store" });
  if (!res.ok) throw new Error(`Community index request failed (${res.status})`);
  return res.json();
}

/** The published package of ONE entry, fetched only when a card is opened.
 *
 *  Never part of the browse request: pulling every skill's text just to draw a
 *  list would download the whole registry to show two lines per card. */
function useEntryContents(name: string | null) {
  return useQuery({
    queryKey: ["marketplace-community-contents", name],
    enabled: name !== null,
    // The published bytes of a given version do not change under us, and the
    // server caches the download too — refetching on every reopen is waste.
    staleTime: 5 * 60 * 1000,
    queryFn: async (): Promise<EntryContentsWire> => {
      const res = await fetch(
        `/api/marketplace/community/${encodeURIComponent(name ?? "")}/contents`,
        { cache: "no-store" },
      );
      if (!res.ok) throw new Error(`Could not read that entry (${res.status})`);
      return res.json();
    },
  });
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  return `${(bytes / 1024).toFixed(1)} kB`;
}

const AUTH_MODE_LABEL: Record<string, string> = {
  pat_paste: "Personal access token",
  oauth_device_flow: "Device sign-in",
  hosted_mcp_oauth_dcr: "OAuth sign-in",
  oauth_pkce_loopback: "OAuth sign-in",
  hosted_mcp_allowlist: "Account allowlist",
};

export function CommunityTab() {
  const queryClient = useQueryClient();
  // The storefront section shows the same index with room to browse it; this
  // tab keeps the plugin connect flow next to the installed plugins.
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const { data, isLoading, error } = useQuery({
    queryKey: ["marketplace-community"],
    queryFn: fetchCommunity,
  });
  const [query, setQuery] = useState("");
  const [consentPlugin, setConsentPlugin] = useState<CommunityPluginWire | null>(null);
  const [consentSkill, setConsentSkill] = useState<CommunitySkillWire | null>(null);
  const [consentPaper, setConsentPaper] = useState<CommunityWallpaperWire | null>(null);

  const refreshMutation = useMutation({
    mutationFn: async (): Promise<CommunityResponse> => {
      const res = await fetch("/api/marketplace/community/refresh", { method: "POST" });
      if (!res.ok) throw new Error(`Refresh failed (${res.status})`);
      return res.json();
    },
    onSuccess: (fresh) => {
      queryClient.setQueryData(["marketplace-community"], fresh);
    },
  });

  const installMutation = useMutation({
    mutationFn: async (name: string) => {
      const res = await fetch(
        `/api/marketplace/community/plugins/${encodeURIComponent(name)}/install`,
        { method: "POST" },
      );
      if (!res.ok) {
        const detail = await res
          .json()
          .then((body: { detail?: string }) => body.detail)
          .catch(() => undefined);
        throw new Error(detail ?? `Install failed (${res.status})`);
      }
      return res.json();
    },
    onSuccess: () => {
      setConsentPlugin(null);
      // The installed plugin is now a first-class store card — both lists
      // must reflect it.
      queryClient.invalidateQueries({ queryKey: ["marketplace-community"] });
      queryClient.invalidateQueries({ queryKey: ["marketplace-plugins"] });
    },
  });

  const uninstallMutation = useMutation({
    mutationFn: async (name: string) => {
      const res = await fetch(
        `/api/marketplace/community/plugins/${encodeURIComponent(name)}`,
        { method: "DELETE" },
      );
      if (!res.ok) throw new Error(`Remove failed (${res.status})`);
      return res.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["marketplace-community"] });
      queryClient.invalidateQueries({ queryKey: ["marketplace-plugins"] });
    },
  });

  const skillInstallMutation = useMutation({
    mutationFn: async (skill: CommunitySkillWire) => {
      // The by-name route, same as the wallpaper below: it runs the existing
      // catalog install (download, validation, registry hot-swap) and then
      // writes the origin receipt. Posting to the catalog route directly
      // skipped that receipt, so a skill installed from this card arrived
      // with no mark on it — installed from the marketplace and unable to
      // say so, which is the one thing this card exists to make visible.
      const res = await fetch(
        `/api/marketplace/community/install/${encodeURIComponent(skill.name)}`,
        { method: "POST" },
      );
      if (!res.ok) {
        const detail = await res
          .json()
          .then((body: { detail?: string }) => body.detail)
          .catch(() => undefined);
        throw new Error(detail ?? `Install failed (${res.status})`);
      }
      return res.json();
    },
    onSuccess: () => {
      setConsentSkill(null);
      queryClient.invalidateQueries({ queryKey: ["marketplace-community"] });
      queryClient.invalidateQueries({ queryKey: ["skills"] });
    },
  });

  const paperInstallMutation = useMutation({
    mutationFn: async (paper: CommunityWallpaperWire) => {
      // The by-name route already resolves the kind and does the download,
      // re-encode and origin receipt — a picture needs no second install path.
      const res = await fetch(
        `/api/marketplace/community/install/${encodeURIComponent(paper.name)}`,
        { method: "POST" },
      );
      if (!res.ok) {
        const detail = await res
          .json()
          .then((body: { detail?: string }) => body.detail)
          .catch(() => undefined);
        throw new Error(detail ?? `Install failed (${res.status})`);
      }
      return res.json();
    },
    onSuccess: () => {
      setConsentPaper(null);
      queryClient.invalidateQueries({ queryKey: ["marketplace-community"] });
      // The picker reads its own store — it has to learn about the new tile.
      queryClient.invalidateQueries({ queryKey: ["wallpapers"] });
    },
  });

  const q = query.trim().toLowerCase();
  const plugins = useMemo(
    () =>
      (data?.plugins ?? []).filter((p) => {
        if (!q) return true;
        return (
          p.name.toLowerCase().includes(q) ||
          (p.display_name ?? "").toLowerCase().includes(q) ||
          (p.description ?? "").toLowerCase().includes(q)
        );
      }),
    [data, q],
  );
  const skills = useMemo(
    () =>
      (data?.skills ?? []).filter((s) => {
        if (!q) return true;
        return (
          s.name.toLowerCase().includes(q) ||
          s.title.toLowerCase().includes(q) ||
          s.description.toLowerCase().includes(q)
        );
      }),
    [data, q],
  );
  const wallpapers = useMemo(
    () =>
      (data?.wallpapers ?? []).filter((w) => {
        if (!q) return true;
        return (
          w.name.toLowerCase().includes(q) ||
          w.title.toLowerCase().includes(q) ||
          w.description.toLowerCase().includes(q)
        );
      }),
    [data, q],
  );

  return (
    <div className="space-y-8">
      <header className="flex flex-wrap items-center gap-3">
        <div className="min-w-0 flex-1">
          <h2 className="font-display text-base font-semibold tracking-tight text-foreground">
            Community marketplace
          </h2>
          <p className="text-xs text-muted-foreground">
            Plugins, skills and wallpapers published by anyone. Nothing here is
            reviewed by the {PRODUCT_NAME} team — read what a card would connect
            to before installing it.
          </p>
        </div>
        <Button
          size="sm"
          variant="outline"
          onClick={() => setActiveSection("marketplace")}
          title="The whole marketplace — plugins, skills and wallpapers — in its own section"
        >
          <Store className="mr-1.5 h-3.5 w-3.5" />
          Marketplace section
        </Button>
        <Button
          size="sm"
          variant="outline"
          onClick={() => openExternalUrl(MARKETPLACE_SUBMIT_URL)}
          title="Publish your own plugin or skill on the marketplace website"
        >
          <UploadCloud className="mr-1.5 h-3.5 w-3.5" />
          Publish your own
        </Button>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => refreshMutation.mutate()}
          disabled={refreshMutation.isPending}
          title="Re-fetch the community index"
        >
          <RefreshCw
            className={cn("h-3.5 w-3.5", refreshMutation.isPending && "animate-spin")}
          />
        </Button>
      </header>

      <StatusNotice status={data?.status} error={error} isLoading={isLoading} />

      {(data?.plugins.length ?? 0) +
        (data?.skills.length ?? 0) +
        (data?.wallpapers?.length ?? 0) >
        0 && (
        <label className="flex items-center gap-2 rounded-lg border border-border bg-card/40 px-3 py-2">
          <Search className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search community plugins, skills and wallpapers…"
            className="w-full bg-transparent text-sm text-foreground outline-none placeholder:text-muted-foreground"
          />
        </label>
      )}

      {plugins.length > 0 && (
        <section>
          <h3 className="mb-3 font-display text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">
            Plugins
          </h3>
          <div className="grid gap-2 sm:grid-cols-2">
            {plugins.map((p) => (
              <CommunityPluginRow
                key={p.name}
                plugin={p}
                onInstall={() => setConsentPlugin(p)}
                onUninstall={() => uninstallMutation.mutate(p.name)}
                uninstalling={
                  uninstallMutation.isPending &&
                  uninstallMutation.variables === p.name
                }
              />
            ))}
          </div>
        </section>
      )}

      {skills.length > 0 && (
        <section>
          <h3 className="mb-3 font-display text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">
            Skills
          </h3>
          <div className="grid gap-2 sm:grid-cols-2">
            {skills.map((s) => (
              <CommunitySkillRow
                key={s.name}
                skill={s}
                onInstall={() => setConsentSkill(s)}
                installing={
                  skillInstallMutation.isPending &&
                  skillInstallMutation.variables?.name === s.name
                }
                installError={
                  skillInstallMutation.variables?.name === s.name &&
                  skillInstallMutation.error instanceof Error
                    ? skillInstallMutation.error.message
                    : null
                }
              />
            ))}
          </div>
        </section>
      )}

      {wallpapers.length > 0 && (
        <section>
          <h3 className="mb-3 font-display text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">
            Wallpapers
          </h3>
          <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-4">
            {wallpapers.map((w) => (
              <CommunityWallpaperCard
                key={w.name}
                paper={w}
                onOpen={() => setConsentPaper(w)}
              />
            ))}
          </div>
        </section>
      )}

      {consentPaper && (
        <WallpaperPreviewDialog
          paper={consentPaper}
          isPending={paperInstallMutation.isPending}
          errorMessage={
            paperInstallMutation.error instanceof Error
              ? paperInstallMutation.error.message
              : null
          }
          onCancel={() => {
            setConsentPaper(null);
            paperInstallMutation.reset();
          }}
          onConfirm={() => paperInstallMutation.mutate(consentPaper)}
        />
      )}

      {consentPlugin && (
        <InstallConsentDialog
          plugin={consentPlugin}
          isPending={installMutation.isPending}
          errorMessage={
            installMutation.error instanceof Error
              ? installMutation.error.message
              : null
          }
          onCancel={() => {
            setConsentPlugin(null);
            installMutation.reset();
          }}
          onConfirm={() => installMutation.mutate(consentPlugin.name)}
        />
      )}

      {consentSkill && (
        <SkillInstallConsentDialog
          skill={consentSkill}
          isPending={skillInstallMutation.isPending}
          errorMessage={
            skillInstallMutation.error instanceof Error
              ? skillInstallMutation.error.message
              : null
          }
          onCancel={() => {
            setConsentSkill(null);
            skillInstallMutation.reset();
          }}
          onConfirm={() => skillInstallMutation.mutate(consentSkill)}
        />
      )}
    </div>
  );
}

/** Honest feed state. `stale` matters most: the list still renders, but the
 *  user should know it is yesterday's copy, not silently trust it as live. */
function StatusNotice({
  status,
  error,
  isLoading,
}: {
  status: CommunityResponse["status"] | undefined;
  error: unknown;
  isLoading: boolean;
}) {
  if (isLoading) {
    return (
      <p className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading community index…
      </p>
    );
  }
  if (error) {
    return (
      <Notice tone="error">
        The community index could not be loaded. Check the connection and try
        Refresh.
      </Notice>
    );
  }
  if (status === "disabled") {
    return (
      <Notice tone="muted">
        The community marketplace is switched off in the configuration
        (marketplace.community_index_url is empty).
      </Notice>
    );
  }
  if (status === "unavailable") {
    return (
      <Notice tone="error">
        The community index is unreachable and no saved copy exists yet.
        Connect to the internet once to load it.
      </Notice>
    );
  }
  if (status === "stale") {
    return (
      <Notice tone="warn">
        Showing a saved copy — the index could not be refreshed just now.
      </Notice>
    );
  }
  return null;
}

function Notice({
  tone,
  children,
}: {
  tone: "muted" | "warn" | "error";
  children: React.ReactNode;
}) {
  return (
    <p
      className={cn(
        "flex items-start gap-2 rounded-lg border px-3 py-2 text-xs",
        tone === "muted" && "border-border bg-card/40 text-muted-foreground",
        tone === "warn" && "border-foreground/40 bg-foreground/5 text-foreground",
        tone === "error" &&
          "border-destructive/40 bg-destructive/5 text-destructive",
      )}
    >
      {tone !== "muted" && <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />}
      <span>{children}</span>
    </p>
  );
}

/** The published package itself, readable before anything is installed.
 *
 *  This is the answer to the badge above it: a card that says "not reviewed"
 *  is only honest if the reader can see what they would be installing. Files
 *  are shown open — a fold would put the content one click away again, which
 *  is the state this panel exists to end. */
function ContentsPanel({ name }: { name: string }) {
  const { data, isLoading, error } = useEntryContents(name);

  if (isLoading) {
    return (
      <p className="flex items-center gap-2 text-xs text-muted-foreground">
        <Loader2 className="h-3.5 w-3.5 animate-spin" /> Reading the published files…
      </p>
    );
  }
  if (error || !data) {
    return (
      <Notice tone="warn">
        The published files could not be read just now. The install below is
        unaffected — but you would be installing something you have not seen.
      </Notice>
    );
  }
  if (data.error) return <Notice tone="warn">{data.error}</Notice>;
  if (data.files.length === 0) return null;

  const total = data.files.reduce((sum, file) => sum + file.size, 0);
  return (
    <div>
      <div className="mb-1.5 flex items-baseline justify-between gap-3">
        <p className="text-xs font-medium text-foreground">What's inside</p>
        <p className="shrink-0 text-[11px] text-muted-foreground">
          {data.files.length === 1 ? "1 file" : `${data.files.length} files`} ·{" "}
          {formatBytes(total)}
        </p>
      </div>
      <div className="overflow-hidden rounded-md border border-border">
        <p className="border-b border-border bg-muted/40 px-2.5 py-1.5 font-mono text-[11px] text-muted-foreground">
          {data.root}
        </p>
        {data.files.map((file) => (
          <div key={file.path} className="border-b border-border last:border-b-0">
            <div className="flex items-baseline justify-between gap-3 bg-card/60 px-2.5 py-1.5">
              <span className="truncate font-mono text-[11px] text-foreground">
                {file.path}
              </span>
              <span className="shrink-0 font-mono text-[10px] text-muted-foreground">
                {formatBytes(file.size)}
              </span>
            </div>
            {/* Long lines scroll inside the file rather than widening the
                dialog. The text is interpolated, never dangerouslySetInnerHTML
                — a publisher's file is untrusted text and stays text. */}
            <pre className="max-h-[420px] overflow-auto bg-background/60 px-2.5 py-2 font-mono text-[11px] leading-relaxed text-muted-foreground">
              {file.text}
            </pre>
            {file.truncated && (
              <p className="bg-card/60 px-2.5 py-1.5 text-[11px] text-foreground">
                Shown up to 256 kB — the published file is longer. Open the
                source to read the rest.
              </p>
            )}
          </div>
        ))}
      </div>
      <p className="mt-1.5 text-[11px] text-muted-foreground/70">
        Exactly as published. These are the bytes {PRODUCT_NAME} downloads when
        you install it — nothing is added or rewritten in between.
      </p>
    </div>
  );
}

/** Coloured brand tile with monogram fallback — the same three-tier idea as
 *  the main store, minus bundled assets (community brands ship none). */
function CommunityTile({ plugin }: { plugin: CommunityPluginWire }) {
  const [failed, setFailed] = useState(false);
  const raw = (plugin.logo_color ?? "").trim();
  const tile = /^[0-9a-fA-F]{6}$/.test(raw) ? `#${raw}` : "#3f3f46";
  const src =
    plugin.logo_url ??
    (plugin.logo_slug
      ? `https://cdn.simpleicons.org/${plugin.logo_slug}/F4F4F5`
      : undefined);
  const name = plugin.display_name ?? plugin.name;
  return (
    <div
      className="grid h-10 w-10 shrink-0 place-items-center overflow-hidden rounded-lg border border-border/60"
      style={{ backgroundColor: tile }}
    >
      {failed || !src ? (
        <span className="text-sm font-semibold text-white/90">
          {name.slice(0, 1).toUpperCase()}
        </span>
      ) : (
        <img
          src={src}
          alt=""
          className="h-5 w-5"
          loading="lazy"
          onError={() => setFailed(true)}
        />
      )}
    </div>
  );
}

function CommunityPluginRow({
  plugin,
  onInstall,
  onUninstall,
  uninstalling,
}: {
  plugin: CommunityPluginWire;
  onInstall: () => void;
  onUninstall: () => void;
  uninstalling: boolean;
}) {
  const name = plugin.display_name ?? plugin.name;
  if (!plugin.valid) {
    return (
      <article className="flex items-center gap-3 rounded-lg border border-border bg-card/20 px-3 py-2.5 opacity-70">
        <div className="grid h-10 w-10 shrink-0 place-items-center rounded-lg border border-border/60 bg-muted">
          <AlertTriangle className="h-4 w-4 text-muted-foreground" />
        </div>
        <div className="min-w-0 flex-1">
          <h4 className="truncate text-sm font-semibold text-foreground">
            {plugin.name}
          </h4>
          <p
            className="truncate text-xs text-muted-foreground"
            title={plugin.error}
          >
            Not installable: {plugin.error}
          </p>
        </div>
      </article>
    );
  }
  return (
    <article
      className={cn(
        "group flex items-center gap-3 rounded-lg border bg-card/40 px-3 py-2.5 transition-[colors,box-shadow]",
        plugin.installed
          ? "border-primary/30"
          : "border-border hover:border-primary/40 hover:bg-card/70",
      )}
    >
      <CommunityTile plugin={plugin} />
      {/* The whole card body opens the entry — reading what a stranger
          published must not require intending to install it. */}
      <button
        type="button"
        onClick={onInstall}
        className="min-w-0 flex-1 text-left"
        title={`Read what ${name} contains`}
      >
        <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
          <h4 className="min-w-0 max-w-full truncate text-sm font-semibold tracking-tight text-foreground">
            {name}
          </h4>
          <span className="shrink-0 text-[9px] font-medium uppercase tracking-wider text-foreground/80">
            Community · not reviewed
          </span>
          {plugin.installed && (
            <span className="shrink-0 text-[9px] font-medium uppercase tracking-wider text-primary">
              · Installed
            </span>
          )}
        </div>
        <p className="truncate text-xs text-muted-foreground" title={plugin.description}>
          {plugin.description}
        </p>
        <p className="truncate text-[11px] text-muted-foreground/70">
          {plugin.publisher ? `by ${plugin.publisher}` : "unknown publisher"}
          {plugin.version ? ` · v${plugin.version}` : ""}
        </p>
      </button>
      {plugin.source_url && (
        <button
          type="button"
          onClick={() => openExternalUrl(plugin.source_url ?? "")}
          className="grid h-7 w-7 shrink-0 place-items-center rounded-full text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          title="View the published source"
          aria-label={`View source of ${name}`}
        >
          <ExternalLink className="h-3.5 w-3.5" />
        </button>
      )}
      {plugin.installed ? (
        <Button
          size="sm"
          variant="ghost"
          onClick={onUninstall}
          disabled={uninstalling}
          title="Remove this plugin and its stored access"
        >
          {uninstalling ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Trash2 className="h-3.5 w-3.5" />
          )}
        </Button>
      ) : plugin.seed_conflict ? (
        <span
          className="shrink-0 text-[10px] text-muted-foreground"
          title="A built-in plugin already uses this name"
        >
          Name taken
        </span>
      ) : (
        <Button size="sm" variant="outline" onClick={onInstall}>
          Install
        </Button>
      )}
    </article>
  );
}

function CommunitySkillRow({
  skill,
  onInstall,
  installing,
  installError,
}: {
  skill: CommunitySkillWire;
  onInstall: () => void;
  installing: boolean;
  installError: string | null;
}) {
  return (
    <article
      className={cn(
        "flex items-center gap-3 rounded-lg border bg-card/40 px-3 py-2.5",
        skill.installed
          ? "border-primary/30"
          : "border-border hover:border-primary/40 hover:bg-card/70",
      )}
    >
      {/* Same as the plugin card: the body opens the skill so its instructions
          can be read without committing to an install. */}
      <button
        type="button"
        onClick={onInstall}
        className="min-w-0 flex-1 text-left"
        title={`Read what ${skill.title} tells the assistant`}
      >
        <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
          <h4 className="min-w-0 max-w-full truncate text-sm font-semibold tracking-tight text-foreground">
            {skill.title}
          </h4>
          <span className="shrink-0 text-[9px] font-medium uppercase tracking-wider text-foreground/80">
            Community · not reviewed
          </span>
          {skill.flavor === "portable" && (
            <span
              className="shrink-0 rounded-full border border-border px-1.5 text-[9px] font-medium uppercase tracking-wider text-muted-foreground"
              title={portableNote(skill) ?? undefined}
            >
              Portable
            </span>
          )}
        </div>
        <p className="truncate text-xs text-muted-foreground" title={skill.description}>
          {skill.description}
        </p>
        <p className="truncate text-[11px] text-muted-foreground/70">
          {skill.publisher ? `by ${skill.publisher}` : "unknown publisher"}
          {installError ? ` · ${installError}` : ""}
        </p>
      </button>
      {skill.source_url && (
        <button
          type="button"
          onClick={() => openExternalUrl(skill.source_url ?? "")}
          className="grid h-7 w-7 shrink-0 place-items-center rounded-full text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          title="View the published source"
          aria-label={`View source of ${skill.title}`}
        >
          <ExternalLink className="h-3.5 w-3.5" />
        </button>
      )}
      {skill.installed ? (
        <span className="inline-flex shrink-0 items-center gap-1 text-[10px] font-medium uppercase tracking-wider text-primary">
          <Check className="h-3 w-3" /> Installed
        </span>
      ) : skill.raw_url ? (
        <Button size="sm" variant="outline" onClick={onInstall} disabled={installing}>
          {installing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : "Install"}
        </Button>
      ) : (
        <span
          className="shrink-0 text-[10px] text-muted-foreground"
          title="No direct download — open the source and follow its steps"
        >
          Manual
        </span>
      )}
    </article>
  );
}

/** A published wallpaper, shown as the thing it is: a picture.
 *
 *  A tile rather than a row — a filename tells nobody whether they want the
 *  picture, and the whole card opens the full-size preview. */
function CommunityWallpaperCard({
  paper,
  onOpen,
}: {
  paper: CommunityWallpaperWire;
  onOpen: () => void;
}) {
  const [failed, setFailed] = useState(false);
  return (
    <button
      type="button"
      onClick={onOpen}
      title={`Preview ${paper.title}`}
      className={cn(
        "group overflow-hidden rounded-lg border bg-card/40 text-left transition-colors",
        paper.installed
          ? "border-primary/30"
          : "border-border hover:border-primary/40 hover:bg-card/70",
      )}
    >
      <div className="grid aspect-video place-items-center overflow-hidden bg-muted">
        {failed || !paper.raw_url ? (
          <span className="text-[11px] text-muted-foreground">No preview</span>
        ) : (
          <img
            src={paper.raw_url}
            alt=""
            loading="lazy"
            className="h-full w-full object-cover transition-transform group-hover:scale-[1.03]"
            onError={() => setFailed(true)}
          />
        )}
      </div>
      <div className="px-2.5 py-2">
        <div className="flex items-center gap-1.5">
          <h4 className="min-w-0 truncate text-xs font-semibold text-foreground">
            {paper.title}
          </h4>
          {paper.installed && (
            <Check className="h-3 w-3 shrink-0 text-primary" aria-label="Installed" />
          )}
        </div>
        <p className="truncate text-[11px] text-muted-foreground/70">
          {paper.publisher ? `by ${paper.publisher}` : "unknown publisher"}
        </p>
      </div>
    </button>
  );
}

/** The picture at full size before it lands in the picker.
 *
 *  A wallpaper carries no code and no credentials, so there is nothing to
 *  disclose beyond the image itself — which makes seeing it big the entire
 *  decision. */
export function WallpaperPreviewDialog({
  paper,
  isPending,
  errorMessage,
  onCancel,
  onConfirm,
}: {
  paper: CommunityWallpaperWire;
  isPending: boolean;
  errorMessage: string | null;
  onCancel: () => void;
  onConfirm: () => void;
}) {
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
      aria-labelledby="community-wallpaper-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-scrim/70 backdrop-blur-sm"
      onClick={(e) => {
        if (e.target === e.currentTarget && !isPending) onCancel();
      }}
    >
      <div className="relative flex max-h-[88vh] w-full max-w-3xl flex-col overflow-hidden rounded-2xl border border-border bg-card shadow-[0_20px_60px_rgba(0,0,0,0.6)]">
        <header className="border-b border-border px-5 py-4">
          <h2
            id="community-wallpaper-title"
            className="font-display text-base font-semibold tracking-tight"
          >
            {paper.installed ? paper.title : `Install ${paper.title}?`}
          </h2>
          <p className="text-[11px] uppercase tracking-wider text-foreground/80">
            Community wallpaper · not reviewed
            {paper.installed ? " · installed" : ""}
          </p>
        </header>

        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-5 py-4 text-sm">
          {paper.raw_url ? (
            <img
              src={paper.raw_url}
              alt={paper.title}
              className="max-h-[52vh] w-full rounded-lg border border-border object-contain"
            />
          ) : (
            <Notice tone="warn">This wallpaper publishes no downloadable image.</Notice>
          )}
          {paper.description && (
            <p className="text-muted-foreground">{paper.description}</p>
          )}
          <p className="text-xs text-muted-foreground">
            Published by{" "}
            <span className="text-foreground">
              {paper.publisher ?? "an unknown author"}
            </span>
            {paper.version ? ` · version ${paper.version}` : ""}
          </p>
          <p className="text-xs text-muted-foreground">
            Installing downloads the picture and stores it beside your own
            uploads. It carries no code and no credentials.
          </p>
          {errorMessage && (
            <p className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/5 px-2 py-1.5 text-xs text-destructive">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              {errorMessage}
            </p>
          )}
        </div>

        <footer className="flex shrink-0 justify-end gap-2 border-t border-border px-5 py-3">
          <Button size="sm" variant="ghost" onClick={onCancel} disabled={isPending}>
            {paper.installed ? "Close" : "Cancel"}
          </Button>
          {!paper.installed && paper.raw_url && (
            <Button size="sm" onClick={onConfirm} disabled={isPending}>
              {isPending ? (
                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
              ) : null}
              Install
            </Button>
          )}
        </footer>
      </div>
    </div>
  );
}

/** Skills get the same trust boundary as plugins: a community skill is
 *  instructions the assistant will follow, downloaded from a URL nobody
 *  reviewed — so the dialog shows that exact URL and reminds the user to
 *  read the instructions before switching the skill on. */
export function SkillInstallConsentDialog({
  skill,
  isPending,
  errorMessage,
  onCancel,
  onConfirm,
}: {
  skill: CommunitySkillWire;
  isPending: boolean;
  errorMessage: string | null;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !isPending) onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel, isPending]);

  // The published URLs travel into the command builder: a skill hosted on
  // GitHub also has an `npx skills add` line, which installs it into every
  // other agent that reads SKILL.md files.
  const skillInstall = installBlock(skill.name, "skill", {
    sourceUrl: skill.source_url,
    rawUrl: skill.raw_url,
  });

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="community-skill-install-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-scrim/70 backdrop-blur-sm"
      onClick={(e) => {
        if (e.target === e.currentTarget && !isPending) onCancel();
      }}
    >
      <div className="relative flex max-h-[88vh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl border border-border bg-card shadow-[0_20px_60px_rgba(0,0,0,0.6)]">
        <header className="border-b border-border px-5 py-4">
          <h2
            id="community-skill-install-title"
            className="font-display text-base font-semibold tracking-tight"
          >
            {skill.installed ? skill.title : `Install ${skill.title}?`}
          </h2>
          <p className="text-[11px] uppercase tracking-wider text-foreground/80">
            Community skill · not reviewed
            {skill.installed ? " · installed" : ""}
          </p>
        </header>

        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-5 py-4 text-sm">
          <p className="text-muted-foreground">{skill.description}</p>
          <p className="text-xs text-muted-foreground">
            Published by{" "}
            <span className="text-foreground">
              {skill.publisher ?? "an unknown author"}
            </span>
            {skill.version ? ` · version ${skill.version}` : ""}
          </p>
          {/* A portable skill was written for the open format, not for this
              app. Saying so up front explains both the extra install command
              below and why settings meant for another agent are ignored. */}
          {portableNote(skill) && (
            <p className="text-xs text-muted-foreground">
              {portableNote(skill)}. {PRODUCT_NAME} follows its instructions and
              ignores the settings meant for other agents.
            </p>
          )}
          {/* The instructions themselves, before the fine print: a skill IS
              its text, so reading it is the decision this dialog asks for. */}
          <ContentsPanel name={skill.name} />
          <div>
            <p className="mb-1 text-xs font-medium text-foreground">
              The instructions are downloaded from:
            </p>
            <code className="block break-all rounded-md border border-border bg-muted/40 px-2 py-1.5 text-xs text-foreground">
              {skill.raw_url}
            </code>
          </div>
          {/* The same line the storefront shows, and the same one the Install
              button below runs — worth having when the install belongs in a
              setup script or in a message to someone else. */}
          {skillInstall && (
            <InstallTerminal
              commands={skillInstall.commands}
              path={`~/marketplace/${skill.name}`}
              comment="# The same install, from a terminal."
            />
          )}
          <p className="text-xs text-muted-foreground">
            A skill is a set of instructions the assistant follows — the text
            above is exactly what it would be told to do.
          </p>
          {errorMessage && (
            <p className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/5 px-2 py-1.5 text-xs text-destructive">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              {errorMessage}
            </p>
          )}
        </div>

        <footer className="flex shrink-0 justify-end gap-2 border-t border-border px-5 py-3">
          <Button size="sm" variant="ghost" onClick={onCancel} disabled={isPending}>
            {skill.installed ? "Close" : "Cancel"}
          </Button>
          {!skill.installed && (
            <Button size="sm" onClick={onConfirm} disabled={isPending}>
              {isPending ? (
                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
              ) : null}
              Install
            </Button>
          )}
        </footer>
      </div>
    </div>
  );
}

/** The trust boundary. Nothing was human-reviewed, so BEFORE the install this
 *  dialog states verbatim where requests (and later the token) would go, or
 *  which command would run locally — the two fields a malicious submission
 *  would have to use. */
export function InstallConsentDialog({
  plugin,
  isPending,
  errorMessage,
  onCancel,
  onConfirm,
}: {
  plugin: CommunityPluginWire;
  isPending: boolean;
  errorMessage: string | null;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !isPending) onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel, isPending]);

  const name = plugin.display_name ?? plugin.name;
  const mcp = plugin.mcp_server ?? null;
  const authLabel = plugin.auth ? AUTH_MODE_LABEL[plugin.auth.mode] : undefined;
  const pluginInstall = installBlock(plugin.name, "plugin");

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="community-install-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-scrim/70 backdrop-blur-sm"
      onClick={(e) => {
        if (e.target === e.currentTarget && !isPending) onCancel();
      }}
    >
      <div className="relative flex max-h-[88vh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl border border-border bg-card shadow-[0_20px_60px_rgba(0,0,0,0.6)]">
        <header className="flex items-center gap-3 border-b border-border px-5 py-4">
          <CommunityTile plugin={plugin} />
          <div className="min-w-0">
            <h2
              id="community-install-title"
              className="font-display text-base font-semibold tracking-tight"
            >
              {plugin.installed ? name : `Install ${name}?`}
            </h2>
            <p className="text-[11px] uppercase tracking-wider text-foreground/80">
              Community plugin · not reviewed
              {plugin.installed ? " · installed" : ""}
            </p>
          </div>
        </header>

        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-5 py-4 text-sm">
          <p className="text-muted-foreground">{plugin.description}</p>
          <p className="text-xs text-muted-foreground">
            Published by{" "}
            <span className="text-foreground">
              {plugin.publisher ?? "an unknown author"}
            </span>
            {plugin.version ? ` · version ${plugin.version}` : ""}
          </p>

          {mcp?.transport === "http" && mcp.url && (
            <div>
              <p className="mb-1 text-xs font-medium text-foreground">
                After you connect it, requests and your {name} access token go
                to:
              </p>
              <code className="block break-all rounded-md border border-border bg-muted/40 px-2 py-1.5 text-xs text-foreground">
                {mcp.url}
              </code>
            </div>
          )}
          {mcp?.transport === "stdio" && (
            <div>
              <p className="mb-1 text-xs font-medium text-foreground">
                After you connect it, this command runs on your computer:
              </p>
              <code className="block break-all rounded-md border border-border bg-muted/40 px-2 py-1.5 text-xs text-foreground">
                {(mcp.install ?? []).join(" ")}
              </code>
            </div>
          )}
          {!mcp && (
            <p className="text-xs text-muted-foreground">
              This entry is metadata only — it adds no server and runs no
              command.
            </p>
          )}
          {authLabel && (
            <p className="text-xs text-muted-foreground">
              Sign-in method: <span className="text-foreground">{authLabel}</span>
              {" "}— installing does not connect anything yet.
            </p>
          )}

          {/* The manifests in full. The address above is the summary; this is
              the document it was read from, so a claim can be checked rather
              than trusted. */}
          <ContentsPanel name={plugin.name} />

          {/* The same line the storefront shows, and the same one the Install
              button below runs — worth having when the install belongs in a
              setup script or in a message to someone else. */}
          {pluginInstall && (
            <InstallTerminal
              commands={pluginInstall.commands}
              path={`~/marketplace/${plugin.name}`}
              comment="# The same install, from a terminal."
            />
          )}

          {errorMessage && (
            <p className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/5 px-2 py-1.5 text-xs text-destructive">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              {errorMessage}
            </p>
          )}
        </div>

        <footer className="flex shrink-0 justify-end gap-2 border-t border-border px-5 py-3">
          <Button size="sm" variant="ghost" onClick={onCancel} disabled={isPending}>
            {plugin.installed ? "Close" : "Cancel"}
          </Button>
          {!plugin.installed && (
            <Button size="sm" onClick={onConfirm} disabled={isPending}>
              {isPending ? (
                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
              ) : null}
              Install
            </Button>
          )}
        </footer>
      </div>
    </div>
  );
}
