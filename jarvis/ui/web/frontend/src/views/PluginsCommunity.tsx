import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowUpCircle,
  Check,
  ExternalLink,
  FileText,
  Globe,
  HardDrive,
  LayoutGrid,
  List as ListIcon,
  Loader2,
  Plug,
  RefreshCw,
  Search,
  Terminal,
  Trash2,
  UploadCloud,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { BrandedSelect } from "@/components/ui/select";
import {
  InstallStandard,
  type InstallStandardWire,
} from "@/components/InstallStandard";
import { cn } from "@/lib/utils";
import { openExternalUrl } from "@/lib/openExternal";
import { PRODUCT_NAME } from "@/lib/branding";

// ---------------------------------------------------------------------------
// Community marketplace tab.
//
// Everything here is UNREVIEWED third-party content: the registry auto-merges
// submissions that pass automated checks, so the consent dialog below is the
// trust boundary — it states in plain language what a card would be allowed to
// do, and shows verbatim where data would flow (hosted MCP URL) or what command
// would run (stdio argv) BEFORE anything is installed.
//
// Layout follows what every comparable store converged on (Raycast, Obsidian,
// LobeHub, Smithery, ClawHub): browse controls -> card grid -> detail view, one
// primary action per card, and exactly one headline fact per card rather than a
// row of numbers. We deliberately show NO install count and NO rating: we
// cannot measure either without routing traffic through a server we do not run,
// and a fabricated number is worse than a missing one.
// ---------------------------------------------------------------------------

/** The storefront's submit page — where "Publish your own" sends authors. */
export const MARKETPLACE_SUBMIT_URL = "https://personaljarvis.ai/marketplace/submit";

// Wire types — mirror /api/marketplace/community (see marketplace_routes.py).

/** Re-exported for the tests and views that import the wire shape from here;
 *  it is defined next to the component that renders it. */
export type { InstallStandardWire };

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
  update_available?: boolean;
  seed_conflict?: boolean;
  has_usage_card?: boolean;
  post_install_hint_md?: string | null;
  install?: InstallStandardWire | null;
  /** Skills the package carries (Agent Plugins `skills/`). Installing the
   *  plugin writes each one into the user's skills folder, so the consent
   *  dialog must name them BEFORE anything is written. */
  bundled_skills?: string[];
}

export interface CommunitySkillWire {
  name: string;
  title: string;
  description: string;
  publisher?: string | null;
  version?: string | null;
  published_at?: string | null;
  categories: string[];
  source_url?: string | null;
  raw_url?: string | null;
  /** The install can actually run: the index carries the SKILL.md itself
   *  (`embedded`) or at least a download link. Neither means the registry
   *  published an incomplete entry — say so on the card, don't fail on the
   *  button. */
  installable?: boolean;
  embedded?: boolean;
  installed: boolean;
  installed_version?: string | null;
  update_available?: boolean;
  install?: InstallStandardWire | null;
}

export interface CommunityCategoryWire {
  name: string;
  count: number;
}

export interface CommunityResponse {
  status: "fresh" | "fetched" | "stale" | "unavailable" | "disabled";
  revision?: number | null;
  generated_at?: string | null;
  plugins: CommunityPluginWire[];
  skills: CommunitySkillWire[];
  // The payload also carries a `wallpapers` array. DELIBERATELY not typed or
  // rendered here: wallpapers are browsed on the storefront website (whose
  // "Add to Personal Jarvis" button calls the local install route) and via
  // `jarvis marketplace install <name>` — the picker's own Community tab is
  // future work, not an oversight to "fix" by ad-hoc adding the field.
  categories?: CommunityCategoryWire[];
}

async function fetchCommunity(): Promise<CommunityResponse> {
  const res = await fetch("/api/marketplace/community", { cache: "no-store" });
  if (!res.ok) throw new Error(`Community index request failed (${res.status})`);
  return res.json();
}

const AUTH_MODE_LABEL: Record<string, string> = {
  pat_paste: "Personal access token",
  oauth_device_flow: "Device sign-in",
  hosted_mcp_oauth_dcr: "OAuth sign-in",
  oauth_pkce_loopback: "OAuth sign-in",
  hosted_mcp_allowlist: "Account allowlist",
};

type SortId = "recent" | "name";
type LayoutId = "grid" | "list";

// No "Most popular" on purpose: we cannot measure installs without routing
// traffic through a server we do not run, and a sort order backed by an
// invented number would be the one lie the whole store is judged on.
const SORT_OPTIONS = [
  { value: "recent", label: "Recently published" },
  { value: "name", label: "Name (A–Z)" },
] as const satisfies readonly { value: SortId; label: string }[];

/** Newest first. Entries without a date sort last rather than pretending to be
 *  old — an unpublished date is unknown, not zero. */
function byRecent(a: string | null | undefined, b: string | null | undefined): number {
  if (a === b) return 0;
  if (!a) return 1;
  if (!b) return -1;
  return a < b ? 1 : -1;
}

/** "3 months ago" from an ISO date, or null when there is nothing to say. */
function relativeDate(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return null;
  const days = Math.floor((Date.now() - then) / 86_400_000);
  if (days < 0) return null;
  if (days === 0) return "today";
  if (days === 1) return "yesterday";
  if (days < 30) return `${days}d ago`;
  if (days < 365) return `${Math.floor(days / 30)}mo ago`;
  return `${Math.floor(days / 365)}y ago`;
}

export function CommunityTab() {
  const queryClient = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: ["marketplace-community"],
    queryFn: fetchCommunity,
  });
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState<string>("all");
  const [sort, setSort] = useState<SortId>("recent");
  const [layout, setLayout] = useState<LayoutId>("grid");
  const [installedOnly, setInstalledOnly] = useState(false);
  const [consentPlugin, setConsentPlugin] = useState<CommunityPluginWire | null>(null);
  const [consentSkill, setConsentSkill] = useState<CommunitySkillWire | null>(null);
  // One line about what the last action did to the disk. Cleared on the next
  // one; there is no toast system in this app and inventing one for a single
  // sentence would be worse than a line in the header.
  const [notice, setNotice] = useState<string | null>(null);
  const [detail, setDetail] = useState<CommunityPluginWire | null>(null);
  const [skillDetail, setSkillDetail] = useState<CommunitySkillWire | null>(null);

  const invalidateAll = () => {
    // An installed community plugin is a first-class store card and an
    // installed skill joins the Skill Finder pool — every list that could show
    // it must re-read, not just the one we were looking at.
    queryClient.invalidateQueries({ queryKey: ["marketplace-community"] });
    queryClient.invalidateQueries({ queryKey: ["marketplace-plugins"] });
    queryClient.invalidateQueries({ queryKey: ["skills"] });
  };

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
      return (await res.json()) as { plugin?: { installed_skills?: string[] } };
    },
    onSuccess: (result, name) => {
      setConsentPlugin(null);
      const added = result.plugin?.installed_skills ?? [];
      setNotice(
        added.length > 0
          ? `Installed ${name} and added ${added.length === 1 ? "1 skill" : `${added.length} skills`}: ${added.join(", ")}. They start as drafts.`
          : null,
      );
      invalidateAll();
    },
  });

  const uninstallMutation = useMutation({
    mutationFn: async (name: string) => {
      const res = await fetch(
        `/api/marketplace/community/plugins/${encodeURIComponent(name)}`,
        { method: "DELETE" },
      );
      if (!res.ok) {
        const detail = await res
          .json()
          .then((body: { detail?: string }) => body.detail)
          .catch(() => undefined);
        throw new Error(detail ?? `Remove failed (${res.status})`);
      }
      return (await res.json()) as { removed_skills?: string[] };
    },
    onSuccess: (result, name) => {
      // Close the sheet only if it shows the removed plugin — removing B
      // from a list row must not close an open sheet for A.
      setDetail((d) => (d && d.name === name ? null : d));
      // Files left the disk. Say which — a silent deletion is correct
      // behaviour that looks like a bug.
      const gone = result.removed_skills ?? [];
      setNotice(
        gone.length > 0
          ? `Removed ${name} and ${gone.length === 1 ? "its skill" : `its ${gone.length} skills`}: ${gone.join(", ")}.`
          : null,
      );
      invalidateAll();
    },
  });

  const skillInstallMutation = useMutation({
    mutationFn: async (skill: CommunitySkillWire) => {
      // The name is the whole request: the server reads the same index entry
      // the card was rendered from, prefers the embedded SKILL.md, and falls
      // back to the download only for older feeds. Sending a URL from here
      // would make "install this skill" mean "write whatever that URL serves".
      const res = await fetch(
        `/api/marketplace/community/skills/${encodeURIComponent(skill.name)}/install`,
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
      invalidateAll();
    },
  });

  const skillRemoveMutation = useMutation({
    mutationFn: async (name: string) => {
      const res = await fetch(`/api/skills/${encodeURIComponent(name)}`, {
        method: "DELETE",
      });
      if (!res.ok) throw new Error(`Remove failed (${res.status})`);
      return res.json();
    },
    onSuccess: (_result, name) => {
      // Close the sheet only if it shows the removed skill — removing B
      // from a list row must not close an open sheet for A.
      setSkillDetail((d) => (d && d.name === name ? null : d));
      invalidateAll();
    },
  });

  const q = query.trim().toLowerCase();
  const categories = data?.categories ?? [];

  const plugins = useMemo(() => {
    const list = (data?.plugins ?? []).filter((p) => {
      if (installedOnly && !p.installed) return false;
      if (category !== "all" && p.category !== category) return false;
      if (!q) return true;
      return (
        p.name.toLowerCase().includes(q) ||
        (p.display_name ?? "").toLowerCase().includes(q) ||
        (p.description ?? "").toLowerCase().includes(q) ||
        (p.publisher ?? "").toLowerCase().includes(q)
      );
    });
    return [...list].sort((a, b) =>
      sort === "name"
        ? (a.display_name ?? a.name).localeCompare(b.display_name ?? b.name)
        : byRecent(a.published_at, b.published_at),
    );
  }, [data, q, category, sort, installedOnly]);

  const skills = useMemo(() => {
    // Skills carry free-form categories that do not share the plugin taxonomy,
    // so the category chips deliberately do not filter them — a chip that
    // silently emptied the skills section would read as "no skills exist".
    const list = (data?.skills ?? []).filter((s) => {
      if (installedOnly && !s.installed) return false;
      if (!q) return true;
      return (
        s.name.toLowerCase().includes(q) ||
        s.title.toLowerCase().includes(q) ||
        s.description.toLowerCase().includes(q) ||
        (s.publisher ?? "").toLowerCase().includes(q)
      );
    });
    return [...list].sort((a, b) =>
      sort === "name"
        ? a.title.localeCompare(b.title)
        : byRecent(a.published_at, b.published_at),
    );
  }, [data, q, sort, installedOnly]);

  const totalShown = plugins.length + skills.length;
  const totalAvailable = (data?.plugins.length ?? 0) + (data?.skills.length ?? 0);
  const updateCount =
    (data?.plugins ?? []).filter((p) => p.update_available).length +
    (data?.skills ?? []).filter((s) => s.update_available).length;
  const filtersActive = Boolean(q) || category !== "all" || installedOnly;

  const clearFilters = () => {
    setQuery("");
    setCategory("all");
    setInstalledOnly(false);
  };

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <h2 className="font-display text-base font-semibold tracking-tight text-foreground">
            Community marketplace
          </h2>
          <p className="text-xs text-muted-foreground">
            Plugins and skills published by anyone. Nothing here is reviewed by
            the {PRODUCT_NAME} team — read what a card would connect to before
            installing it.
          </p>
        </div>
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
          aria-label="Refresh the community index"
        >
          <RefreshCw
            className={cn("h-3.5 w-3.5", refreshMutation.isPending && "animate-spin")}
          />
        </Button>
      </header>

      <StatusNotice status={data?.status} error={error} isLoading={isLoading} />

      {updateCount > 0 && (
        <p className="flex items-center gap-2 border-l-2 border-primary py-1 pl-3 text-xs text-foreground">
          <ArrowUpCircle className="h-3.5 w-3.5 shrink-0 text-primary" />
          {updateCount === 1
            ? "1 installed item has a newer version."
            : `${updateCount} installed items have newer versions.`}
        </p>
      )}

      {notice && (
        <p
          role="status"
          className="flex items-start gap-2 border-l-2 border-border py-1 pl-3 text-xs text-muted-foreground"
        >
          <FileText className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          {notice}
        </p>
      )}

      {totalAvailable > 0 && (
        <BrowseControls
          query={query}
          setQuery={setQuery}
          categories={categories}
          category={category}
          setCategory={setCategory}
          sort={sort}
          setSort={setSort}
          layout={layout}
          setLayout={setLayout}
          installedOnly={installedOnly}
          setInstalledOnly={setInstalledOnly}
          shown={totalShown}
          total={totalAvailable}
        />
      )}

      {plugins.length > 0 && (
        <Section title="Plugins" count={plugins.length}>
          <div
            className={cn(
              layout === "grid"
                ? "relative sm:grid sm:grid-cols-2 sm:gap-x-12 sm:before:absolute sm:before:inset-y-3 sm:before:left-1/2 sm:before:w-px sm:before:bg-border/60 sm:before:content-['']"
                : "flex flex-col",
            )}
          >
            {plugins.map((p) => (
              <CommunityPluginRow
                key={p.name}
                plugin={p}
                layout={layout}
                onOpen={() => p.valid && setDetail(p)}
                onInstall={() => setConsentPlugin(p)}
                onUninstall={() => uninstallMutation.mutate(p.name)}
                busy={
                  (uninstallMutation.isPending &&
                    uninstallMutation.variables === p.name) ||
                  (installMutation.isPending && installMutation.variables === p.name)
                }
              />
            ))}
          </div>
        </Section>
      )}

      {skills.length > 0 && (
        <Section title="Skills" count={skills.length}>
          <div
            className={cn(
              layout === "grid"
                ? "relative sm:grid sm:grid-cols-2 sm:gap-x-12 sm:before:absolute sm:before:inset-y-3 sm:before:left-1/2 sm:before:w-px sm:before:bg-border/60 sm:before:content-['']"
                : "flex flex-col",
            )}
          >
            {skills.map((s) => (
              <CommunitySkillRow
                key={s.name}
                skill={s}
                layout={layout}
                onOpen={() => setSkillDetail(s)}
                onInstall={() => setConsentSkill(s)}
                onRemove={() => skillRemoveMutation.mutate(s.name)}
                busy={
                  (skillInstallMutation.isPending &&
                    skillInstallMutation.variables?.name === s.name) ||
                  (skillRemoveMutation.isPending &&
                    skillRemoveMutation.variables === s.name)
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
        </Section>
      )}

      {!isLoading && totalAvailable > 0 && totalShown === 0 && (
        <EmptyState
          title="Nothing matches"
          body={
            filtersActive
              ? "No plugin or skill matches the current search and filters."
              : "Nothing to show on this view right now."
          }
          action={
            filtersActive ? (
              <Button size="sm" variant="outline" onClick={clearFilters}>
                Clear filters
              </Button>
            ) : null
          }
        />
      )}

      {!isLoading &&
        totalAvailable === 0 &&
        data?.status !== "disabled" &&
        data?.status !== "unavailable" && (
          <EmptyState
            title="A quiet shelf"
            body="No community plugins or skills are published yet. Be the first — anything that passes the automated checks is listed automatically."
            action={
              <Button
                size="sm"
                variant="outline"
                onClick={() => openExternalUrl(MARKETPLACE_SUBMIT_URL)}
              >
                <UploadCloud className="mr-1.5 h-3.5 w-3.5" />
                Publish your own
              </Button>
            }
          />
        )}

      {detail && (
        <PluginDetailSheet
          plugin={detail}
          onClose={() => setDetail(null)}
          onInstall={() => {
            setDetail(null);
            setConsentPlugin(detail);
          }}
          onUninstall={() => uninstallMutation.mutate(detail.name)}
          uninstalling={
            uninstallMutation.isPending && uninstallMutation.variables === detail.name
          }
        />
      )}

      {skillDetail && (
        <SkillDetailSheet
          skill={skillDetail}
          onClose={() => setSkillDetail(null)}
          onInstall={() => {
            setSkillDetail(null);
            setConsentSkill(skillDetail);
          }}
          onRemove={() => skillRemoveMutation.mutate(skillDetail.name)}
          removing={
            skillRemoveMutation.isPending &&
            skillRemoveMutation.variables === skillDetail.name
          }
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

function Section({
  title,
  count,
  children,
}: {
  title: string;
  count: number;
  children: React.ReactNode;
}) {
  // The same register header the main catalogue uses: title on a rule with a
  // tabular count at the far end — no pill, no box.
  return (
    <section>
      <div className="mb-1.5 flex items-baseline gap-3">
        <h3 className="font-display text-xs font-semibold uppercase tracking-[0.18em] text-foreground">
          {title}
        </h3>
        <span aria-hidden className="h-px flex-1 self-center bg-border" />
        <span className="font-mono text-[10px] tabular-nums text-muted-foreground">
          {count}
        </span>
      </div>
      {children}
    </section>
  );
}

/** Search, category chips, sort and layout — the whole filter surface in one
 *  strip, so browsing never requires leaving the list. */
function BrowseControls({
  query,
  setQuery,
  categories,
  category,
  setCategory,
  sort,
  setSort,
  layout,
  setLayout,
  installedOnly,
  setInstalledOnly,
  shown,
  total,
}: {
  query: string;
  setQuery: (value: string) => void;
  categories: CommunityCategoryWire[];
  category: string;
  setCategory: (value: string) => void;
  sort: SortId;
  setSort: (value: SortId) => void;
  layout: LayoutId;
  setLayout: (value: LayoutId) => void;
  installedOnly: boolean;
  setInstalledOnly: (value: boolean) => void;
  shown: number;
  total: number;
}) {
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <label className="group flex min-w-[12rem] flex-1 items-center gap-2.5 border-b border-border pb-2 transition-colors focus-within:border-primary">
          <Search className="h-3.5 w-3.5 shrink-0 text-muted-foreground transition-colors group-focus-within:text-primary" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search by name, description or publisher…"
            aria-label="Search community plugins and skills"
            className="w-full bg-transparent text-sm text-foreground outline-none placeholder:text-muted-foreground/60"
          />
          {query && (
            <button
              type="button"
              onClick={() => setQuery("")}
              aria-label="Clear search"
              className="shrink-0 rounded-full p-0.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              <X className="h-3 w-3" />
            </button>
          )}
        </label>

        <BrandedSelect
          value={sort}
          options={SORT_OPTIONS}
          onValueChange={(value) => setSort(value as SortId)}
          ariaLabel="Sort order"
          className="w-44 shrink-0"
        />

        <div className="flex shrink-0 items-center gap-0.5">
          <LayoutButton
            active={layout === "grid"}
            label="Grid view"
            onClick={() => setLayout("grid")}
          >
            <LayoutGrid className="h-3.5 w-3.5" />
          </LayoutButton>
          <LayoutButton
            active={layout === "list"}
            label="List view"
            onClick={() => setLayout("list")}
          >
            <ListIcon className="h-3.5 w-3.5" />
          </LayoutButton>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-1.5">
        <Chip active={category === "all" && !installedOnly} onClick={() => {
          setCategory("all");
          setInstalledOnly(false);
        }}>
          All <ChipCount>{total}</ChipCount>
        </Chip>
        <Chip active={installedOnly} onClick={() => setInstalledOnly(!installedOnly)}>
          Installed
        </Chip>
        {categories.map((c) => (
          <Chip
            key={c.name}
            active={category === c.name}
            onClick={() => setCategory(category === c.name ? "all" : c.name)}
          >
            {c.name} <ChipCount>{c.count}</ChipCount>
          </Chip>
        ))}
        {shown !== total && (
          <span className="ml-auto text-[11px] tabular-nums text-muted-foreground">
            {shown} of {total}
          </span>
        )}
      </div>
    </div>
  );
}

function LayoutButton({
  active,
  label,
  onClick,
  children,
}: {
  active: boolean;
  label: string;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      aria-pressed={active}
      title={label}
      className={cn(
        "grid h-6 w-6 place-items-center border-b-2 transition-colors",
        active
          ? "border-primary text-foreground"
          : "border-transparent text-muted-foreground hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}

function Chip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      // Ink, not pills: an active filter is underlined in gold the way an
      // index entry gets a pencil mark — the label itself is the control.
      className={cn(
        "inline-flex items-baseline gap-1.5 border-b pb-0.5 text-[11px] font-medium uppercase tracking-wider transition-colors",
        active
          ? "border-primary text-foreground"
          : "border-transparent text-muted-foreground hover:border-border hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}

function ChipCount({ children }: { children: React.ReactNode }) {
  return (
    <span className="font-mono text-[10px] tabular-nums text-muted-foreground/70">{children}</span>
  );
}

/** A named empty state rather than a blank panel — a thin catalog should read
 *  as "nothing here yet", never as "the page is broken". */
function EmptyState({
  title,
  body,
  action,
}: {
  title: string;
  body: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="border-y border-border/70 px-6 py-10 text-center">
      <span aria-hidden className="mx-auto mb-4 block h-1.5 w-1.5 rotate-45 bg-primary" />
      <p className="font-display text-sm font-semibold text-foreground">{title}</p>
      <p className="mx-auto mt-1 max-w-sm text-xs text-muted-foreground">{body}</p>
      {action && <div className="mt-4">{action}</div>}
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
        "flex items-start gap-2 border-l-2 py-1 pl-3 text-xs",
        tone === "muted" && "border-border text-muted-foreground",
        tone === "warn" && "border-amber-500 text-amber-500",
        tone === "error" && "border-destructive text-destructive",
      )}
    >
      {tone !== "muted" && <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />}
      <span>{children}</span>
    </p>
  );
}

/** Coloured brand tile with monogram fallback — the same three-tier idea as
 *  the main store, minus bundled assets (community brands ship none). */
function CommunityTile({
  plugin,
  size = "md",
}: {
  plugin: CommunityPluginWire;
  size?: "md" | "lg";
}) {
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
      className={cn(
        "grid shrink-0 place-items-center overflow-hidden rounded-md border border-border/60",
        size === "lg" ? "h-14 w-14" : "h-10 w-10",
      )}
      style={{ backgroundColor: tile }}
    >
      {failed || !src ? (
        <span
          className={cn(
            "font-semibold text-white/90",
            size === "lg" ? "text-xl" : "text-sm",
          )}
        >
          {name.slice(0, 1).toUpperCase()}
        </span>
      ) : (
        <img
          src={src}
          alt=""
          className={cn(size === "lg" ? "h-7 w-7" : "h-5 w-5")}
          loading="lazy"
          onError={() => setFailed(true)}
        />
      )}
    </div>
  );
}

/** The one badge every community entry carries. Kept as its own component so
 *  the wording stays identical on the card, in the detail view and in the
 *  consent dialog — three different phrasings would read as three different
 *  levels of risk. */
function NotReviewedBadge() {
  return (
    <span className="shrink-0 text-[9px] font-medium uppercase tracking-wider text-amber-500/80">
      Community · not reviewed
    </span>
  );
}

function CommunityPluginRow({
  plugin,
  layout,
  onOpen,
  onInstall,
  onUninstall,
  busy,
}: {
  plugin: CommunityPluginWire;
  layout: LayoutId;
  onOpen: () => void;
  onInstall: () => void;
  onUninstall: () => void;
  busy: boolean;
}) {
  const name = plugin.display_name ?? plugin.name;
  if (!plugin.valid) {
    return (
      <article className="flex items-center gap-3 border-b border-border/70 py-3 pl-0.5 pr-0.5 opacity-70">
        <div className="grid h-10 w-10 shrink-0 place-items-center rounded-md border border-border/60 bg-muted">
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
  const updated = relativeDate(plugin.published_at);
  return (
    <article
      className={cn(
        "group relative flex gap-3 border-b py-3 pl-0.5 pr-0.5 transition-[colors,box-shadow]",
        layout === "grid" ? "flex-col" : "items-center",
        plugin.installed
          ? "border-primary/40"
          : "border-border/70 hover:border-primary/60",
      )}
    >
      <div className={cn("flex min-w-0 gap-3", layout === "grid" && "w-full")}>
        <CommunityTile plugin={plugin} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
            <button
              type="button"
              onClick={onOpen}
              className="min-w-0 max-w-full truncate text-left text-sm font-semibold tracking-tight text-foreground outline-none hover:text-primary focus-visible:text-primary"
            >
              {name}
            </button>
            <NotReviewedBadge />
            {plugin.installed && (
              <span className="shrink-0 text-[9px] font-medium uppercase tracking-wider text-primary">
                · Installed
              </span>
            )}
          </div>
          <p
            className={cn(
              "text-xs text-muted-foreground",
              layout === "grid" ? "line-clamp-2" : "truncate",
            )}
            title={plugin.description}
          >
            {plugin.description}
          </p>
          <p className="truncate text-[11px] text-muted-foreground/70">
            {plugin.publisher ? `by ${plugin.publisher}` : "unknown publisher"}
            {plugin.version ? ` · v${plugin.version}` : ""}
            {plugin.category ? ` · ${plugin.category}` : ""}
            {updated ? ` · ${updated}` : ""}
          </p>
        </div>
      </div>

      <div
        className={cn(
          "flex shrink-0 items-center gap-1",
          layout === "grid" && "w-full justify-end border-t border-border/50 pt-2",
        )}
      >
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
          <>
            {plugin.update_available && (
              <Button size="sm" variant="outline" onClick={onInstall} disabled={busy}>
                <ArrowUpCircle className="mr-1.5 h-3.5 w-3.5" />
                Update
              </Button>
            )}
            <Button
              size="sm"
              variant="ghost"
              onClick={onUninstall}
              disabled={busy}
              title="Remove this plugin and its stored access"
              aria-label={`Remove ${name}`}
            >
              {busy ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Trash2 className="h-3.5 w-3.5" />
              )}
            </Button>
          </>
        ) : plugin.seed_conflict ? (
          <span
            className="shrink-0 text-[10px] text-muted-foreground"
            title="A built-in plugin already uses this name"
          >
            Name taken
          </span>
        ) : (
          <Button size="sm" variant="outline" onClick={onInstall} disabled={busy}>
            Install
          </Button>
        )}
      </div>
    </article>
  );
}

function CommunitySkillRow({
  skill,
  layout,
  onOpen,
  onInstall,
  onRemove,
  busy,
  installError,
}: {
  skill: CommunitySkillWire;
  layout: LayoutId;
  onOpen: () => void;
  onInstall: () => void;
  onRemove: () => void;
  busy: boolean;
  installError: string | null;
}) {
  const updated = relativeDate(skill.published_at);
  return (
    <article
      className={cn(
        "flex gap-3 border-b py-3 pl-0.5 pr-0.5 transition-colors",
        layout === "grid" ? "flex-col" : "items-center",
        skill.installed
          ? "border-primary/40"
          : "border-border/70 hover:border-primary/60",
      )}
    >
      <div className="flex min-w-0 flex-1 gap-3">
        <div className="grid h-10 w-10 shrink-0 place-items-center rounded-md border border-border/60 bg-muted/60">
          <FileText className="h-4 w-4 text-muted-foreground" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
            <button
              type="button"
              onClick={onOpen}
              className="min-w-0 max-w-full truncate text-left text-sm font-semibold tracking-tight text-foreground outline-none hover:text-primary focus-visible:text-primary"
            >
              {skill.title}
            </button>
            <NotReviewedBadge />
            {skill.installed && (
              <span className="shrink-0 text-[9px] font-medium uppercase tracking-wider text-primary">
                · Installed
              </span>
            )}
          </div>
          <p
            className={cn(
              "text-xs text-muted-foreground",
              layout === "grid" ? "line-clamp-2" : "truncate",
            )}
            title={skill.description}
          >
            {skill.description}
          </p>
          <p className="truncate text-[11px] text-muted-foreground/70">
            {skill.publisher ? `by ${skill.publisher}` : "unknown publisher"}
            {skill.version ? ` · v${skill.version}` : ""}
            {updated ? ` · ${updated}` : ""}
            {installError ? ` · ${installError}` : ""}
          </p>
        </div>
      </div>

      <div
        className={cn(
          "flex shrink-0 items-center gap-1",
          layout === "grid" && "w-full justify-end border-t border-border/50 pt-2",
        )}
      >
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
          <>
            {skill.update_available && skillIsInstallable(skill) && (
              <Button size="sm" variant="outline" onClick={onInstall} disabled={busy}>
                <ArrowUpCircle className="mr-1.5 h-3.5 w-3.5" />
                Update
              </Button>
            )}
            <span className="inline-flex shrink-0 items-center gap-1 text-[10px] font-medium uppercase tracking-wider text-primary">
              <Check className="h-3 w-3" /> Installed
            </span>
            <Button
              size="sm"
              variant="ghost"
              onClick={onRemove}
              disabled={busy}
              title="Remove this skill from your skills folder"
              aria-label={`Remove ${skill.title}`}
            >
              {busy ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Trash2 className="h-3.5 w-3.5" />
              )}
            </Button>
          </>
        ) : skillIsInstallable(skill) ? (
          <Button size="sm" variant="outline" onClick={onInstall} disabled={busy}>
            {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : "Install"}
          </Button>
        ) : (
          <span
            className="shrink-0 text-[10px] text-muted-foreground"
            title="No direct download — open the source and follow its steps"
          >
            Manual
          </span>
        )}
      </div>
    </article>
  );
}

/** Whether the Install button can do anything.
 *
 *  A skill arrives either embedded in the feed (preferred) or as a download
 *  link. `installable` is computed server-side; the `raw_url` fallback keeps
 *  a client pointed at an older index working. Never test `raw_url` alone —
 *  that reads an embedded skill as "manual install" and hides the button
 *  that would have worked. */
function skillIsInstallable(skill: CommunitySkillWire): boolean {
  return skill.installable ?? Boolean(skill.raw_url);
}

/** What a plugin would be ALLOWED to do, in plain language.
 *
 *  Derived from the manifest rather than written per plugin, so it cannot go
 *  stale and cannot be softened by a publisher. A hosted server sees the data
 *  we send it; a local command inherits everything this app can do — which is
 *  the honest, uncomfortable sentence a store must say out loud. */
function capabilityLines(plugin: CommunityPluginWire): { icon: React.ReactNode; text: string }[] {
  const mcp = plugin.mcp_server ?? null;
  const name = plugin.display_name ?? plugin.name;
  const lines: { icon: React.ReactNode; text: string }[] = [];

  if (mcp?.transport === "http") {
    lines.push(
      {
        icon: <Globe className="h-3.5 w-3.5" />,
        text: `Sends the data you ask it to work with — and your ${name} access token — to the address below.`,
      },
      {
        icon: <Plug className="h-3.5 w-3.5" />,
        text: "Runs no code on your computer; the tools live on that server.",
      },
    );
  } else if (mcp?.transport === "stdio") {
    lines.push(
      {
        icon: <Terminal className="h-3.5 w-3.5" />,
        text: "Runs the command below on your computer, with the same access this app has.",
      },
      {
        icon: <HardDrive className="h-3.5 w-3.5" />,
        text: "Can read and change files on your computer and connect to the internet.",
      },
    );
  } else if (!(plugin.bundled_skills?.length)) {
    lines.push({
      icon: <FileText className="h-3.5 w-3.5" />,
      text: "Metadata only — it adds no server and runs no command.",
    });
  }

  // Installing a package with skills WRITES FILES. That has to be on the
  // consent list, not discovered afterwards in the Skills view.
  const skillCount = plugin.bundled_skills?.length ?? 0;
  if (skillCount > 0) {
    lines.push({
      icon: <FileText className="h-3.5 w-3.5" />,
      text:
        skillCount === 1
          ? "Writes one skill file into your skills folder — instructions the assistant reads, not code it runs."
          : `Writes ${skillCount} skill files into your skills folder — instructions the assistant reads, not code it runs.`,
    });
  }
  return lines;
}

/** The detail view: everything the index knows about one plugin, on one screen.
 *
 *  Deliberately a sheet rather than a route — the store lives inside a tab of
 *  the Plugins view, so a URL-addressable detail page would need a nav enum
 *  entry and a deep-link contract for a panel the user closes in two seconds. */
export function PluginDetailSheet({
  plugin,
  onClose,
  onInstall,
  onUninstall,
  uninstalling,
}: {
  plugin: CommunityPluginWire;
  onClose: () => void;
  onInstall: () => void;
  onUninstall: () => void;
  uninstalling: boolean;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const name = plugin.display_name ?? plugin.name;
  const mcp = plugin.mcp_server ?? null;
  const authLabel = plugin.auth ? AUTH_MODE_LABEL[plugin.auth.mode] : undefined;
  const published = relativeDate(plugin.published_at);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="community-detail-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-scrim/70 p-4 backdrop-blur-sm"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="relative flex max-h-[85vh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl border border-border bg-card shadow-[0_20px_60px_rgba(0,0,0,0.6)]">
        <header className="flex items-start gap-4 border-b border-border px-6 py-5">
          <CommunityTile plugin={plugin} size="lg" />
          <div className="min-w-0 flex-1">
            <h2
              id="community-detail-title"
              className="font-display text-lg font-semibold tracking-tight text-foreground"
            >
              {name}
            </h2>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {plugin.publisher ? `by ${plugin.publisher}` : "unknown publisher"}
              {plugin.version ? ` · v${plugin.version}` : ""}
              {published ? ` · published ${published}` : ""}
            </p>
            <div className="mt-1.5">
              <NotReviewedBadge />
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="grid h-7 w-7 shrink-0 place-items-center rounded-full text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            <X className="h-4 w-4" />
          </button>
        </header>

        <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-6 py-5 text-sm">
          <p className="text-muted-foreground">{plugin.description}</p>

          <section>
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-foreground">
              What it is allowed to do
            </h3>
            <ul className="space-y-1.5">
              {capabilityLines(plugin).map((line, i) => (
                <li key={i} className="flex items-start gap-2 text-xs text-muted-foreground">
                  <span className="mt-0.5 shrink-0 text-muted-foreground">{line.icon}</span>
                  <span>{line.text}</span>
                </li>
              ))}
            </ul>
          </section>

          {mcp?.transport === "http" && mcp.url && (
            <Field label="Server address">{mcp.url}</Field>
          )}
          {mcp?.transport === "stdio" && (
            <Field label="Command that runs locally">
              {(mcp.install ?? []).join(" ")}
            </Field>
          )}

          {plugin.install && <InstallStandard install={plugin.install} />}

          <dl className="grid gap-x-6 gap-y-2 sm:grid-cols-2">
            <Meta label="Category" value={plugin.category} />
            <Meta label="Sign-in method" value={authLabel} />
            <Meta
              label="Installed version"
              value={plugin.installed_version ? `v${plugin.installed_version}` : undefined}
            />
            <Meta
              label="Keyword card"
              value={plugin.has_usage_card ? "Included" : "None"}
            />
            <Meta
              label="Skills included"
              value={
                plugin.bundled_skills?.length
                  ? plugin.bundled_skills.join(", ")
                  : undefined
              }
            />
          </dl>

          {plugin.update_available && (
            <p className="flex items-start gap-2 rounded-md border border-primary/30 bg-primary/5 px-2.5 py-2 text-xs text-foreground">
              <ArrowUpCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" />
              Version {plugin.version} is available — you have{" "}
              {plugin.installed_version ?? "an older version"}.
            </p>
          )}
        </div>

        <footer className="flex flex-wrap items-center justify-end gap-2 border-t border-border px-6 py-4">
          {plugin.source_url && (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => openExternalUrl(plugin.source_url ?? "")}
            >
              <ExternalLink className="mr-1.5 h-3.5 w-3.5" />
              View source
            </Button>
          )}
          {plugin.installed ? (
            <>
              {plugin.update_available && (
                <Button size="sm" onClick={onInstall}>
                  <ArrowUpCircle className="mr-1.5 h-3.5 w-3.5" />
                  Update
                </Button>
              )}
              <Button
                size="sm"
                variant="outline"
                onClick={onUninstall}
                disabled={uninstalling}
              >
                {uninstalling ? (
                  <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Trash2 className="mr-1.5 h-3.5 w-3.5" />
                )}
                Remove
              </Button>
            </>
          ) : plugin.seed_conflict ? (
            <span className="text-xs text-muted-foreground">
              A built-in plugin already uses this name.
            </span>
          ) : (
            <Button size="sm" onClick={onInstall}>
              Install
            </Button>
          )}
        </footer>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="mb-1 text-xs font-medium text-foreground">{label}</p>
      <code className="block break-all rounded-md border border-border bg-muted/40 px-2 py-1.5 text-xs text-foreground">
        {children}
      </code>
    </div>
  );
}

function Meta({ label, value }: { label: string; value?: string | null }) {
  if (!value) return null;
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-border/40 pb-1.5">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="truncate text-xs font-medium text-foreground">{value}</dd>
    </div>
  );
}

/** The skill detail view — the same sheet plugins get, so the install
 *  standard has a place to live for skills too (a row with two buttons
 *  cannot carry three install surfaces). */
export function SkillDetailSheet({
  skill,
  onClose,
  onInstall,
  onRemove,
  removing,
}: {
  skill: CommunitySkillWire;
  onClose: () => void;
  onInstall: () => void;
  onRemove: () => void;
  removing: boolean;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const published = relativeDate(skill.published_at);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="community-skill-detail-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-scrim/70 p-4 backdrop-blur-sm"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="relative flex max-h-[85vh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl border border-border bg-card shadow-[0_20px_60px_rgba(0,0,0,0.6)]">
        <header className="flex items-start gap-4 border-b border-border px-6 py-5">
          <div className="grid h-14 w-14 shrink-0 place-items-center rounded-lg border border-border/60 bg-muted/60">
            <FileText className="h-6 w-6 text-muted-foreground" />
          </div>
          <div className="min-w-0 flex-1">
            <h2
              id="community-skill-detail-title"
              className="font-display text-lg font-semibold tracking-tight text-foreground"
            >
              {skill.title}
            </h2>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {skill.publisher ? `by ${skill.publisher}` : "unknown publisher"}
              {skill.version ? ` · v${skill.version}` : ""}
              {published ? ` · published ${published}` : ""}
            </p>
            <div className="mt-1.5">
              <NotReviewedBadge />
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="grid h-7 w-7 shrink-0 place-items-center rounded-full text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            <X className="h-4 w-4" />
          </button>
        </header>

        <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-6 py-5 text-sm">
          <p className="text-muted-foreground">{skill.description}</p>

          <section>
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-foreground">
              What it is allowed to do
            </h3>
            <ul className="space-y-1.5">
              <li className="flex items-start gap-2 text-xs text-muted-foreground">
                <span className="mt-0.5 shrink-0 text-muted-foreground">
                  <FileText className="h-3.5 w-3.5" />
                </span>
                <span>
                  A set of instructions the assistant follows once you switch
                  it on — it runs no code by itself.
                </span>
              </li>
            </ul>
          </section>

          {skill.embedded ? (
            <Field label="The instructions come from">
              the marketplace catalogue itself — nothing else is downloaded
            </Field>
          ) : skill.raw_url ? (
            <Field label="The instructions are downloaded from">
              {skill.raw_url}
            </Field>
          ) : null}

          {skill.install && <InstallStandard install={skill.install} />}

          <dl className="grid gap-x-6 gap-y-2 sm:grid-cols-2">
            <Meta
              label="Categories"
              value={skill.categories.length ? skill.categories.join(", ") : undefined}
            />
            <Meta
              label="Installed version"
              value={skill.installed_version ? `v${skill.installed_version}` : undefined}
            />
          </dl>

          {skill.update_available && (
            <p className="flex items-start gap-2 rounded-md border border-primary/30 bg-primary/5 px-2.5 py-2 text-xs text-foreground">
              <ArrowUpCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" />
              Version {skill.version} is available — you have{" "}
              {skill.installed_version ?? "an older version"}.
            </p>
          )}
        </div>

        <footer className="flex flex-wrap items-center justify-end gap-2 border-t border-border px-6 py-4">
          {skill.source_url && (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => openExternalUrl(skill.source_url ?? "")}
            >
              <ExternalLink className="mr-1.5 h-3.5 w-3.5" />
              View source
            </Button>
          )}
          {skill.installed ? (
            <>
              {skill.update_available && skillIsInstallable(skill) && (
                <Button size="sm" onClick={onInstall}>
                  <ArrowUpCircle className="mr-1.5 h-3.5 w-3.5" />
                  Update
                </Button>
              )}
              <Button size="sm" variant="outline" onClick={onRemove} disabled={removing}>
                {removing ? (
                  <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Trash2 className="mr-1.5 h-3.5 w-3.5" />
                )}
                Remove
              </Button>
            </>
          ) : skillIsInstallable(skill) ? (
            <Button size="sm" onClick={onInstall}>
              Install
            </Button>
          ) : (
            <span
              className="text-xs text-muted-foreground"
              title="No direct download — open the source and follow its steps"
            >
              Manual install — see the source.
            </span>
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
      <div className="relative w-full max-w-md overflow-hidden rounded-2xl border border-border bg-card shadow-[0_20px_60px_rgba(0,0,0,0.6)]">
        <header className="border-b border-border px-5 py-4">
          <h2
            id="community-skill-install-title"
            className="font-display text-base font-semibold tracking-tight"
          >
            Install {skill.title}?
          </h2>
          <p className="text-[11px] uppercase tracking-wider text-amber-500/80">
            Community skill · not reviewed
          </p>
        </header>

        <div className="space-y-3 px-5 py-4 text-sm">
          <p className="text-muted-foreground">{skill.description}</p>
          <p className="text-xs text-muted-foreground">
            Published by{" "}
            <span className="text-foreground">
              {skill.publisher ?? "an unknown author"}
            </span>
            {skill.version ? ` · version ${skill.version}` : ""}
          </p>
          {skill.embedded ? (
            <p className="text-xs text-muted-foreground">
              The instructions come from the marketplace catalogue itself —
              installing downloads nothing else.
            </p>
          ) : (
            <div>
              <p className="mb-1 text-xs font-medium text-foreground">
                The instructions are downloaded from:
              </p>
              <code className="block break-all rounded-md border border-border bg-muted/40 px-2 py-1.5 text-xs text-foreground">
                {skill.raw_url}
              </code>
            </div>
          )}
          <p className="text-xs text-muted-foreground">
            A skill is a set of instructions the assistant follows. Read the
            installed instructions before you switch it on.
          </p>
          {errorMessage && (
            <p className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/5 px-2 py-1.5 text-xs text-destructive">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              {errorMessage}
            </p>
          )}
        </div>

        <footer className="flex justify-end gap-2 border-t border-border px-5 py-3">
          <Button size="sm" variant="ghost" onClick={onCancel} disabled={isPending}>
            Cancel
          </Button>
          <Button size="sm" onClick={onConfirm} disabled={isPending}>
            {isPending ? (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
            ) : null}
            Install
          </Button>
        </footer>
      </div>
    </div>
  );
}

/** The trust boundary. Nothing was human-reviewed, so BEFORE the install this
 *  dialog states in plain language what the plugin is allowed to do, then names
 *  verbatim where requests (and later the token) would go, or which command
 *  would run locally — the two fields a malicious submission would have to use. */
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
  const updating = Boolean(plugin.installed && plugin.update_available);

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
      <div className="relative w-full max-w-md overflow-hidden rounded-2xl border border-border bg-card shadow-[0_20px_60px_rgba(0,0,0,0.6)]">
        <header className="flex items-center gap-3 border-b border-border px-5 py-4">
          <CommunityTile plugin={plugin} />
          <div className="min-w-0">
            <h2
              id="community-install-title"
              className="font-display text-base font-semibold tracking-tight"
            >
              Install {name}?
            </h2>
            <p className="text-[11px] uppercase tracking-wider text-amber-500/80">
              Community plugin · not reviewed
            </p>
          </div>
        </header>

        <div className="space-y-3 px-5 py-4 text-sm">
          <p className="text-muted-foreground">{plugin.description}</p>
          <p className="text-xs text-muted-foreground">
            Published by{" "}
            <span className="text-foreground">
              {plugin.publisher ?? "an unknown author"}
            </span>
            {plugin.version ? ` · version ${plugin.version}` : ""}
          </p>

          <ul className="space-y-1.5 rounded-md border border-border bg-muted/20 px-2.5 py-2">
            {capabilityLines(plugin).map((line, i) => (
              <li key={i} className="flex items-start gap-2 text-xs text-muted-foreground">
                <span className="mt-0.5 shrink-0">{line.icon}</span>
                <span>{line.text}</span>
              </li>
            ))}
          </ul>

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
          {(plugin.bundled_skills?.length ?? 0) > 0 && (
            <div>
              <p className="mb-1 text-xs font-medium text-foreground">
                Installing also adds{" "}
                {plugin.bundled_skills!.length === 1 ? "this skill" : "these skills"}{" "}
                to your skills folder:
              </p>
              <ul className="space-y-0.5 rounded-md border border-border bg-muted/40 px-2.5 py-1.5">
                {plugin.bundled_skills!.map((skill) => (
                  <li key={skill} className="font-mono text-xs text-foreground">
                    {skill}
                  </li>
                ))}
              </ul>
              <p className="mt-1 text-[11px] text-muted-foreground">
                They start as drafts and leave again when you remove the plugin.
              </p>
            </div>
          )}
          {authLabel && (
            <p className="text-xs text-muted-foreground">
              Sign-in method: <span className="text-foreground">{authLabel}</span>
              {" "}— installing does not connect anything yet.
            </p>
          )}
          {updating && (
            <p className="text-xs text-muted-foreground">
              This replaces your installed v{plugin.installed_version} — the
              connection you already made stays.
            </p>
          )}

          {errorMessage && (
            <p className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/5 px-2 py-1.5 text-xs text-destructive">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              {errorMessage}
            </p>
          )}
        </div>

        <footer className="flex justify-end gap-2 border-t border-border px-5 py-3">
          <Button size="sm" variant="ghost" onClick={onCancel} disabled={isPending}>
            Cancel
          </Button>
          <Button size="sm" onClick={onConfirm} disabled={isPending}>
            {isPending ? (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
            ) : null}
            Install
          </Button>
        </footer>
      </div>
    </div>
  );
}
