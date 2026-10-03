import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowUpRight,
  Check,
  ChevronRight,
  ExternalLink,
  FileText,
  Loader2,
  Package,
  RefreshCw,
  Search,
  ShieldAlert,
  Store,
  UploadCloud,
  X,
} from "lucide-react";

import { Badge, badgeVariants } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { ScrollArea } from "@/components/ui/scroll-area";
import { TabBar } from "@/components/layout/SectionTabBar";
import { ViewHeader } from "@/views/ChatsView";
import {
  GithubSignInDialog,
  PublisherChip,
  usePublishIdentity,
} from "@/components/marketplace/PublishIdentity";
import { PublishStudio } from "@/components/marketplace/PublishStudio";
import { fill, useI18nStore, useLocaleChunk, useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { openExternalUrl } from "@/lib/openExternal";
import { bundledPluginLogo } from "@/lib/pluginLogos";
import { useEventStore, type SectionId } from "@/store/events";
import {
  MARKETPLACE_SUBMIT_URL,
  type CommunityPluginWire,
  type CommunityResponse,
  type CommunitySkillWire,
  type EntryContentsWire,
} from "@/views/PluginsCommunity";

// ---------------------------------------------------------------------------
// The Jarvis Marketplace, as a section of its own.
//
// Everything published by the community used to be reachable only by knowing
// where to look: plugins behind the "Community" tab of Skills & Tools, skills
// in a second list. Somebody who never opened those places never learned the
// marketplace exists.
//
// This screen is the storefront: one index, one search across both kinds,
// and — the part that matters — a landing that says where the thing WENT, with
// a jump into the section that now holds it. Installing is not the end of the
// errand; using the thing is.
//
// Everything here is UNREVIEWED third-party content. The registry auto-merges
// submissions that pass automated checks, so every install goes through the
// detail sheet, where the publisher, the destination, the source and the
// actual published files are on screen before the button is reachable.
// ---------------------------------------------------------------------------

/** The public storefront — the same catalogue, on the web. */
const MARKETPLACE_WEB_URL = "https://github.com/PersonalJarvis/marketplace";

type Kind = "plugin" | "skill";
type KindFilter = "all" | Kind | "installed" | "mine";

/** One entry of any kind, flattened into what the storefront draws. */
interface Entry {
  kind: Kind;
  /** The registry name — the id every API route takes. */
  name: string;
  title: string;
  /** Null when the index carries nothing readable (a leaked `|`, a blank). */
  description: string | null;
  publisher?: string | null;
  version?: string | null;
  publishedAt?: string | null;
  categories: string[];
  sourceUrl?: string | null;
  installed: boolean;
  /** Plugins only: the installed version differs from the published one. */
  updateAvailable?: boolean;
  installedVersion?: string | null;
  featured?: boolean;
  /** Plugins only — the brand tile. */
  logoSlug?: string | null;
  logoUrl?: string | null;
  logoColor?: string | null;
  /** A plugin whose manifest this client could not read. */
  broken?: boolean;
  problem?: string | null;
  /**
   * Where installing this would eventually send data, or what it would run.
   *
   * The whole reason the sheet exists: the registry auto-merges submissions
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
  /** Plugins only: the publisher's note on what to do after installing. */
  postInstallHint?: string | null;
}

/** Where an installed entry of this kind now lives in the app. */
const HOME_SECTION: Record<Kind, SectionId> = {
  plugin: "plugins",
  skill: "skills",
};

/**
 * A description worth printing, or null.
 *
 * The index generator has served a bare YAML block marker (`"|"`) as a
 * description; a card that prints one stray bar reads as broken. Anything
 * with no letter or digit in it is treated as missing.
 */
function readableText(raw: string | null | undefined): string | null {
  const text = (raw ?? "").trim();
  return /[\p{L}\p{N}]/u.test(text) ? text : null;
}

function pluginEntry(p: CommunityPluginWire): Entry {
  const raw = (p.logo_color ?? "").trim();
  const installedVersion = p.installed_version ?? null;
  return {
    kind: "plugin",
    name: p.id ?? p.name,
    title: p.display_name ?? p.name,
    description: readableText(p.description),
    publisher: p.publisher,
    version: p.version,
    publishedAt: p.published_at ?? null,
    categories: p.category ? [p.category] : [],
    sourceUrl: p.source_url,
    installed: Boolean(p.installed),
    installedVersion,
    updateAvailable: Boolean(
      p.installed && installedVersion && p.version && installedVersion !== p.version,
    ),
    featured: Boolean(p.featured),
    logoSlug: p.logo_slug ?? null,
    logoUrl: p.logo_url ?? null,
    logoColor: /^[0-9a-fA-F]{6}$/.test(raw) ? `#${raw}` : null,
    broken: !p.valid,
    problem: p.error ?? null,
    mcp: p.mcp_server ?? null,
    authMode: p.auth?.mode ?? null,
    seedConflict: Boolean(p.seed_conflict),
    postInstallHint: readableText(p.post_install_hint_md),
  };
}

function skillEntry(s: CommunitySkillWire): Entry {
  return {
    kind: "skill",
    name: s.name,
    title: s.title || s.name,
    description: readableText(s.description),
    publisher: s.publisher,
    version: s.version,
    publishedAt: s.published_at ?? null,
    categories: s.categories ?? [],
    sourceUrl: s.source_url,
    installed: Boolean(s.installed),
    portableAgents: s.flavor === "portable" ? (s.compatible_agents ?? []) : null,
  };
}

function matches(entry: Entry, needle: string): boolean {
  if (!needle) return true;
  const hay = [
    entry.title,
    entry.name,
    entry.description ?? "",
    entry.publisher ?? "",
    entry.categories.join(" "),
  ]
    .join(" ")
    .toLowerCase();
  return hay.includes(needle);
}

function passesFilter(entry: Entry, filter: KindFilter, login: string | null): boolean {
  switch (filter) {
    case "all":
      return true;
    case "installed":
      return entry.installed;
    case "mine":
      return login !== null && entry.publisher === login;
    default:
      return entry.kind === filter;
  }
}

/** Featured first, then the newest publication, then by name. */
function storeOrder(a: Entry, b: Entry): number {
  if (Boolean(a.featured) !== Boolean(b.featured)) return a.featured ? -1 : 1;
  const at = a.publishedAt ? Date.parse(a.publishedAt) : 0;
  const bt = b.publishedAt ? Date.parse(b.publishedAt) : 0;
  if (at !== bt) return bt - at;
  return a.title.localeCompare(b.title);
}

/** Registry category slugs ("productivity") read as labels ("Productivity"). */
function categoryLabel(category: string): string {
  const spaced = category.replace(/[-_]+/g, " ").trim();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
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
  // Section-only strings ride in a lazy chunk (bundle budget). The header and
  // toolbar render from the main locale file; the shelves wait for the chunk
  // so no card ever paints a raw key.
  const localeReady = useLocaleChunk("marketplace");
  const queryClient = useQueryClient();
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const [query, setQuery] = useState("");
  const [kindFilter, setKindFilter] = useState<KindFilter>("all");
  const [openEntry, setOpenEntry] = useState<Entry | null>(null);
  const [landing, setLanding] = useState<InstallResultWire | null>(null);
  const [studioOpen, setStudioOpen] = useState(false);
  const [signInOpen, setSignInOpen] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);
  const identity = usePublishIdentity();
  const login = identity.data?.signed_in ? (identity.data.login ?? null) : null;
  const publishEnabled = identity.data?.enabled !== false;

  const { data, isLoading, error, refetch, isRefetching } = useQuery({
    queryKey: ["marketplace-community"],
    queryFn: fetchCommunity,
    // The store stays live while it is open: a publish that just merged shows
    // up on its own instead of waiting for the refresh button. Cheap on
    // purpose — the server answers from its cache inside the TTL and
    // revalidates with a conditional GET (304) after it, so this never
    // re-downloads an unchanged index. Paused while the window is hidden.
    refetchInterval: 60_000,
    refetchIntervalInBackground: false,
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
    onSuccess: (result, installed) => {
      // Close the sheet only if it still shows what was installed — the person
      // may have moved on to another entry while the request ran.
      setOpenEntry((current) => (current?.name === installed.name ? null : current));
      setLanding(result);
      // Everything that lists installed things must reflect the new arrival.
      queryClient.invalidateQueries({ queryKey: ["marketplace-community"] });
      queryClient.invalidateQueries({ queryKey: ["marketplace-plugins"] });
      queryClient.invalidateQueries({ queryKey: ["skills"] });
    },
  });

  const openDetail = (entry: Entry) => {
    // An error from a previous entry's install must not follow the sheet; a
    // running install keeps its state so its spinner and toast stay honest.
    if (!install.isPending) install.reset();
    setOpenEntry(entry);
  };

  // "/" jumps into the search, the way every storefront with a search does —
  // unless the person is already typing somewhere, or a modal layer is open
  // (focus must not escape into the page behind it).
  const modalOpen = openEntry !== null || studioOpen || signInOpen;
  const modalOpenRef = useRef(modalOpen);
  modalOpenRef.current = modalOpen;
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "/" || event.ctrlKey || event.metaKey || event.altKey) return;
      if (modalOpenRef.current) return;
      const target = event.target as HTMLElement | null;
      if (target?.closest("input, textarea, [contenteditable='true']")) return;
      event.preventDefault();
      searchRef.current?.focus();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const entries = useMemo<Entry[]>(() => {
    if (!data) return [];
    return [
      ...(data.plugins ?? []).map(pluginEntry),
      ...(data.skills ?? []).map(skillEntry),
    ].sort(storeOrder);
  }, [data]);

  const needle = query.trim().toLowerCase();
  // The chip counts answer "how many would I see here", so they follow the
  // search — "Plugins 1" beside zero results is a small lie.
  const searched = useMemo(() => entries.filter((e) => matches(e, needle)), [entries, needle]);
  const counts = useMemo<Record<KindFilter, number>>(
    () => ({
      all: searched.length,
      plugin: searched.filter((e) => e.kind === "plugin").length,
      skill: searched.filter((e) => e.kind === "skill").length,
      installed: searched.filter((e) => e.installed).length,
      mine: login ? searched.filter((e) => e.publisher === login).length : 0,
    }),
    [searched, login],
  );
  const visible = useMemo(
    () => searched.filter((e) => passesFilter(e, kindFilter, login)),
    [searched, kindFilter, login],
  );

  const plugins = visible.filter((e) => e.kind === "plugin");
  const skills = visible.filter((e) => e.kind === "skill");

  const status = data?.status;
  const offline = status === "stale" || status === "unavailable";
  const ready = !isLoading && !error && localeReady;
  // Switched off, or unreachable with nothing cached: there is no shelf to
  // invite anybody onto, so the page says only that.
  const indexDown = entries.length === 0 && (status === "disabled" || status === "unavailable");
  const frontPage = kindFilter === "all" && !needle;

  const clearAll = () => {
    setQuery("");
    setKindFilter("all");
  };

  return (
    <div className="relative flex h-full min-h-0 flex-col">
      <ViewHeader
        icon={<Store className="h-4 w-4 text-muted-foreground" />}
        title={t("marketplace.title")}
        subtitle={subtitleFor(t, data, isLoading, localeReady)}
        right={
          <div className="flex items-center gap-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => refresh.mutate()}
              disabled={refresh.isPending}
              title={t("marketplace.refresh")}
              aria-label={t("marketplace.refresh")}
            >
              <RefreshCw
                className={cn("h-4 w-4", refresh.isPending && "animate-spin")}
              />
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => openExternalUrl(MARKETPLACE_WEB_URL)}
              title={t("marketplace.open_web")}
            >
              {t("marketplace.open_web")}
              <ExternalLink className="ml-1.5 h-3.5 w-3.5" />
            </Button>
            <PublisherChip
              onSignIn={() => setSignInOpen(true)}
              onMine={() => {
                setKindFilter("mine");
                setQuery("");
              }}
            />
            {publishEnabled && (
              <Button size="sm" onClick={() => setStudioOpen(true)} data-testid="marketplace-publish">
                <UploadCloud className="mr-1.5 h-3.5 w-3.5" />
                {t("marketplace.publish_cta")}
              </Button>
            )}
          </div>
        }
      />

      <div className="shrink-0 border-b border-border px-8">
        <div className="flex w-full max-w-6xl flex-wrap items-center gap-x-6 gap-y-2">
          <FilterTabs
            active={kindFilter}
            onChange={setKindFilter}
            counts={counts}
            showInstalled={localeReady && entries.some((e) => e.installed)}
            showMine={localeReady && login !== null}
            t={t}
          />
          <SearchField
            inputRef={searchRef}
            value={query}
            onChange={setQuery}
            placeholder={t("marketplace.search_placeholder")}
            clearLabel={t("marketplace.empty_clear")}
          />
        </div>
      </div>

      {refresh.error && (
        <div className="shrink-0 border-b border-destructive/20 bg-destructive/[0.08] px-8 py-2">
          <p
            role="alert"
            className="flex w-full max-w-6xl items-center gap-2 text-xs text-destructive"
          >
            <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
            {(refresh.error as Error).message}
          </p>
        </div>
      )}

      {offline && (
        <div className="shrink-0 border-b border-warning/20 bg-warning/[0.08] px-8 py-2">
          <div className="flex w-full max-w-6xl items-center gap-2 text-xs text-foreground">
            <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-warning" />
            <span className="min-w-0 flex-1">
              {status === "unavailable"
                ? t("marketplace.status_unavailable")
                : t("marketplace.status_stale")}
            </span>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => refresh.mutate()}
              disabled={refresh.isPending}
            >
              {refresh.isPending && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
              {localeReady ? t("marketplace.retry") : t("marketplace.refresh")}
            </Button>
          </div>
        </div>
      )}

      <ScrollArea className="min-h-0 flex-1">
        {/* Left-aligned under the header and the toolbar, capped so a wide
            window does not stretch three cards into a slab. */}
        <div className="w-full max-w-[calc(72rem+4rem)] px-8 py-6">
          {(isLoading || (!error && !localeReady)) && <SkeletonGrid />}

          {error && !isLoading && localeReady && (
            <EmptyState
              icon={<AlertTriangle />}
              title={t("marketplace.error_title")}
              description={(error as Error).message}
              actions={
                <Button size="sm" onClick={() => refetch()} disabled={isRefetching}>
                  {isRefetching && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
                  {t("marketplace.retry")}
                </Button>
              }
            />
          )}

          {ready && frontPage && !indexDown && (
            <Hero publishEnabled={publishEnabled} t={t} />
          )}

          {ready && indexDown && (
            <EmptyState
              icon={status === "disabled" ? <Store /> : <AlertTriangle />}
              title={
                status === "disabled"
                  ? t("marketplace.status_disabled")
                  : t("marketplace.error_title")
              }
              description={
                status === "unavailable" ? t("marketplace.status_unavailable") : undefined
              }
            />
          )}

          {ready && visible.length === 0 && !indexDown && (
            <NoResults
              query={query.trim()}
              filter={kindFilter}
              onClear={clearAll}
              onPublish={publishEnabled ? () => setStudioOpen(true) : null}
              onBrowseBuiltIn={() => setActiveSection("plugins")}
              t={t}
            />
          )}

          {ready && plugins.length > 0 && (
            <Shelf
              title={t("marketplace.shelf_plugins")}
              hint={t("marketplace.shelf_plugins_hint")}
              count={plugins.length}
            >
              <EntryGrid entries={plugins} onOpen={openDetail} t={t} />
            </Shelf>
          )}

          {ready && skills.length > 0 && (
            <Shelf
              title={t("marketplace.shelf_skills")}
              hint={t("marketplace.shelf_skills_hint")}
              count={skills.length}
            >
              <EntryGrid entries={skills} onOpen={openDetail} t={t} />
            </Shelf>
          )}

          {ready && visible.length > 0 && !indexDown && (
            <PublishInvite
              enabled={publishEnabled}
              onPublish={() => setStudioOpen(true)}
              t={t}
            />
          )}
        </div>
      </ScrollArea>

      {openEntry && (
        <EntrySheet
          entry={openEntry}
          onClose={() => setOpenEntry(null)}
          onInstall={() => install.mutate(openEntry)}
          onOpenHome={() => {
            setOpenEntry(null);
            setActiveSection(HOME_SECTION[openEntry.kind]);
          }}
          installing={install.isPending && install.variables?.name === openEntry.name}
          installError={
            install.error && install.variables?.name === openEntry.name
              ? (install.error as Error).message
              : null
          }
          t={t}
        />
      )}

      {landing && <LandingToast result={landing} onClose={() => setLanding(null)} t={t} />}

      {studioOpen && <PublishStudio onClose={() => setStudioOpen(false)} />}
      {signInOpen && <GithubSignInDialog onClose={() => setSignInOpen(false)} />}
    </div>
  );
}

type Translate = (key: string) => string;

/** "3 published entries · Updated Sep 30", or the honest alternative. */
function subtitleFor(
  t: Translate,
  data: CommunityResponse | undefined,
  loading: boolean,
  localeReady: boolean,
): string {
  if (loading) return t("marketplace.loading");
  if (!data) return "";
  if (data.status === "disabled") return t("marketplace.status_disabled");
  const total = (data.plugins?.length ?? 0) + (data.skills?.length ?? 0);
  const parts = [fill(t("marketplace.subtitle_count"), { count: total })];
  if (data.generated_at && localeReady) {
    parts.push(fill(t("marketplace.subtitle_updated"), { date: formatDate(data.generated_at) }));
  }
  return parts.join(" · ");
}

/** A date in the app's UI language — not the operating system's. */
function formatDate(iso: string): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return iso;
  return parsed.toLocaleDateString(useI18nStore.getState().ui, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function SearchField({
  inputRef,
  value,
  onChange,
  placeholder,
  clearLabel,
}: {
  inputRef: React.RefObject<HTMLInputElement>;
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  clearLabel: string;
}) {
  return (
    <div className="relative my-1.5 min-w-[240px] max-w-md flex-1 sm:ml-auto">
      <Search className="pointer-events-none absolute left-3 top-1/2 z-10 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
      <input
        ref={inputRef}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Escape" && value) {
            event.stopPropagation();
            onChange("");
          }
        }}
        placeholder={placeholder}
        aria-label={placeholder}
        className={cn(
          "h-9 w-full rounded-md border border-border-strong bg-input pl-9 pr-16",
          "text-base text-foreground placeholder:text-foreground-faint",
          "transition-colors focus-visible:border-accent focus-visible:outline-none",
          "focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
        )}
      />
      <div className="absolute right-2 top-1/2 flex -translate-y-1/2 items-center gap-1">
        {value ? (
          <button
            type="button"
            onClick={() => {
              onChange("");
              inputRef.current?.focus();
            }}
            aria-label={clearLabel}
            title={clearLabel}
            className="grid h-6 w-6 place-items-center rounded text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        ) : (
          <kbd
            aria-hidden
            className="grid h-5 min-w-5 place-items-center rounded border border-border px-1 font-mono text-xs text-muted-foreground"
          >
            /
          </kbd>
        )}
      </div>
    </div>
  );
}

/** The kind switch: the house underline tabs, each with its live count. */
function FilterTabs({
  active,
  onChange,
  counts,
  showInstalled,
  showMine,
  t,
}: {
  active: KindFilter;
  onChange: (kind: KindFilter) => void;
  counts: Record<KindFilter, number>;
  showInstalled: boolean;
  showMine: boolean;
  t: Translate;
}) {
  const ids: KindFilter[] = ["all", "plugin", "skill"];
  if (showInstalled || active === "installed") ids.push("installed");
  if (showMine) ids.push("mine");
  const label: Record<KindFilter, string> = {
    all: t("marketplace.filter_all"),
    plugin: t("marketplace.filter_plugins"),
    skill: t("marketplace.filter_skills"),
    installed: t("marketplace.installed"),
    mine: t("marketplace.filter_mine"),
  };
  return (
    <TabBar
      tabs={ids.map((id) => ({ id, label: label[id], count: counts[id] }))}
      active={active}
      onChange={(id) => onChange(id as KindFilter)}
      className="shrink-0 border-b-0"
    />
  );
}

/** A titled band of the storefront: a heading, a quiet hint, and its cards. */
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
    <section className="mb-10 last:mb-0">
      <div className="mb-3 flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
        <h2 className="text-lg font-semibold text-foreground-strong">{title}</h2>
        <span className="text-sm tabular-nums text-muted-foreground">{count}</span>
        <span className="w-full text-sm text-muted-foreground sm:ml-2 sm:w-auto">{hint}</span>
      </div>
      {children}
    </section>
  );
}

const GRID = "grid gap-3 [grid-template-columns:repeat(auto-fill,minmax(min(100%,300px),1fr))]";

function SkeletonGrid() {
  return (
    <div role="status" aria-busy="true" data-testid="marketplace-skeleton">
      <div className="mb-3 h-5 w-32 rounded bg-secondary" />
      <div className={GRID}>
        {Array.from({ length: 6 }, (_, index) => (
          <div key={index} className="flex h-[148px] flex-col gap-3 rounded-lg border border-border bg-card p-4">
            <div className="flex items-center gap-3">
              <div className="h-10 w-10 rounded-lg bg-secondary" />
              <div className="flex-1 space-y-1.5">
                <div className="h-3.5 w-2/5 rounded bg-secondary" />
                <div className="h-3 w-1/4 rounded bg-secondary" />
              </div>
            </div>
            <div className="space-y-1.5">
              <div className="h-3 w-full rounded bg-secondary" />
              <div className="h-3 w-3/4 rounded bg-secondary" />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// --- Brand marks -----------------------------------------------------------
// The same three tiers the built-in Plugins catalog uses, so one brand looks
// the same in both places: a full-colour mark (bundled or published) on the
// white app-icon surface, else the Simple Icons glyph on the brand's own
// colour, else a monogram on that colour.
const DEFAULT_BRAND_TILE = "#3F3F46";
/** Bundled marks drawn in white; on the white app-icon surface they invert. */
const INVERTED_MARKS = new Set(["github", "vercel", "notion", "cal_com"]);

/** A glyph colour that stays legible on the tile (a few brands are near-white). */
function glyphColor(tileHex: string): string {
  const hex = tileHex.replace("#", "");
  if (hex.length !== 6) return "ffffff";
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  const lin = (c: number) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
  const luminance = 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
  return luminance > 0.6 ? "111111" : "ffffff";
}

function EntryMark({ entry, size = "md" }: { entry: Entry; size?: "md" | "lg" }) {
  const [failed, setFailed] = useState(false);
  const box = size === "lg" ? "h-12 w-12 rounded-xl" : "h-10 w-10 rounded-lg";

  if (entry.broken) {
    return (
      <div className={cn("grid shrink-0 place-items-center border border-border bg-muted", box)}>
        <AlertTriangle className="h-4 w-4 text-muted-foreground" />
      </div>
    );
  }

  if (entry.kind === "skill") {
    // Skills carry no artwork; the initial gives each one a face of its own
    // on the neutral ground instead of one shared wand for all of them.
    return (
      <div
        className={cn(
          "grid shrink-0 place-items-center border border-border bg-secondary font-semibold text-foreground",
          box,
          size === "lg" ? "text-lg" : "text-base",
        )}
      >
        {entry.title.slice(0, 1).toUpperCase()}
      </div>
    );
  }

  const tile = entry.logoColor ?? DEFAULT_BRAND_TILE;
  const fullColour = bundledPluginLogo(entry.name) ?? entry.logoUrl ?? null;
  const src =
    fullColour ??
    (entry.logoSlug
      ? `https://cdn.simpleicons.org/${entry.logoSlug}/${glyphColor(tile)}`
      : null);
  const monogram = failed || !src;
  const onWhite = Boolean(fullColour) && !monogram;

  return (
    <div
      className={cn(
        "grid shrink-0 place-items-center overflow-hidden border",
        box,
        onWhite ? "border-border bg-[hsl(var(--plugin-icon-surface))]" : "border-border/60",
      )}
      style={onWhite ? undefined : { backgroundColor: tile }}
    >
      {monogram ? (
        <span className="text-sm font-semibold" style={{ color: `#${glyphColor(tile)}` }}>
          {entry.title.slice(0, 1).toUpperCase()}
        </span>
      ) : (
        <img
          src={src}
          alt=""
          loading="lazy"
          className={cn(
            onWhite && INVERTED_MARKS.has(entry.name.replace(/-/g, "_")) && "invert",
            onWhite
              ? size === "lg"
                ? "h-8 w-8"
                : "h-7 w-7"
              : size === "lg"
                ? "h-6 w-6"
                : "h-5 w-5",
          )}
          onError={() => setFailed(true)}
        />
      )}
    </div>
  );
}

/**
 * The state an entry is in, as one small label — or nothing when it is just
 * on the shelf. A `<span>` with the badge recipe, because it sits inside the
 * card's `<button>`, which allows phrasing content only.
 */
function EntryState({ entry, t }: { entry: Entry; t: Translate }) {
  if (entry.updateAvailable) {
    return (
      <span className={cn(badgeVariants({ variant: "accent" }), "shrink-0")}>
        {t("marketplace.update_available")}
      </span>
    );
  }
  if (entry.installed) {
    return (
      <span className={cn(badgeVariants({ variant: "success" }), "shrink-0")}>
        <Check />
        {t("marketplace.installed")}
      </span>
    );
  }
  return null;
}

/** Cards in a grid: each one an object you press as a whole to open its sheet. */
function EntryGrid({
  entries,
  onOpen,
  t,
}: {
  entries: Entry[];
  onOpen: (entry: Entry) => void;
  t: Translate;
}) {
  return (
    <div className={GRID}>
      {entries.map((entry) => (
        <EntryCard key={`${entry.kind}:${entry.name}`} entry={entry} onOpen={onOpen} t={t} />
      ))}
    </div>
  );
}

function EntryCard({
  entry,
  onOpen,
  t,
}: {
  entry: Entry;
  onOpen: (entry: Entry) => void;
  t: Translate;
}) {
  const meta = [
    entry.categories[0] ? categoryLabel(entry.categories[0]) : null,
    entry.version ? `v${entry.version}` : null,
    entry.publishedAt ? formatDate(entry.publishedAt) : null,
  ].filter(Boolean);
  const description = entry.broken
    ? (entry.problem ?? t("marketplace.entry_unreadable"))
    : (entry.description ?? t("marketplace.description_missing"));

  return (
    <button
      type="button"
      onClick={() => onOpen(entry)}
      data-testid={`marketplace-card-${entry.name}`}
      className={cn(
        "group flex min-w-0 flex-col gap-3 rounded-lg border border-border bg-card p-4 text-left shadow-rim",
        "transition-colors hover:border-border-strong",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
      )}
    >
      <div className="flex w-full min-w-0 items-start gap-3">
        <EntryMark entry={entry} />
        <div className="min-w-0 flex-1">
          <span className="block truncate text-base font-semibold text-foreground-strong">
            {entry.title}
          </span>
          <span className="block truncate text-sm text-muted-foreground">
            {entry.publisher ? `@${entry.publisher}` : t(`marketplace.kind_${entry.kind}`)}
          </span>
        </div>
        <EntryState entry={entry} t={t} />
      </div>
      <span
        className={cn(
          "line-clamp-2 min-h-[40px] text-base",
          entry.description || entry.broken ? "text-muted-foreground" : "italic text-foreground-faint",
        )}
      >
        {description}
      </span>
      <div className="mt-auto flex w-full items-center gap-2 text-xs text-muted-foreground">
        <span className="min-w-0 flex-1 truncate tabular-nums">{meta.join(" · ")}</span>
        <ChevronRight className="h-4 w-4 shrink-0 text-foreground-faint transition-colors group-hover:text-foreground" />
      </div>
    </button>
  );
}

function NoResults({
  query,
  filter,
  onClear,
  onPublish,
  onBrowseBuiltIn,
  t,
}: {
  query: string;
  filter: KindFilter;
  onClear: () => void;
  onPublish: (() => void) | null;
  onBrowseBuiltIn: () => void;
  t: Translate;
}) {
  if (filter === "mine" && !query) {
    return (
      <div data-testid="marketplace-empty-mine">
        <EmptyState
          icon={<UploadCloud />}
          title={t("marketplace.empty_mine")}
          actions={
            onPublish && (
              <Button size="sm" onClick={onPublish}>
                <UploadCloud className="mr-1.5 h-3.5 w-3.5" />
                {t("marketplace.publish_cta")}
              </Button>
            )
          }
        />
      </div>
    );
  }
  if (query) {
    return (
      <EmptyState
        icon={<Search />}
        title={fill(t("marketplace.empty_search"), { query })}
        description={t("marketplace.empty_search_builtin")}
        actions={
          <>
            <Button size="sm" onClick={onBrowseBuiltIn}>
              {t("marketplace.browse_builtin")}
            </Button>
            <Button size="sm" variant="outline" onClick={onClear}>
              {t("marketplace.empty_clear")}
            </Button>
          </>
        }
      />
    );
  }
  return (
    <EmptyState
      icon={<Package />}
      title={t("marketplace.empty")}
      actions={
        filter !== "all" ? (
          <Button size="sm" variant="outline" onClick={onClear}>
            {t("marketplace.show_all")}
          </Button>
        ) : undefined
      }
    />
  );
}

/**
 * The storefront's opening line: what this shelf is and how it is vetted.
 * Drawn on the unfiltered front page only, so a search never scrolls past
 * it. It carries no buttons — Publish lives in the header and at the foot of
 * the page, and a third copy here only made the page louder.
 */
function Hero({ publishEnabled, t }: { publishEnabled: boolean; t: Translate }) {
  return (
    <section
      className="mb-8 rounded-lg border border-border bg-card px-5 py-4 shadow-rim"
      data-testid="marketplace-hero"
    >
      <p className="text-xs font-medium text-accent">{t("marketplace.hero_eyebrow")}</p>
      <h2 className="mt-0.5 text-lg font-semibold text-foreground-strong">
        {t("marketplace.hero_title")}
      </h2>
      <p className="mt-1 max-w-2xl text-base text-muted-foreground">
        {publishEnabled ? t("marketplace.hero_body") : t("marketplace.hero_body_browse")}
      </p>
    </section>
  );
}

/** The closing invitation: publish in the app when it can, on the web otherwise. */
function PublishInvite({
  enabled,
  onPublish,
  t,
}: {
  enabled: boolean;
  onPublish: () => void;
  t: Translate;
}) {
  return (
    <div className="mt-10 flex flex-wrap items-center gap-4 rounded-lg border border-dashed border-border-strong px-5 py-4">
      <div className="grid h-10 w-10 shrink-0 place-items-center rounded-lg bg-secondary text-muted-foreground">
        <UploadCloud className="h-4 w-4" />
      </div>
      <div className="min-w-0 flex-1 basis-[280px]">
        <p className="text-base font-semibold text-foreground-strong">
          {t("marketplace.publish_card_title")}
        </p>
        <p className="text-sm text-muted-foreground">
          {enabled ? t("marketplace.publish_card_body") : t("marketplace.publish_hint")}
        </p>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {!enabled && (
          <Button
            variant="outline"
            size="sm"
            onClick={() => openExternalUrl(MARKETPLACE_SUBMIT_URL)}
            title={t("marketplace.publish_on_web")}
          >
            {t("marketplace.publish_on_web")}
            <ExternalLink className="ml-1.5 h-3.5 w-3.5" />
          </Button>
        )}
        {enabled && (
          <Button size="sm" onClick={onPublish}>
            <UploadCloud className="mr-1.5 h-3.5 w-3.5" />
            {t("marketplace.publish_cta")}
          </Button>
        )}
      </div>
    </div>
  );
}

/** The published bytes of one entry, fetched only when its sheet opens. */
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

/** Tab stops inside the sheet, for the focus trap. */
const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])';

/**
 * The trust boundary. Nothing installs from a card — the publisher, where
 * the data goes, the source repo and the actual published files are on
 * screen first, because this is unreviewed third-party content and the person
 * clicking deserves to see what they are about to run.
 */
function EntrySheet({
  entry,
  onClose,
  onInstall,
  onOpenHome,
  installing,
  installError,
  t,
}: {
  entry: Entry;
  onClose: () => void;
  onInstall: () => void;
  onOpenHome: () => void;
  installing: boolean;
  installError: string | null;
  t: Translate;
}) {
  const contents = useEntryContents(entry.name);
  const sheetRef = useRef<HTMLElement>(null);
  const titleId = `marketplace-sheet-${entry.kind}-${entry.name}`;
  // The parent hands a fresh `onClose` every render (the index refetches each
  // minute); the focus effect below must run once per opening, not per render.
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  useEffect(() => {
    // Focus moves into the sheet and returns to the card that opened it.
    const opener = document.activeElement as HTMLElement | null;
    sheetRef.current?.querySelector<HTMLElement>("[data-autofocus]")?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        closeRef.current();
        return;
      }
      if (event.key !== "Tab" || !sheetRef.current) return;
      const stops = Array.from(sheetRef.current.querySelectorAll<HTMLElement>(FOCUSABLE));
      if (stops.length === 0) return;
      const first = stops[0];
      const last = stops[stops.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      opener?.focus?.();
    };
  }, []);

  const byline = [
    entry.publisher ? `@${entry.publisher}` : null,
    entry.version ? `v${entry.version}` : null,
    entry.publishedAt ? formatDate(entry.publishedAt) : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <div className="absolute inset-0 z-40 flex justify-end">
      <button
        type="button"
        tabIndex={-1}
        aria-label={t("marketplace.close")}
        onClick={onClose}
        className="absolute inset-0 bg-scrim/40 backdrop-blur-[2px]"
      />
      {/* A sheet over a scrim leaves the plane, so it takes the floating
          ground and its cast edge instead of the card ground. */}
      <aside
        ref={sheetRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="relative flex h-full w-full max-w-[520px] flex-col border-l border-border bg-popover shadow-float"
      >
        <header className="border-b border-border px-6 pb-5 pt-5">
          <div className="flex items-start gap-4">
            <EntryMark entry={entry} size="lg" />
            <div className="min-w-0 flex-1">
              <p className="text-xs text-muted-foreground">
                {t(`marketplace.kind_${entry.kind}`)}
              </p>
              <h2 id={titleId} className="truncate text-xl font-semibold text-foreground-strong">
                {entry.title}
              </h2>
              {byline && <p className="truncate text-sm text-muted-foreground">{byline}</p>}
            </div>
            <Button
              variant="ghost"
              size="sm"
              onClick={onClose}
              aria-label={t("marketplace.close")}
              data-autofocus
            >
              <X className="h-4 w-4" />
            </Button>
          </div>
          <div className="mt-4">
            <SheetAction
              entry={entry}
              onInstall={onInstall}
              onOpenHome={onOpenHome}
              installing={installing}
              installError={installError}
              t={t}
            />
          </div>
        </header>

        <ScrollArea className="min-h-0 flex-1">
          <div className="space-y-6 px-6 py-5">
            <SheetSection title={t("marketplace.section_about")}>
              <p
                className={cn(
                  "text-base leading-relaxed",
                  entry.description ? "text-foreground" : "italic text-foreground-faint",
                )}
              >
                {entry.description ?? t("marketplace.description_missing")}
              </p>
              {entry.categories.length > 0 && (
                <div className="mt-3 flex flex-wrap gap-1.5">
                  {entry.categories.map((category) => (
                    <Badge key={category}>{categoryLabel(category)}</Badge>
                  ))}
                </div>
              )}
            </SheetSection>

            <Destination entry={entry} t={t} />

            {entry.postInstallHint && (
              <SheetSection title={t("marketplace.section_after")}>
                <p className="whitespace-pre-line text-sm text-muted-foreground">
                  {entry.postInstallHint}
                </p>
              </SheetSection>
            )}

            <SheetSection title={t("marketplace.section_trust")}>
              <div className="flex items-start gap-2.5 rounded-lg border border-warning/20 bg-warning/[0.08] px-3 py-2.5">
                <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
                <p className="text-sm leading-relaxed text-foreground">
                  {t("marketplace.unreviewed_note")}
                </p>
              </div>
              {entry.sourceUrl && (
                <button
                  type="button"
                  onClick={() => openExternalUrl(entry.sourceUrl as string)}
                  className="mt-2 inline-flex items-center gap-1 text-sm font-medium text-accent hover:underline"
                >
                  {t("marketplace.view_source")}
                  <ArrowUpRight className="h-3.5 w-3.5" />
                </button>
              )}
            </SheetSection>

            <SheetSection
              title={t("marketplace.files")}
              icon={<FileText className="h-3.5 w-3.5" />}
            >
              {contents.isLoading && (
                <p className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  {t("marketplace.files_loading")}
                </p>
              )}
              {contents.error && (
                <p className="text-sm text-destructive">{(contents.error as Error).message}</p>
              )}
              {contents.data?.error && (
                <p
                  role="alert"
                  className="mb-2 flex items-start gap-2 rounded-lg border border-warning/20 bg-warning/[0.08] px-3 py-2 text-sm text-foreground"
                >
                  <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warning" />
                  {contents.data.error}
                </p>
              )}
              {contents.data && !(contents.data.error && contents.data.files.length === 0) && (
                <div className="overflow-hidden rounded-lg border border-border">
                  {contents.data.files.length === 0 && (
                    <p className="px-3 py-2.5 text-sm text-muted-foreground">
                      {t("marketplace.files_none")}
                    </p>
                  )}
                  {contents.data.files.map((file) => (
                    <details
                      key={file.path}
                      // Open by default: this is the trust boundary, and a
                      // fold would put the content one click away again.
                      open
                      className="group/file border-b border-border bg-background last:border-b-0"
                    >
                      <summary className="flex cursor-pointer select-none list-none items-center gap-2 px-3 py-2 text-sm font-medium text-foreground hover:bg-secondary [&::-webkit-details-marker]:hidden">
                        <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform group-open/file:rotate-90" />
                        <span className="min-w-0 flex-1 truncate font-mono">{file.path}</span>
                        <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
                          {file.size < 1024
                            ? `${file.size} B`
                            : `${(file.size / 1024).toFixed(1)} kB`}
                        </span>
                      </summary>
                      <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words border-t border-border bg-muted px-3 py-2 font-mono text-xs text-muted-foreground">
                        {file.text}
                        {file.truncated ? `\n${t("marketplace.files_truncated")}` : ""}
                      </pre>
                    </details>
                  ))}
                </div>
              )}
            </SheetSection>
          </div>
        </ScrollArea>
      </aside>
    </div>
  );
}

function SheetSection({
  title,
  icon,
  children,
}: {
  title: string;
  icon?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section>
      <h3 className="mb-2 flex items-center gap-1.5 text-sm font-semibold text-foreground-strong">
        {icon}
        {title}
      </h3>
      {children}
    </section>
  );
}

/** The one action an entry offers right now, at the top of its sheet. */
function SheetAction({
  entry,
  onInstall,
  onOpenHome,
  installing,
  installError,
  t,
}: {
  entry: Entry;
  onInstall: () => void;
  onOpenHome: () => void;
  installing: boolean;
  installError: string | null;
  t: Translate;
}) {
  const home = entry.kind === "plugin" ? t("marketplace.filter_plugins") : t("marketplace.filter_skills");
  let action: React.ReactNode;
  if (entry.broken) {
    action = (
      <p className="flex items-start gap-2 text-sm text-muted-foreground">
        <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        {entry.problem ?? t("marketplace.entry_unreadable")}
      </p>
    );
  } else if (entry.installed && !entry.updateAvailable) {
    action = (
      <div className="flex flex-wrap items-center gap-3">
        <Badge variant="success">
          <Check />
          {t("marketplace.installed")}
        </Badge>
        <Button size="sm" variant="outline" onClick={onOpenHome}>
          {fill(t("marketplace.open_in"), { section: home })}
          <ArrowUpRight className="ml-1.5 h-3.5 w-3.5" />
        </Button>
      </div>
    );
  } else {
    action = (
      <div className="flex flex-wrap items-center gap-3">
        <Button onClick={onInstall} disabled={installing} className="min-w-[120px]">
          {installing && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
          {entry.updateAvailable
            ? fill(t("marketplace.update_to"), { version: entry.version ?? "" })
            : t("marketplace.install")}
        </Button>
        {entry.updateAvailable && entry.installedVersion && (
          <span className="text-sm text-muted-foreground">
            {fill(t("marketplace.installed_version"), { version: entry.installedVersion })}
          </span>
        )}
      </div>
    );
  }
  return (
    <div className="space-y-2">
      {action}
      {installError && (
        <p
          role="alert"
          className="flex items-start gap-2 rounded-lg border border-destructive/20 bg-destructive/[0.08] px-3 py-2 text-sm text-destructive"
        >
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          {installError}
        </p>
      )}
    </div>
  );
}

/**
 * What installing this would actually reach.
 *
 * A plugin is a door to somebody else's service: the hosted URL its requests
 * and access token go to, or the command it would run on this computer. The
 * plugin consent dialog has always said this verbatim, and now that the sheet
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

  if (rows.length === 0) return null;

  return (
    <SheetSection
      title={
        entry.kind === "plugin" ? t("marketplace.section_data") : t("marketplace.section_runs")
      }
    >
      <div className="space-y-3">
        {entry.seedConflict && (
          <p className="flex items-start gap-2 rounded-lg border border-warning/20 bg-warning/[0.08] px-3 py-2 text-sm text-foreground">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warning" />
            {t("marketplace.seed_conflict")}
          </p>
        )}
        {rows.map((row) => (
          <div key={row.label}>
            <p className="mb-1 text-sm text-muted-foreground">{row.label}</p>
            {row.code && (
              <code className="block break-all rounded-md border border-border bg-muted px-2.5 py-1.5 font-mono text-sm text-foreground">
                {row.code}
              </code>
            )}
            {row.value && <p className="text-sm font-medium text-foreground">{row.value}</p>}
          </div>
        ))}
      </div>
    </SheetSection>
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
  // A plugin always lands unconnected — that is its next step, not a fault.
  // Only a named problem (or a skill that is not usable) earns the warning.
  const warn = Boolean(result.problem) || (!ready && result.kind !== "plugin");
  return (
    <div className="pointer-events-none absolute inset-x-0 bottom-0 z-50 flex justify-center p-5">
      <div
        role="status"
        className="pointer-events-auto flex w-full max-w-lg items-start gap-3 rounded-xl border border-border-strong bg-popover px-4 py-3 shadow-float"
      >
        <div
          className={cn(
            "grid h-8 w-8 shrink-0 place-items-center rounded-lg",
            warn ? "bg-warning/[0.12] text-warning" : "bg-success/[0.12] text-success",
          )}
        >
          {warn ? <AlertTriangle className="h-4 w-4" /> : <Check className="h-4 w-4" />}
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-base font-medium text-foreground-strong">
            {fill(t("marketplace.landed_title"), { title: result.title ?? result.id ?? "" })}
          </p>
          <p className="text-sm text-muted-foreground">
            {result.problem
              ? result.problem
              : ready || result.kind === "plugin"
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
