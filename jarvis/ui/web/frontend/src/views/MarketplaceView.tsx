import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowRight,
  Check,
  ExternalLink,
  FileText,
  Loader2,
  Package,
  RefreshCw,
  Search,
  Sparkles,
  Store,
  Wand2,
  X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { ViewHeader } from "@/views/ChatsView";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { openExternalUrl } from "@/lib/openExternal";
import { useEventStore, type SectionId } from "@/store/events";
import {
  MARKETPLACE_SUBMIT_URL,
  type CommunityPluginWire,
  type CommunityResponse,
  type CommunitySkillWire,
  type CommunityWallpaperWire,
  type EntryContentsWire,
} from "@/views/PluginsCommunity";

// ---------------------------------------------------------------------------
// The Jarvis Marketplace, as a section of its own.
//
// Everything published by the community used to be reachable only by knowing
// where to look: plugins behind the "Community" tab of Skills & Tools, skills
// in a second list, wallpapers in a third screen entirely. Somebody who never
// opened those three places never learned the marketplace exists.
//
// This screen is the storefront: one index, one search across all three kinds,
// and — the part that matters — a landing that says where the thing WENT, with
// a jump into the section that now holds it. Installing is not the end of the
// errand; using the thing is.
//
// Everything here is UNREVIEWED third-party content. The registry auto-merges
// submissions that pass automated checks, so every install goes through the
// detail drawer, where the publisher, the source and the actual published
// files are on screen before the button is reachable.
// ---------------------------------------------------------------------------

/** The public storefront — the same catalogue, on the web. */
const MARKETPLACE_WEB_URL = "https://personaljarvis.ai/marketplace/";

type Kind = "plugin" | "skill" | "wallpaper";
type KindFilter = "all" | Kind;

/** One entry of any kind, flattened into what the storefront draws. */
interface Entry {
  kind: Kind;
  /** The registry name — the id every API route takes. */
  name: string;
  title: string;
  description: string;
  publisher?: string | null;
  version?: string | null;
  categories: string[];
  sourceUrl?: string | null;
  installed: boolean;
  /** Wallpapers only — the published preview image. */
  thumbUrl?: string | null;
  /** Plugins only — the brand tile. */
  logoUrl?: string | null;
  logoColor?: string | null;
  /** A plugin whose manifest this client could not read. */
  broken?: boolean;
  problem?: string | null;
  /**
   * Where installing this would eventually send data, or what it would run.
   *
   * The whole reason the drawer exists: the registry auto-merges submissions
   * that pass automated checks, so the only honest consent is showing the
   * destination verbatim before the button is pressed — the same three cases
   * the plugin consent dialog spells out (hosted URL / stdio argv / neither).
   */
  mcp?: { transport?: string; url?: string; install?: string[] } | null;
  /** How connecting works, once installed. Plugins only. */
  authMode?: string | null;
  /** A plugin the shipped seed catalog already carries under this name. */
  seedConflict?: boolean;
  /** Skills only: a portable Agent Skill states the agents it also runs in. */
  portableAgents?: string[] | null;
  /** Wallpapers only. */
  license?: string | null;
}

/** Where an installed entry of this kind now lives in the app. */
const HOME_SECTION: Record<Kind, SectionId> = {
  plugin: "plugins",
  skill: "skills",
  wallpaper: "wallpaper",
};

function pluginEntry(p: CommunityPluginWire): Entry {
  const raw = (p.logo_color ?? "").trim();
  return {
    kind: "plugin",
    name: p.id ?? p.name,
    title: p.display_name ?? p.name,
    description: p.description ?? "",
    publisher: p.publisher,
    version: p.version,
    categories: p.category ? [p.category] : [],
    sourceUrl: p.source_url,
    installed: Boolean(p.installed),
    logoUrl:
      p.logo_url ??
      (p.logo_slug ? `https://cdn.simpleicons.org/${p.logo_slug}/F4F4F5` : null),
    logoColor: /^[0-9a-fA-F]{6}$/.test(raw) ? `#${raw}` : null,
    broken: !p.valid,
    problem: p.error ?? null,
    mcp: p.mcp_server ?? null,
    authMode: p.auth?.mode ?? null,
    seedConflict: Boolean(p.seed_conflict),
  };
}

function skillEntry(s: CommunitySkillWire): Entry {
  return {
    kind: "skill",
    name: s.name,
    title: s.title || s.name,
    description: s.description ?? "",
    publisher: s.publisher,
    version: s.version,
    categories: s.categories ?? [],
    sourceUrl: s.source_url,
    installed: Boolean(s.installed),
    portableAgents: s.flavor === "portable" ? (s.compatible_agents ?? []) : null,
  };
}

function wallpaperEntry(w: CommunityWallpaperWire): Entry {
  return {
    kind: "wallpaper",
    name: w.name,
    title: w.title || w.name,
    description: w.description ?? "",
    publisher: w.publisher,
    version: w.version,
    categories: w.categories ?? [],
    sourceUrl: w.source_url,
    installed: Boolean(w.installed),
    thumbUrl: w.thumb_url ?? w.raw_url ?? null,
    license: w.license ?? null,
  };
}

function matches(entry: Entry, needle: string): boolean {
  if (!needle) return true;
  const hay = [
    entry.title,
    entry.name,
    entry.description,
    entry.publisher ?? "",
    entry.categories.join(" "),
  ]
    .join(" ")
    .toLowerCase();
  return hay.includes(needle);
}

async function fetchCommunity(): Promise<CommunityResponse> {
  const res = await fetch("/api/marketplace/community", { cache: "no-store" });
  if (!res.ok) throw new Error(`Marketplace request failed (${res.status})`);
  return res.json();
}

/** What `POST /community/install/{name}` answers — the honest landing report. */
interface InstallResultWire {
  ok: boolean;
  kind: Kind;
  id?: string;
  title?: string;
  location?: string;
  state?: string;
  ready?: boolean;
  problem?: string | null;
  next_action?: string | null;
}

export function MarketplaceView() {
  const t = useT();
  const queryClient = useQueryClient();
  const [query, setQuery] = useState("");
  const [kindFilter, setKindFilter] = useState<KindFilter>("all");
  const [openEntry, setOpenEntry] = useState<Entry | null>(null);
  const [landing, setLanding] = useState<InstallResultWire | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["marketplace-community"],
    queryFn: fetchCommunity,
  });

  const refresh = useMutation({
    mutationFn: async (): Promise<CommunityResponse> => {
      const res = await fetch("/api/marketplace/community/refresh", { method: "POST" });
      if (!res.ok) throw new Error(`Refresh failed (${res.status})`);
      return res.json();
    },
    onSuccess: (fresh) => queryClient.setQueryData(["marketplace-community"], fresh),
  });

  const install = useMutation({
    mutationFn: async (entry: Entry): Promise<InstallResultWire> => {
      const res = await fetch(
        `/api/marketplace/community/install/${encodeURIComponent(entry.name)}`,
        { method: "POST" },
      );
      if (!res.ok) {
        const detail = await res
          .json()
          .then((body: { detail?: string }) => body.detail)
          .catch(() => undefined);
        // A failure names its cause — a bare "that did not work" is a bug.
        throw new Error(detail ?? `Install failed (${res.status})`);
      }
      return res.json();
    },
    onSuccess: (result) => {
      setOpenEntry(null);
      setLanding(result);
      // Everything that lists installed things must reflect the new arrival.
      queryClient.invalidateQueries({ queryKey: ["marketplace-community"] });
      queryClient.invalidateQueries({ queryKey: ["marketplace-plugins"] });
      queryClient.invalidateQueries({ queryKey: ["skills"] });
      queryClient.invalidateQueries({ queryKey: ["wallpaper-catalog"] });
    },
  });

  const entries = useMemo<Entry[]>(() => {
    if (!data) return [];
    return [
      ...(data.plugins ?? []).map(pluginEntry),
      ...(data.skills ?? []).map(skillEntry),
      ...(data.wallpapers ?? []).map(wallpaperEntry),
    ];
  }, [data]);

  const needle = query.trim().toLowerCase();
  const visible = useMemo(
    () =>
      entries.filter(
        (e) => (kindFilter === "all" || e.kind === kindFilter) && matches(e, needle),
      ),
    [entries, kindFilter, needle],
  );

  const wallpapers = visible.filter((e) => e.kind === "wallpaper");
  const plugins = visible.filter((e) => e.kind === "plugin");
  const skills = visible.filter((e) => e.kind === "skill");

  const status = data?.status;
  const offline = status === "stale" || status === "unavailable";

  return (
    <div className="relative flex h-full min-h-0 flex-col">
      <ViewHeader
        icon={<Store className="h-4 w-4 text-muted-foreground" />}
        title={t("marketplace.title")}
        subtitle={subtitleFor(t, data, isLoading)}
        right={
          <div className="flex items-center gap-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => refresh.mutate()}
              disabled={refresh.isPending}
              title={t("marketplace.refresh")}
            >
              <RefreshCw
                className={cn("h-4 w-4", refresh.isPending && "animate-spin")}
              />
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => openExternalUrl(MARKETPLACE_WEB_URL)}
            >
              {t("marketplace.open_web")}
              <ExternalLink className="ml-1.5 h-3.5 w-3.5" />
            </Button>
          </div>
        }
      />

      <div className="flex items-center gap-3 border-b border-border bg-background/60 px-6 py-3 backdrop-blur-sm">
        <div className="relative min-w-0 flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t("marketplace.search_placeholder")}
            aria-label={t("marketplace.search_placeholder")}
            className={cn(
              "h-9 w-full rounded-md border border-border bg-background pl-9 pr-3",
              "text-sm text-foreground placeholder:text-muted-foreground",
              "outline-none transition-colors focus:border-primary/60",
            )}
          />
        </div>
        <FilterChips
          active={kindFilter}
          onChange={setKindFilter}
          counts={{
            all: entries.length,
            plugin: entries.filter((e) => e.kind === "plugin").length,
            skill: entries.filter((e) => e.kind === "skill").length,
            wallpaper: entries.filter((e) => e.kind === "wallpaper").length,
          }}
          t={t}
        />
      </div>

      {offline && (
        <p className="flex items-center gap-2 border-b border-border bg-amber-500/10 px-6 py-2 text-xs text-amber-700 dark:text-amber-300">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
          {status === "unavailable"
            ? t("marketplace.status_unavailable")
            : t("marketplace.status_stale")}
        </p>
      )}

      <ScrollArea className="min-h-0 flex-1">
        <div className="px-6 py-5">
          {isLoading && (
            <p className="flex items-center gap-2 py-10 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              {t("marketplace.loading")}
            </p>
          )}

          {error && !isLoading && (
            <p className="flex items-center gap-2 py-10 text-sm text-destructive">
              <AlertTriangle className="h-4 w-4" />
              {(error as Error).message}
            </p>
          )}

          {!isLoading && !error && visible.length === 0 && (
            <EmptyState
              query={query}
              onClear={() => {
                setQuery("");
                setKindFilter("all");
              }}
              t={t}
            />
          )}

          {wallpapers.length > 0 && (
            <Shelf
              title={t("marketplace.shelf_wallpapers")}
              hint={t("marketplace.shelf_wallpapers_hint")}
              count={wallpapers.length}
            >
              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                {wallpapers.map((entry) => (
                  <WallpaperTile
                    key={entry.name}
                    entry={entry}
                    onOpen={() => setOpenEntry(entry)}
                    t={t}
                  />
                ))}
              </div>
            </Shelf>
          )}

          {plugins.length > 0 && (
            <Shelf
              title={t("marketplace.shelf_plugins")}
              hint={t("marketplace.shelf_plugins_hint")}
              count={plugins.length}
            >
              <EntryList
                entries={plugins}
                onOpen={setOpenEntry}
                icon={(entry) => <BrandTile entry={entry} />}
                t={t}
              />
            </Shelf>
          )}

          {skills.length > 0 && (
            <Shelf
              title={t("marketplace.shelf_skills")}
              hint={t("marketplace.shelf_skills_hint")}
              count={skills.length}
            >
              <EntryList
                entries={skills}
                onOpen={setOpenEntry}
                icon={() => (
                  <div className="grid h-10 w-10 shrink-0 place-items-center rounded-lg border border-border/60 bg-secondary/50">
                    <Wand2 className="h-4 w-4 text-muted-foreground" />
                  </div>
                )}
                t={t}
              />
            </Shelf>
          )}

          {!isLoading && !error && (
            <PublishFooter t={t} />
          )}
        </div>
      </ScrollArea>

      {openEntry && (
        <EntryDrawer
          entry={openEntry}
          onClose={() => setOpenEntry(null)}
          onInstall={() => install.mutate(openEntry)}
          installing={install.isPending}
          installError={install.error ? (install.error as Error).message : null}
          t={t}
        />
      )}

      {landing && <LandingToast result={landing} onClose={() => setLanding(null)} t={t} />}
    </div>
  );
}

type Translate = (key: string) => string;

/**
 * Fill `{token}` placeholders in a translated string.
 *
 * The in-house i18n resolver interpolates exactly one token — the assistant's
 * name — and takes no variables, so counts and titles are substituted here
 * rather than by widening a resolver three locales and every view depend on.
 */
function fill(template: string, vars: Record<string, string | number>): string {
  return template.replace(/\{(\w+)\}/g, (match, key: string) =>
    key in vars ? String(vars[key]) : match,
  );
}

/** "12 entries · index 24 · updated 4 days ago", or the honest alternative. */
function subtitleFor(
  t: Translate,
  data: CommunityResponse | undefined,
  loading: boolean,
): string {
  if (loading) return t("marketplace.loading");
  if (!data) return "";
  if (data.status === "disabled") return t("marketplace.status_disabled");
  const total =
    (data.plugins?.length ?? 0) + (data.skills?.length ?? 0) + (data.wallpapers?.length ?? 0);
  const parts = [fill(t("marketplace.subtitle_count"), { count: total })];
  if (data.revision != null) {
    parts.push(fill(t("marketplace.subtitle_revision"), { revision: data.revision }));
  }
  if (data.generated_at) parts.push(formatDate(data.generated_at));
  return parts.join(" · ");
}

function formatDate(iso: string): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return iso;
  return parsed.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function FilterChips({
  active,
  onChange,
  counts,
  t,
}: {
  active: KindFilter;
  onChange: (kind: KindFilter) => void;
  counts: Record<KindFilter, number>;
  t: Translate;
}) {
  const chips: { id: KindFilter; label: string }[] = [
    { id: "all", label: t("marketplace.filter_all") },
    { id: "plugin", label: t("marketplace.filter_plugins") },
    { id: "skill", label: t("marketplace.filter_skills") },
    { id: "wallpaper", label: t("marketplace.filter_wallpapers") },
  ];
  return (
    <div className="flex shrink-0 items-center gap-1">
      {chips.map((chip) => (
        <button
          key={chip.id}
          type="button"
          onClick={() => onChange(chip.id)}
          aria-pressed={active === chip.id}
          className={cn(
            "rounded-md px-2.5 py-1.5 text-xs font-medium transition-colors",
            active === chip.id
              ? "bg-secondary text-foreground"
              : "text-muted-foreground hover:bg-secondary/50 hover:text-foreground",
          )}
        >
          {chip.label}
          <span className="ml-1.5 tabular-nums opacity-60">{counts[chip.id]}</span>
        </button>
      ))}
    </div>
  );
}

/** A titled band of the storefront. Not a card — a heading and its row. */
function Shelf({
  title,
  hint,
  count,
  children,
}: {
  title: string;
  hint: string;
  count: number;
  children: React.ReactNode;
}) {
  return (
    <section className="mb-8 last:mb-0">
      <div className="mb-3 flex items-baseline gap-3">
        <h3 className="font-display text-sm font-semibold tracking-tight text-foreground">
          {title}
        </h3>
        <span className="text-xs tabular-nums text-muted-foreground">{count}</span>
        <span className="min-w-0 truncate text-xs font-medium text-foreground/75">{hint}</span>
      </div>
      {children}
    </section>
  );
}

function WallpaperTile({
  entry,
  onOpen,
  t,
}: {
  entry: Entry;
  onOpen: () => void;
  t: Translate;
}) {
  return (
    <button
      type="button"
      onClick={onOpen}
      className={cn(
        "group relative block aspect-[16/10] w-full overflow-hidden rounded-xl",
        "border border-border bg-secondary/40 text-left",
        "transition-colors hover:border-primary/50",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60",
      )}
    >
      {entry.thumbUrl ? (
        <img
          src={entry.thumbUrl}
          alt=""
          loading="lazy"
          className="h-full w-full object-cover transition-transform duration-500 group-hover:scale-[1.03]"
        />
      ) : (
        <div className="grid h-full w-full place-items-center">
          <Sparkles className="h-5 w-5 text-muted-foreground" />
        </div>
      )}
      <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/75 via-black/35 to-transparent p-3">
        <p className="truncate text-sm font-medium text-white">{entry.title}</p>
        {entry.publisher && (
          <p className="truncate text-[11px] text-white/70">{entry.publisher}</p>
        )}
      </div>
      {entry.installed && (
        <span className="absolute right-2 top-2 flex items-center gap-1 rounded-full bg-black/60 px-2 py-0.5 text-[10px] font-medium text-white backdrop-blur">
          <Check className="h-3 w-3" />
          {t("marketplace.installed")}
        </span>
      )}
    </button>
  );
}

function BrandTile({ entry }: { entry: Entry }) {
  const [failed, setFailed] = useState(false);
  return (
    <div
      className="grid h-10 w-10 shrink-0 place-items-center overflow-hidden rounded-lg border border-border/60"
      style={{ backgroundColor: entry.logoColor ?? undefined }}
    >
      {failed || !entry.logoUrl ? (
        <span
          className={cn(
            "text-sm font-semibold",
            entry.logoColor ? "text-white/90" : "text-muted-foreground",
          )}
        >
          {entry.title.slice(0, 1).toUpperCase()}
        </span>
      ) : (
        <img
          src={entry.logoUrl}
          alt=""
          loading="lazy"
          className="h-5 w-5"
          onError={() => setFailed(true)}
        />
      )}
    </div>
  );
}

/** Rows with a hairline between them — a list, deliberately not a grid of boxes. */
function EntryList({
  entries,
  onOpen,
  icon,
  t,
}: {
  entries: Entry[];
  onOpen: (entry: Entry) => void;
  icon: (entry: Entry) => React.ReactNode;
  t: Translate;
}) {
  return (
    <div className="divide-y divide-border/70 overflow-hidden rounded-xl border border-border bg-card/60 backdrop-blur-sm">
      {entries.map((entry) => (
        <button
          key={`${entry.kind}:${entry.name}`}
          type="button"
          onClick={() => onOpen(entry)}
          className={cn(
            "flex w-full items-center gap-3 px-3.5 py-3 text-left transition-colors",
            "hover:bg-secondary/60 focus-visible:outline-none focus-visible:bg-secondary/60",
          )}
        >
          {entry.broken ? (
            <div className="grid h-10 w-10 shrink-0 place-items-center rounded-lg border border-border/60 bg-muted">
              <AlertTriangle className="h-4 w-4 text-muted-foreground" />
            </div>
          ) : (
            icon(entry)
          )}
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <span className="truncate text-sm font-semibold text-foreground">
                {entry.title}
              </span>
              {entry.installed && (
                <span className="flex shrink-0 items-center gap-1 rounded-full bg-primary/10 px-1.5 py-0.5 text-[10px] font-medium text-primary">
                  <Check className="h-3 w-3" />
                  {t("marketplace.installed")}
                </span>
              )}
            </div>
            <p className="truncate text-xs text-muted-foreground">
              {entry.broken
                ? (entry.problem ?? t("marketplace.entry_unreadable"))
                : entry.description}
            </p>
          </div>
          <div className="hidden shrink-0 items-center gap-3 text-[11px] text-muted-foreground sm:flex">
            {entry.publisher && <span className="truncate">{entry.publisher}</span>}
            {entry.version && <span className="tabular-nums">v{entry.version}</span>}
          </div>
          <ArrowRight className="h-4 w-4 shrink-0 text-muted-foreground/50" />
        </button>
      ))}
    </div>
  );
}

function EmptyState({
  query,
  onClear,
  t,
}: {
  query: string;
  onClear: () => void;
  t: Translate;
}) {
  return (
    <div className="py-16 text-center">
      <Package className="mx-auto mb-3 h-6 w-6 text-muted-foreground/60" />
      <p className="text-sm text-muted-foreground">
        {query ? fill(t("marketplace.empty_search"), { query }) : t("marketplace.empty")}
      </p>
      {query && (
        <Button variant="ghost" size="sm" className="mt-3" onClick={onClear}>
          {t("marketplace.empty_clear")}
        </Button>
      )}
    </div>
  );
}

function PublishFooter({ t }: { t: Translate }) {
  return (
    <div className="mt-10 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-dashed border-border bg-card/50 px-4 py-3 backdrop-blur-sm">
      <p className="text-xs text-muted-foreground">{t("marketplace.publish_hint")}</p>
      <Button
        variant="outline"
        size="sm"
        onClick={() => openExternalUrl(MARKETPLACE_SUBMIT_URL)}
      >
        {t("marketplace.publish_cta")}
        <ExternalLink className="ml-1.5 h-3.5 w-3.5" />
      </Button>
    </div>
  );
}

/** The published bytes of one entry, fetched only when its drawer opens. */
function useEntryContents(name: string | null) {
  return useQuery({
    queryKey: ["marketplace-community-contents", name],
    enabled: name !== null,
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

/**
 * The trust boundary. Nothing installs from a list row — the publisher, the
 * source repo and the actual published files are on screen first, because this
 * is unreviewed third-party content and the person clicking deserves to see
 * what they are about to run.
 */
function EntryDrawer({
  entry,
  onClose,
  onInstall,
  installing,
  installError,
  t,
}: {
  entry: Entry;
  onClose: () => void;
  onInstall: () => void;
  installing: boolean;
  installError: string | null;
  t: Translate;
}) {
  const contents = useEntryContents(entry.name);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="absolute inset-0 z-40 flex justify-end">
      <button
        type="button"
        aria-label={t("marketplace.close")}
        onClick={onClose}
        className="absolute inset-0 bg-background/70 backdrop-blur-sm"
      />
      <aside className="relative flex h-full w-full max-w-md flex-col border-l border-border bg-card shadow-2xl">
        <header className="flex items-start gap-3 border-b border-border px-5 py-4">
          <div className="min-w-0 flex-1">
            <p className="text-[11px] uppercase tracking-wide text-muted-foreground">
              {t(`marketplace.kind_${entry.kind}`)}
            </p>
            <h3 className="truncate font-display text-base font-semibold tracking-tight">
              {entry.title}
            </h3>
            <p className="truncate text-xs text-muted-foreground">
              {[entry.publisher, entry.version ? `v${entry.version}` : null]
                .filter(Boolean)
                .join(" · ")}
            </p>
          </div>
          <Button variant="ghost" size="sm" onClick={onClose} aria-label={t("marketplace.close")}>
            <X className="h-4 w-4" />
          </Button>
        </header>

        <ScrollArea className="min-h-0 flex-1">
          <div className="space-y-5 px-5 py-4">
            {entry.thumbUrl && (
              <img
                src={entry.thumbUrl}
                alt=""
                className="w-full rounded-lg border border-border object-cover"
              />
            )}

            {entry.description && (
              <p className="text-sm leading-relaxed text-foreground/90">
                {entry.description}
              </p>
            )}

            {entry.categories.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {entry.categories.map((category) => (
                  <span
                    key={category}
                    className="rounded-full border border-border px-2 py-0.5 text-[11px] text-muted-foreground"
                  >
                    {category}
                  </span>
                ))}
              </div>
            )}

            <p className="rounded-lg border border-border bg-secondary/30 px-3 py-2 text-xs leading-relaxed text-muted-foreground">
              {t("marketplace.unreviewed_note")}
            </p>

            <Destination entry={entry} t={t} />

            {entry.sourceUrl && (
              <Button
                variant="outline"
                size="sm"
                className="w-full"
                onClick={() => openExternalUrl(entry.sourceUrl as string)}
              >
                {t("marketplace.view_source")}
                <ExternalLink className="ml-1.5 h-3.5 w-3.5" />
              </Button>
            )}

            <div>
              <h4 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                <FileText className="h-3.5 w-3.5" />
                {t("marketplace.files")}
              </h4>
              {contents.isLoading && (
                <p className="flex items-center gap-2 text-xs text-muted-foreground">
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  {t("marketplace.files_loading")}
                </p>
              )}
              {contents.error && (
                <p className="text-xs text-destructive">
                  {(contents.error as Error).message}
                </p>
              )}
              {contents.data && (
                <div className="space-y-2">
                  {contents.data.files.length === 0 && (
                    <p className="text-xs text-muted-foreground">
                      {t("marketplace.files_none")}
                    </p>
                  )}
                  {contents.data.files.map((file) => (
                    <details
                      key={file.path}
                      className="rounded-lg border border-border bg-background/50"
                    >
                      <summary className="cursor-pointer select-none px-3 py-2 text-xs font-medium text-foreground">
                        {file.path}
                        <span className="ml-2 tabular-nums text-muted-foreground">
                          {file.size < 1024
                            ? `${file.size} B`
                            : `${(file.size / 1024).toFixed(1)} kB`}
                        </span>
                      </summary>
                      <pre className="max-h-64 overflow-auto border-t border-border px-3 py-2 text-[11px] leading-relaxed text-muted-foreground">
                        {file.text}
                        {file.truncated ? `\n${t("marketplace.files_truncated")}` : ""}
                      </pre>
                    </details>
                  ))}
                </div>
              )}
            </div>
          </div>
        </ScrollArea>

        <footer className="space-y-2 border-t border-border px-5 py-4">
          {installError && (
            <p className="flex items-start gap-2 text-xs text-destructive">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              {installError}
            </p>
          )}
          {entry.broken ? (
            <p className="text-xs text-muted-foreground">
              {entry.problem ?? t("marketplace.entry_unreadable")}
            </p>
          ) : entry.installed ? (
            <p className="flex items-center gap-2 text-xs text-muted-foreground">
              <Check className="h-3.5 w-3.5 text-primary" />
              {t("marketplace.already_installed")}
            </p>
          ) : (
            <Button className="w-full" onClick={onInstall} disabled={installing}>
              {installing && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {t("marketplace.install")}
            </Button>
          )}
        </footer>
      </aside>
    </div>
  );
}

/**
 * What installing this would actually reach.
 *
 * A plugin is a door to somebody else's service: the hosted URL its requests
 * and access token go to, or the command it would run on this computer. The
 * plugin consent dialog has always said this verbatim, and now that the drawer
 * is a second way to install, it has to say it too — a file listing alone does
 * not tell anybody where their data ends up.
 */
function Destination({ entry, t }: { entry: Entry; t: Translate }) {
  const rows: { label: string; value?: string; code?: string }[] = [];

  if (entry.kind === "plugin") {
    const mcp = entry.mcp;
    if (mcp?.transport === "stdio") {
      rows.push({
        label: t("marketplace.runs_command"),
        code: (mcp.install ?? []).join(" "),
      });
    } else if (mcp?.url) {
      rows.push({ label: t("marketplace.sends_data_to"), code: mcp.url });
    } else {
      rows.push({ label: t("marketplace.metadata_only") });
    }
    const auth = entry.authMode ? AUTH_MODE_LABEL[entry.authMode] : undefined;
    if (auth) rows.push({ label: t("marketplace.sign_in_method"), value: auth });
  }

  if (entry.portableAgents) {
    rows.push({
      label: t("marketplace.portable_skill"),
      value:
        entry.portableAgents.length > 0
          ? entry.portableAgents.join(", ")
          : t("marketplace.portable_any"),
    });
  }

  if (entry.license) {
    rows.push({ label: t("marketplace.license"), value: entry.license });
  }

  if (rows.length === 0) return null;

  return (
    <div className="space-y-2">
      {entry.seedConflict && (
        <p className="flex items-start gap-2 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-300">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          {t("marketplace.seed_conflict")}
        </p>
      )}
      {rows.map((row) => (
        <div key={row.label}>
          <p className="mb-1 text-xs font-medium text-foreground">{row.label}</p>
          {row.code && (
            <code className="block break-all rounded-md border border-border bg-muted/40 px-2 py-1.5 text-xs text-foreground">
              {row.code}
            </code>
          )}
          {row.value && <p className="text-xs text-muted-foreground">{row.value}</p>}
        </div>
      ))}
    </div>
  );
}

/** Mirrors the plugin consent dialog's labels — one vocabulary for one act. */
const AUTH_MODE_LABEL: Record<string, string> = {
  pat_paste: "Personal access token",
  oauth_device_flow: "Device sign-in",
  hosted_mcp_oauth_dcr: "OAuth sign-in",
  oauth_pkce_loopback: "OAuth sign-in",
  hosted_mcp_allowlist: "Account allowlist",
};

/**
 * Where the thing landed — and the way to it.
 *
 * An install that ends in a green tick leaves the person holding something they
 * cannot find. This says what arrived, whether it is usable yet, and jumps to
 * the section that now holds it.
 */
function LandingToast({
  result,
  onClose,
  t,
}: {
  result: InstallResultWire;
  onClose: () => void;
  t: Translate;
}) {
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const target = HOME_SECTION[result.kind] ?? "skills";
  const ready = result.ready !== false;
  return (
    <div className="pointer-events-none absolute inset-x-0 bottom-0 z-50 flex justify-center p-5">
      <div className="pointer-events-auto flex w-full max-w-lg items-start gap-3 rounded-xl border border-border bg-card px-4 py-3 shadow-xl">
        <div
          className={cn(
            "grid h-8 w-8 shrink-0 place-items-center rounded-lg",
            ready ? "bg-primary/15 text-primary" : "bg-amber-500/15 text-amber-600 dark:text-amber-400",
          )}
        >
          {ready ? <Check className="h-4 w-4" /> : <AlertTriangle className="h-4 w-4" />}
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-foreground">
            {fill(t("marketplace.landed_title"), { title: result.title ?? result.id ?? "" })}
          </p>
          <p className="text-xs text-muted-foreground">
            {result.problem
              ? result.problem
              : ready
                ? t(`marketplace.landed_${result.kind}`)
                : t("marketplace.landed_needs_connect")}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <Button
            size="sm"
            variant="outline"
            onClick={() => {
              onClose();
              setActiveSection(target);
            }}
          >
            {t("marketplace.landed_open")}
          </Button>
          <Button size="sm" variant="ghost" onClick={onClose} aria-label={t("marketplace.close")}>
            <X className="h-4 w-4" />
          </Button>
        </div>
      </div>
    </div>
  );
}
