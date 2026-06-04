import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  UserCircle2,
  Users as UsersIcon,
  Inbox,
  RefreshCw,
  Check,
  X,
  ShieldQuestion,
  Sparkles,
  MessageSquareQuote,
  BrainCircuit,
  Heart,
  Target,
  Briefcase,
  FileText,
  Clock,
  ChevronRight,
  Lock,
  Pencil,
  Save,
  Camera,
  Trash2,
  Loader2,
} from "lucide-react";
import { ViewHeader } from "@/views/ChatsView";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { useEventStore } from "@/store/events";
import { cn } from "@/lib/utils";
import { getWSClient } from "@/hooks/useWebSocket";
import { useT } from "@/i18n";

// ----------------------------------------------------------------------
// Types — spiegeln die Backend-Responses aus profile_routes.py
// ----------------------------------------------------------------------

type ClusterId = "identity" | "communication" | "work_style" | "values" | "relationship";

interface ProfileResponse {
  user: {
    name: string | null;
    meta: Record<string, unknown>;
    path: string;
  };
  people: PersonSummary[];
  reviews_count: number;
  has_avatar?: boolean;
}

interface PersonSummary {
  name: string;
  relationship: string;
  aliases: string[];
  slug: string;
}

interface ReviewCandidate {
  idx: number;
  subject: string;
  is_person: boolean;
  person_name: string | null;
  cluster: string;
  field: string;
  value: unknown;
  operation: string;
  confidence: number;
  evidence: string;
  relationship: string | null;
  reason: string;
}

interface ReviewsResponse {
  reviews: ReviewCandidate[];
  total: number;
}

// ----------------------------------------------------------------------
// Fetching helpers
// ----------------------------------------------------------------------

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) {
    if (res.status === 503) {
      const data = await res.json().catch(() => ({ detail: "Profile system not ready." }));
      const err = new Error(data.detail ?? `HTTP ${res.status}`) as Error & { status?: number };
      err.status = 503;
      throw err;
    }
    const txt = await res.text().catch(() => "");
    throw new Error(`HTTP ${res.status}: ${txt || res.statusText}`);
  }
  return res.json();
}

// ----------------------------------------------------------------------
// Cluster-Metadaten — fuer Card-Ueberschriften + Icons
// ----------------------------------------------------------------------

interface ClusterMeta {
  label: string;
  description: string;
  icon: React.ComponentType<{ className?: string }>;
  fields: { key: string; label: string }[];
}

const CLUSTER_FIELD_KEYS: Record<ClusterId, string[]> = {
  identity: ["name", "preferred_address", "pronouns", "primary_language", "languages", "timezone", "devices"],
  communication: ["directness", "formality", "verbosity", "humor_types", "emoji_ok"],
  work_style: ["focus_mode", "planning_horizon"],
  values: ["top_values", "pet_peeves", "motivations"],
  relationship: ["feedback_pref"],
};

const CLUSTER_ICONS: Record<ClusterId, React.ComponentType<{ className?: string }>> = {
  identity: UserCircle2,
  communication: MessageSquareQuote,
  work_style: Briefcase,
  values: Target,
  relationship: Heart,
};

function makeClusterMeta(t: (k: string) => string): Record<ClusterId, ClusterMeta> {
  const result = {} as Record<ClusterId, ClusterMeta>;
  (Object.keys(CLUSTER_FIELD_KEYS) as ClusterId[]).forEach((cid) => {
    result[cid] = {
      label: t(`profile_view.clusters.${cid}.label`),
      description: t(`profile_view.clusters.${cid}.description`),
      icon: CLUSTER_ICONS[cid],
      fields: CLUSTER_FIELD_KEYS[cid].map((key) => ({
        key,
        label: t(`profile_view.fields.${key}`),
      })),
    };
  });
  return result;
}

const CLUSTER_ORDER: ClusterId[] = [
  "identity",
  "communication",
  "work_style",
  "values",
  "relationship",
];

// ----------------------------------------------------------------------
// Shared value helpers
// ----------------------------------------------------------------------

function isEmptyValue(value: unknown): boolean {
  return (
    value === undefined ||
    value === null ||
    value === "" ||
    (Array.isArray(value) && value.length === 0)
  );
}

function formatValue(v: unknown): string {
  if (Array.isArray(v)) return v.map(String).join(", ");
  if (typeof v === "boolean") return v ? "Ja" : "Nein";
  if (v === null || v === undefined) return "—";
  return String(v);
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

function clusterFill(
  fields: { key: string }[],
  data: Record<string, unknown>,
): { filled: number; total: number } {
  let filled = 0;
  for (const f of fields) {
    if (!isEmptyValue(data[f.key])) filled += 1;
  }
  return { filled, total: fields.length };
}

// ----------------------------------------------------------------------
// Dossier primitives — the visual signature of this view
// ----------------------------------------------------------------------

// Corner brackets — the "file photo / targeting reticle" motif. Wraps any
// child and frames it with four L-shaped corners.
function CornerFrame({
  children,
  className,
  tone = "primary",
}: {
  children: React.ReactNode;
  className?: string;
  tone?: "primary" | "muted";
}) {
  const c = tone === "primary" ? "border-primary/60" : "border-muted-foreground/40";
  return (
    <div className={cn("relative", className)}>
      <span className={cn("pointer-events-none absolute -left-1 -top-1 h-2.5 w-2.5 border-l-2 border-t-2", c)} />
      <span className={cn("pointer-events-none absolute -right-1 -top-1 h-2.5 w-2.5 border-r-2 border-t-2", c)} />
      <span className={cn("pointer-events-none absolute -bottom-1 -left-1 h-2.5 w-2.5 border-b-2 border-l-2", c)} />
      <span className={cn("pointer-events-none absolute -bottom-1 -right-1 h-2.5 w-2.5 border-b-2 border-r-2", c)} />
      {children}
    </div>
  );
}

// Segmented instrument meter — replaces the ubiquitous dashboard donut.
function SegmentedMeter({
  pct,
  segments = 22,
  className,
}: {
  pct: number;
  segments?: number;
  className?: string;
}) {
  const lit = Math.round((Math.min(100, Math.max(0, pct)) / 100) * segments);
  return (
    <div className={cn("flex items-end gap-[3px]", className)}>
      {Array.from({ length: segments }).map((_, i) => (
        <span
          key={i}
          className={cn(
            "w-1 rounded-[1px] transition-all duration-500",
            i < lit ? "h-3.5 bg-primary" : "h-2 bg-border",
          )}
          style={{ transitionDelay: `${i * 16}ms` }}
        />
      ))}
    </div>
  );
}

// Two-digit dossier index ("01", "02", …).
function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

// ----------------------------------------------------------------------
// ProfileView — Root
// ----------------------------------------------------------------------

export function ProfileView() {
  const t = useT();
  const { data, isLoading, error, refetch, isRefetching } = useQuery<ProfileResponse, Error>({
    queryKey: ["profile"],
    queryFn: () => fetchJson<ProfileResponse>("/api/profile"),
    retry: false,
  });

  const [activeSlug, setActiveSlug] = useState<string | null>(null);

  return (
    <div className="flex h-full flex-col">
      <ViewHeader
        icon={<UserCircle2 className="h-4 w-4 text-primary" />}
        title={t("profile_view.title")}
        subtitle={t("profile_view.subtitle")}
        right={
          <Button
            size="sm"
            variant="ghost"
            onClick={() => refetch()}
            disabled={isRefetching}
            title={t("profile_view.reload_tooltip")}
          >
            <RefreshCw className={cn("h-4 w-4", isRefetching && "animate-spin")} />
          </Button>
        }
      />

      {isLoading && <LoadingState />}
      {error && <ErrorState error={error} onRetry={() => refetch()} />}

      {data && (
        <ScrollArea className="flex-1 scrollbar-jarvis">
          <div className="mx-auto w-full max-w-5xl px-5 pb-24 md:px-10">
            <div className="profile-rise" style={{ animationDelay: "0ms" }}>
              <HeroBand data={data} />
            </div>

            <NumberedSection
              index={1}
              title={t("profile_view.section_knowledge")}
              delay={70}
              right={
                <span className="hidden truncate font-mono text-[10px] text-muted-foreground/60 sm:block">
                  {data.user.path}
                </span>
              }
            >
              <KnowledgeMatrix data={data} />
            </NumberedSection>

            <NumberedSection
              index={2}
              title={t("profile_view.section_reviews")}
              delay={140}
              wrapReviews
            >
              <ReviewsSection reviewsCount={data.reviews_count} />
            </NumberedSection>

            <NumberedSection
              index={3}
              title={t("profile_view.section_people")}
              count={data.people.length > 0 ? data.people.length : null}
              delay={210}
            >
              <PeopleSection
                people={data.people}
                activeSlug={activeSlug}
                onSelect={setActiveSlug}
              />
            </NumberedSection>

            <NumberedSection
              index={4}
              title={t("profile_view.section_source")}
              delay={280}
            >
              <RawMarkdownSection />
            </NumberedSection>
          </div>
        </ScrollArea>
      )}
    </div>
  );
}

// ----------------------------------------------------------------------
// Numbered editorial section header — ghost numeral + hairline rule
// ----------------------------------------------------------------------

function NumberedSection({
  index,
  title,
  count,
  delay,
  right,
  children,
  // ReviewsSection renders its own refresh control inside the header slot,
  // so when wrapReviews is set the count/right are managed by the child.
  wrapReviews,
}: {
  index: number;
  title: string;
  count?: number | null;
  delay: number;
  right?: React.ReactNode;
  children: React.ReactNode;
  wrapReviews?: boolean;
}) {
  return (
    <section className="mt-12 profile-rise" style={{ animationDelay: `${delay}ms` }}>
      <div className="mb-5 flex items-end gap-3 border-b border-border/70 pb-2.5">
        <span className="font-display text-[2.75rem] font-bold leading-[0.8] tabular-nums text-primary/15">
          {pad2(index)}
        </span>
        <div className="flex flex-1 items-baseline gap-2.5">
          <h3 className="font-display text-lg font-semibold tracking-tight">{title}</h3>
          {count != null && (
            <span className="font-mono text-[11px] font-semibold tabular-nums text-muted-foreground">
              [{pad2(count)}]
            </span>
          )}
        </div>
        {!wrapReviews && right && <div className="self-end pb-1">{right}</div>}
      </div>
      {children}
    </section>
  );
}

// ----------------------------------------------------------------------
// Hero — the classified file header
// ----------------------------------------------------------------------

function HeroBand({ data }: { data: ProfileResponse }) {
  const t = useT();
  const name = data.user.name?.trim() || null;
  const meta = (data.user.meta ?? {}) as Record<string, unknown>;

  const { filled, total } = useMemo(() => {
    let f = 0;
    let tot = 0;
    for (const cid of CLUSTER_ORDER) {
      const clusterData = (meta[cid] ?? {}) as Record<string, unknown>;
      for (const key of CLUSTER_FIELD_KEYS[cid]) {
        tot += 1;
        if (!isEmptyValue(clusterData[key])) f += 1;
      }
    }
    return { filled: f, total: tot };
  }, [meta]);

  const pct = total > 0 ? Math.round((filled / total) * 100) : 0;

  return (
    <section className="relative mt-7 overflow-hidden rounded-3xl border border-border bg-card/40">
      {/* Atmosphere */}
      <div className="jarvis-grid pointer-events-none absolute inset-0 opacity-60" />
      <div className="jarvis-glow pointer-events-none absolute -right-28 -top-32 h-80 w-80" />

      {/* Classification strip */}
      <div className="relative flex items-center justify-between border-b border-border/70 px-5 py-2 font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground md:px-8">
        <span className="flex items-center gap-2">
          <BrainCircuit className="h-3 w-3 text-primary/70" />
          {t("profile_view.hero_classification")}
        </span>
        <span className="hidden truncate text-muted-foreground/60 sm:block">
          {t("profile_view.hero_maintained_by")}
        </span>
      </div>

      {/* Main */}
      <div className="relative flex flex-col gap-8 p-5 md:flex-row md:items-end md:justify-between md:p-8">
        <div className="flex items-center gap-5">
          <AvatarBlock name={name} hasAvatar={!!data.has_avatar} />
          <div className="min-w-0">
            <div className="text-[11px] font-semibold uppercase tracking-[0.24em] text-primary/80">
              {t("profile_view.hero_eyebrow")}
            </div>
            <h1
              className={cn(
                "mt-1.5 font-display text-[2.5rem] font-bold leading-[0.95] tracking-tight md:text-5xl",
                name ? "text-gradient-yellow" : "text-foreground/40",
              )}
            >
              {name ?? t("profile_view.hero_name_placeholder")}
            </h1>
            <p className="mt-3 max-w-sm text-sm leading-relaxed text-muted-foreground">
              {name ? t("profile_view.hero_tagline") : t("profile_view.no_user_hint")}
            </p>
          </div>
        </div>

        {/* Completeness instrument */}
        <div className="shrink-0 md:text-right">
          <div className="flex items-baseline gap-2 md:justify-end">
            <span className="font-display text-5xl font-bold tabular-nums leading-none text-foreground">
              {pct}
            </span>
            <span className="font-display text-xl font-semibold text-primary">%</span>
          </div>
          <div className="mt-2.5 md:flex md:justify-end">
            <SegmentedMeter pct={pct} />
          </div>
          <div className="mt-2 font-mono text-[10px] uppercase tracking-[0.16em] text-muted-foreground">
            {t("profile_view.hero_completeness")} ·{" "}
            <span className="text-foreground/70">{filled}</span>/{total}
          </div>
          <div className="mt-4 flex gap-2 md:justify-end">
            <CountChip value={data.people.length} label={t("profile_view.people")} />
            <CountChip
              value={data.reviews_count}
              label={t("profile_view.reviews_count")}
              highlight={data.reviews_count > 0}
            />
          </div>
        </div>
      </div>
    </section>
  );
}

// ----------------------------------------------------------------------
// AvatarBlock — uploadable / removable profile picture in the hero frame
// ----------------------------------------------------------------------
//
// The avatar bytes live under user_data_dir()/data and are served by
// GET /api/profile/avatar (see profile_routes.py). The frame doubles as the
// upload trigger: a hidden <input type="file"> is .click()'d, which opens the
// OS file picker. A cache-bust query param forces the <img> to reload after a
// replace/delete — the endpoint is no-store, but webviews still cache by URL.

function AvatarBlock({ name, hasAvatar }: { name: string | null; hasAvatar: boolean }) {
  const t = useT();
  const queryClient = useQueryClient();
  const pushToast = useEventStore((s) => s.pushToast);
  const inputRef = useRef<HTMLInputElement | null>(null);
  // Cache-bust token: bumped on every successful upload/delete so the browser
  // re-fetches the image instead of showing a stale cached portrait.
  const [bust, setBust] = useState(0);

  const upload = useMutation({
    mutationFn: async (file: File) => {
      const fd = new FormData();
      fd.append("file", file);
      const res = await fetch("/api/profile/avatar", { method: "POST", body: fd });
      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as { detail?: string };
        throw new Error(body.detail ?? `HTTP ${res.status}`);
      }
      return res.json();
    },
    onSuccess: () => {
      setBust(Date.now());
      pushToast("success", t("profile_view.avatar_uploaded"));
      queryClient.invalidateQueries({ queryKey: ["profile"] });
    },
    onError: (err: Error) => pushToast("error", err.message),
  });

  const remove = useMutation({
    mutationFn: async () => {
      const res = await fetch("/api/profile/avatar", { method: "DELETE" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json();
    },
    onSuccess: () => {
      setBust(Date.now());
      pushToast("info", t("profile_view.avatar_removed"));
      queryClient.invalidateQueries({ queryKey: ["profile"] });
    },
    onError: (err: Error) => pushToast("error", err.message),
  });

  const openPicker = () => inputRef.current?.click();

  const onFileChosen = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    // Reset so picking the *same* file again still fires onChange.
    e.target.value = "";
    if (file) upload.mutate(file);
  };

  const busy = upload.isPending || remove.isPending;

  return (
    <div className="flex flex-col items-center gap-2.5">
      <input
        ref={inputRef}
        type="file"
        accept="image/png,image/jpeg,image/webp,image/gif"
        className="hidden"
        aria-hidden="true"
        tabIndex={-1}
        onChange={onFileChosen}
      />
      <CornerFrame>
        <button
          type="button"
          onClick={openPicker}
          disabled={busy}
          title={hasAvatar ? t("profile_view.avatar_change") : t("profile_view.avatar_upload")}
          aria-label={hasAvatar ? t("profile_view.avatar_change") : t("profile_view.avatar_upload")}
          className="group relative flex h-[5.5rem] w-[5.5rem] items-center justify-center overflow-hidden rounded-xl border border-primary/25 bg-gradient-to-br from-secondary/80 to-background outline-none transition-colors hover:border-primary/60 focus-visible:border-primary"
        >
          {hasAvatar ? (
            <img
              src={`/api/profile/avatar?t=${bust}`}
              alt={t("profile_view.avatar_alt")}
              className="h-full w-full object-cover"
              draggable={false}
            />
          ) : name ? (
            <span className="font-display text-3xl font-bold tracking-tight text-primary">
              {initials(name)}
            </span>
          ) : (
            <UserCircle2 className="h-10 w-10 text-muted-foreground/50" />
          )}

          {/* Hover/focus affordance — a camera scrim that invites the click. */}
          <span className="pointer-events-none absolute inset-0 flex items-center justify-center bg-background/70 opacity-0 backdrop-blur-[1px] transition-opacity duration-200 group-hover:opacity-100 group-focus-visible:opacity-100">
            <Camera className="h-6 w-6 text-primary" />
          </span>

          {busy && (
            <span className="absolute inset-0 flex items-center justify-center bg-background/70">
              <Loader2 className="h-6 w-6 animate-spin text-primary" />
            </span>
          )}
        </button>
      </CornerFrame>

      {/* Always-visible controls — no hover required (and easy to click). */}
      <div className="flex items-center gap-1.5">
        <Button
          size="sm"
          variant="outline"
          onClick={openPicker}
          disabled={busy}
          className="h-7 border-primary/40 px-2.5 text-[11px] font-medium text-primary hover:bg-primary/10 hover:text-primary"
        >
          <Camera className="mr-1.5 h-3.5 w-3.5" />
          {hasAvatar ? t("profile_view.avatar_change") : t("profile_view.avatar_upload")}
        </Button>
        {hasAvatar && (
          <Button
            size="sm"
            variant="ghost"
            onClick={() => remove.mutate()}
            disabled={busy}
            title={t("profile_view.avatar_remove")}
            aria-label={t("profile_view.avatar_remove")}
            className="h-7 w-7 p-0 text-muted-foreground hover:text-destructive"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        )}
      </div>
    </div>
  );
}

function CountChip({
  value,
  label,
  highlight,
}: {
  value: number;
  label: string;
  highlight?: boolean;
}) {
  return (
    <div
      className={cn(
        "flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5",
        highlight ? "border-primary/40 bg-primary/10" : "border-border bg-secondary/40",
      )}
    >
      <span
        className={cn(
          "font-mono text-sm font-bold tabular-nums leading-none",
          highlight ? "text-primary" : "text-foreground",
        )}
      >
        {pad2(value)}
      </span>
      <span className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
        {label}
      </span>
    </div>
  );
}

// ----------------------------------------------------------------------
// Loading / Error
// ----------------------------------------------------------------------

function LoadingState() {
  const t = useT();
  return (
    <div className="flex flex-1 items-center justify-center">
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <RefreshCw className="h-4 w-4 animate-spin" /> {t("common.loading")}
      </div>
    </div>
  );
}

function ErrorState({ error, onRetry }: { error: Error; onRetry: () => void }) {
  const t = useT();
  const status = (error as Error & { status?: number }).status;
  if (status === 503) {
    return (
      <div className="flex flex-1 items-center justify-center p-8">
        <div className="relative max-w-md overflow-hidden rounded-3xl border border-border bg-card/40 p-8 text-center">
          <div className="jarvis-glow pointer-events-none absolute -top-16 left-1/2 h-48 w-48 -translate-x-1/2" />
          <div className="relative">
            <CornerFrame className="mx-auto mb-5 w-fit">
              <div className="flex h-16 w-16 items-center justify-center rounded-2xl border border-primary/30 bg-secondary/50">
                <Sparkles className="h-7 w-7 text-primary" />
              </div>
            </CornerFrame>
            <h3 className="font-display text-lg font-semibold tracking-tight">
              {t("profile_view.hero_name_placeholder")}
            </h3>
            <p className="mt-2 text-sm text-muted-foreground">{error.message}</p>
            <p className="mt-4 text-xs italic text-muted-foreground/70">
              {t("profile_view.no_user_hint")}
            </p>
            <Button className="mt-6" size="sm" variant="outline" onClick={onRetry}>
              <RefreshCw className="mr-2 h-3.5 w-3.5" /> {t("apikeys_view.retry")}
            </Button>
          </div>
        </div>
      </div>
    );
  }
  return (
    <div className="p-6">
      <div className="rounded-2xl border border-destructive/40 bg-destructive/10 p-4 text-sm text-destructive">
        {t("common.error_generic")}: {error.message}
        <button className="ml-2 underline" onClick={onRetry}>
          {t("apikeys_view.retry")}
        </button>
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------
// Knowledge Matrix — dossier readout per cluster
// ----------------------------------------------------------------------

function KnowledgeMatrix({ data }: { data: ProfileResponse }) {
  const t = useT();
  const CLUSTER_META = makeClusterMeta(t);
  const meta = (data.user.meta ?? {}) as Record<string, unknown>;

  return (
    <div className="grid items-start gap-4 md:grid-cols-2">
      {CLUSTER_ORDER.map((cid, i) => {
        const clusterMeta = CLUSTER_META[cid];
        const clusterData = (meta[cid] ?? {}) as Record<string, unknown>;
        const span = cid === "relationship" ? "md:col-span-2" : "";
        return (
          <ClusterCard
            key={cid}
            index={i + 1}
            meta={clusterMeta}
            data={clusterData}
            className={span}
          />
        );
      })}
    </div>
  );
}

function ClusterCard({
  index,
  meta,
  data,
  className,
}: {
  index: number;
  meta: ClusterMeta;
  data: Record<string, unknown>;
  className?: string;
}) {
  const t = useT();
  const Icon = meta.icon;
  const { filled, total } = clusterFill(meta.fields, data);
  const has = filled > 0;

  return (
    <div
      className={cn(
        "group relative overflow-hidden rounded-2xl border border-border/70 bg-card/30 p-5 transition-colors hover:border-primary/30",
        className,
      )}
    >
      <div className="flex items-start gap-3">
        <div
          className={cn(
            "mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border transition-colors",
            has
              ? "border-primary/30 bg-primary/10 text-primary"
              : "border-border bg-secondary/50 text-muted-foreground",
          )}
        >
          <Icon className="h-4 w-4" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline justify-between gap-2">
            <div className="flex items-baseline gap-2">
              <span className="font-mono text-[10px] text-muted-foreground/50">
                {pad2(index)}
              </span>
              <span className="font-display text-sm font-semibold">{meta.label}</span>
            </div>
            <span
              className={cn(
                "shrink-0 font-mono text-[10px] tabular-nums",
                has ? "text-primary" : "text-muted-foreground/50",
              )}
            >
              {t("profile_view.facts_ratio")
                .replace("{0}", String(filled))
                .replace("{1}", String(total))}
            </span>
          </div>
          <p className="mt-0.5 text-[11px] leading-snug text-muted-foreground">
            {meta.description}
          </p>
          <SegmentedMeter pct={total > 0 ? (filled / total) * 100 : 0} segments={total} className="mt-2.5" />
        </div>
      </div>

      <dl className="mt-4 border-t border-border/40 pt-1">
        {meta.fields.map((f) => (
          <FieldRow key={f.key} label={f.label} value={data[f.key]} />
        ))}
      </dl>
    </div>
  );
}

function FieldRow({ label, value }: { label: string; value: unknown }) {
  const t = useT();
  const empty = isEmptyValue(value);
  const isArray = Array.isArray(value) && value.length > 0;

  return (
    <div className="flex items-start justify-between gap-4 border-b border-border/30 py-2 last:border-b-0">
      <dt className="shrink-0 pt-0.5 font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
        {label}
      </dt>
      {empty ? (
        // Unknown fields read as a plain, quiet label — never a redaction bar.
        // Nothing in this view is withheld; an empty field is simply not filled
        // in yet.
        <dd className="min-w-0 flex-1 truncate text-right text-[13px] italic text-muted-foreground/50">
          {t("profile_view.field_unknown")}
        </dd>
      ) : isArray ? (
        <dd className="flex min-w-0 flex-wrap justify-end gap-1">
          {(value as unknown[]).map((item, i) => (
            <span
              key={i}
              className="rounded-md border border-border bg-secondary/50 px-1.5 py-0.5 font-mono text-[11px] text-foreground/90"
            >
              {String(item)}
            </span>
          ))}
        </dd>
      ) : (
        <dd className="min-w-0 flex-1 truncate text-right text-[13px] font-semibold text-foreground">
          {formatValue(value)}
        </dd>
      )}
    </div>
  );
}

// ----------------------------------------------------------------------
// Review-Queue Section
// ----------------------------------------------------------------------

function ReviewsSection({ reviewsCount }: { reviewsCount: number }) {
  const t = useT();
  const queryClient = useQueryClient();
  const pushToast = useEventStore((s) => s.pushToast);

  const { data, isLoading, error, refetch, isRefetching } = useQuery<ReviewsResponse, Error>({
    queryKey: ["profile", "reviews"],
    queryFn: () => fetchJson<ReviewsResponse>("/api/profile/reviews"),
    retry: false,
  });

  const accept = useMutation({
    mutationFn: (idx: number) =>
      fetchJson<{ ok: boolean; applied: number }>(
        `/api/profile/reviews/${idx}/accept`,
        { method: "POST" },
      ),
    onSuccess: (res) => {
      pushToast(
        "success",
        res.applied > 0
          ? t("profile_toast.fact_applied").replace("{0}", String(res.applied))
          : t("profile_view.accepted"),
      );
      queryClient.invalidateQueries({ queryKey: ["profile"] });
      queryClient.invalidateQueries({ queryKey: ["profile", "reviews"] });
    },
    onError: (err: Error) => pushToast("error", err.message),
  });

  const reject = useMutation({
    mutationFn: (idx: number) =>
      fetchJson<{ ok: boolean }>(
        `/api/profile/reviews/${idx}/reject`,
        { method: "POST" },
      ),
    onSuccess: () => {
      pushToast("info", t("profile_view.reject_tooltip"));
      queryClient.invalidateQueries({ queryKey: ["profile"] });
      queryClient.invalidateQueries({ queryKey: ["profile", "reviews"] });
    },
    onError: (err: Error) => pushToast("error", err.message),
  });

  const pendingIdx: number | null = accept.isPending
    ? (accept.variables ?? null)
    : reject.isPending
      ? (reject.variables ?? null)
      : null;

  const items = data?.reviews ?? [];

  return (
    <div>
      {/* Inline header controls — the NumberedSection rule sits above. */}
      <div className="-mt-2 mb-4 flex items-center justify-between">
        <span className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground">
          {reviewsCount > 0 ? (
            <span className="text-primary">[{pad2(reviewsCount)}] {t("profile_view.review_open")}</span>
          ) : (
            <span className="text-muted-foreground/60">[00]</span>
          )}
        </span>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => refetch()}
          disabled={isRefetching}
          title={t("profile_view.reload_tooltip")}
        >
          <RefreshCw className={cn("h-3.5 w-3.5", isRefetching && "animate-spin")} />
        </Button>
      </div>

      {isLoading && (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <RefreshCw className="h-3.5 w-3.5 animate-spin" /> {t("profile_view.raw_loading")}
        </div>
      )}
      {error &&
        ((error as Error & { status?: number }).status === 503 ? (
          // 503 = the Curator subsystem is intentionally not running (e.g.
          // legacy_curator soft-disabled, Mock-Brain). Per the backend
          // contract in profile_routes.py this is an expected state and must
          // render as a calm empty-state, NOT a destructive red badge.
          <ReviewsDisabled />
        ) : (
          <div
            data-testid="reviews-error"
            className="rounded-2xl border border-destructive/40 bg-destructive/10 p-4 text-sm text-destructive"
          >
            {error.message}
          </div>
        ))}

      {data && items.length === 0 && <ReviewsEmpty />}

      {items.length > 0 && (
        <ul className="space-y-2.5">
          {items.map((c) => (
            <ReviewRow
              key={c.idx}
              candidate={c}
              pending={pendingIdx === c.idx}
              onAccept={() => accept.mutate(c.idx)}
              onReject={() => reject.mutate(c.idx)}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

// Calm, designed empty state shared in shape by ReviewsEmpty / PeopleEmpty.
function EmptyCard({
  icon: Icon,
  title,
  body,
  testId,
}: {
  icon: React.ComponentType<{ className?: string }>;
  title: string;
  body: React.ReactNode;
  testId?: string;
}) {
  return (
    <div
      data-testid={testId}
      className="flex items-center gap-4 rounded-2xl border border-dashed border-border bg-card/20 p-6"
    >
      <CornerFrame tone="muted">
        <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border border-border bg-secondary/40">
          <Icon className="h-5 w-5 text-muted-foreground" />
        </div>
      </CornerFrame>
      <div className="min-w-0 flex-1">
        <div className="text-sm font-medium">{title}</div>
        <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">{body}</p>
      </div>
    </div>
  );
}

function ReviewsEmpty() {
  const t = useT();
  return (
    <EmptyCard
      icon={ShieldQuestion}
      title={t("profile_view.reviews_empty_title")}
      body={t("profile_view.reviews_empty_body")}
    />
  );
}

// Shown when the reviews endpoint returns 503 — the Curator subsystem is
// intentionally not running (legacy_curator soft-disabled / Mock-Brain). Uses
// the same calm, dashed-border visual language as ReviewsEmpty so a designed
// "off" state never looks like a failure. Keeps the data-testid contract.
function ReviewsDisabled() {
  const t = useT();
  return (
    <EmptyCard
      icon={ShieldQuestion}
      title={t("profile_view.reviews_disabled_title")}
      body={t("profile_view.reviews_disabled_body")}
      testId="reviews-disabled"
    />
  );
}

function ReviewRow({
  candidate,
  pending,
  onAccept,
  onReject,
}: {
  candidate: ReviewCandidate;
  pending: boolean;
  onAccept: () => void;
  onReject: () => void;
}) {
  const t = useT();
  const conf = candidate.confidence;
  const confColor =
    conf >= 0.7 ? "text-emerald-400" : conf >= 0.5 ? "text-amber-400" : "text-muted-foreground";
  const confTrack =
    conf >= 0.7 ? "bg-emerald-400" : conf >= 0.5 ? "bg-amber-400" : "bg-muted-foreground";

  return (
    <li className="relative overflow-hidden rounded-2xl border border-border/70 bg-card/30 p-4 transition-colors hover:border-primary/30">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1 space-y-2">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="rounded-md border border-border bg-secondary/60 px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
              {candidate.is_person
                ? `person:${candidate.person_name}`
                : t("profile_view.review_subject_user")}
            </span>
            <span className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
              {candidate.cluster}
            </span>
            <ChevronRight className="h-3 w-3 text-muted-foreground/40" />
            <span className="font-mono text-xs text-foreground">{candidate.field}</span>
            <Badge variant="outline" className="font-mono text-[10px]">
              {candidate.operation}
            </Badge>
          </div>
          <div className="text-sm">
            <span className="text-muted-foreground">{t("profile_view.review_value")}:</span>{" "}
            <span className="font-semibold text-foreground">{formatValue(candidate.value)}</span>
          </div>
          {candidate.evidence && (
            <blockquote className="border-l-2 border-primary/40 pl-3 text-xs italic text-muted-foreground">
              „{candidate.evidence}"
            </blockquote>
          )}
          {candidate.reason && (
            <p className="text-[10px] uppercase tracking-wider text-muted-foreground/70">
              {t("profile_view.review_reason")}:{" "}
              <span className="font-mono normal-case">{candidate.reason}</span>
            </p>
          )}
        </div>

        <div className="flex shrink-0 flex-col items-end gap-2.5">
          <div className="flex flex-col items-end gap-1">
            <div className={cn("font-mono text-sm font-semibold tabular-nums", confColor)}>
              {(conf * 100).toFixed(0)}%
            </div>
            <div className="h-1 w-14 overflow-hidden rounded-full bg-border">
              <div
                className={cn("h-full rounded-full", confTrack)}
                style={{ width: `${Math.round(conf * 100)}%` }}
              />
            </div>
          </div>
          <div className="flex gap-1.5">
            <Button
              size="sm"
              variant="default"
              disabled={pending}
              onClick={onAccept}
              title={t("profile_view.accept_tooltip")}
            >
              <Check className="h-3.5 w-3.5" />
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={pending}
              onClick={onReject}
              title={t("profile_view.reject_tooltip")}
            >
              <X className="h-3.5 w-3.5" />
            </Button>
          </div>
        </div>
      </div>
    </li>
  );
}

// ----------------------------------------------------------------------
// People-Roster mit Detail-Panel
// ----------------------------------------------------------------------

function PeopleSection({
  people,
  activeSlug,
  onSelect,
}: {
  people: PersonSummary[];
  activeSlug: string | null;
  onSelect: (slug: string | null) => void;
}) {
  const active = useMemo(
    () => people.find((p) => p.slug === activeSlug) ?? null,
    [people, activeSlug],
  );

  if (people.length === 0) return <PeopleEmpty />;

  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_340px]">
      <ul className="grid gap-2.5 sm:grid-cols-2 lg:grid-cols-1 xl:grid-cols-2">
        {people.map((p) => (
          <PersonRow
            key={p.slug}
            person={p}
            active={p.slug === activeSlug}
            onClick={() => onSelect(p.slug === activeSlug ? null : p.slug)}
          />
        ))}
      </ul>
      <PersonDetail person={active} onClose={() => onSelect(null)} />
    </div>
  );
}

function PeopleEmpty() {
  const t = useT();
  return (
    <EmptyCard
      icon={UsersIcon}
      title={t("profile_view.people_empty_title")}
      body={t("profile_view.people_empty_body")}
    />
  );
}

function PersonRow({
  person,
  active,
  onClick,
}: {
  person: PersonSummary;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <li>
      <button
        type="button"
        onClick={onClick}
        className={cn(
          "flex w-full items-center gap-3 rounded-2xl border p-3 text-left transition-all duration-200",
          active
            ? "border-primary/50 bg-primary/[0.06] shadow-[inset_2px_0_0_hsl(var(--primary))]"
            : "border-border bg-card/30 hover:-translate-y-0.5 hover:border-primary/30 hover:bg-card/60",
        )}
      >
        <CornerFrame tone={active ? "primary" : "muted"}>
          <div
            className={cn(
              "flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border font-display text-sm font-semibold transition-colors",
              active
                ? "border-primary/40 bg-primary/15 text-primary"
                : "border-border bg-secondary/50 text-primary/80",
            )}
          >
            {initials(person.name)}
          </div>
        </CornerFrame>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate font-medium">{person.name}</span>
            {person.aliases.length > 0 && (
              <span className="truncate text-[11px] italic text-muted-foreground">
                aka {person.aliases.join(", ")}
              </span>
            )}
          </div>
          <div className="mt-0.5 font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
            {person.relationship}
          </div>
        </div>
        <BrainCircuit
          className={cn(
            "h-4 w-4 shrink-0 transition-colors",
            active ? "text-primary" : "text-muted-foreground/40",
          )}
        />
      </button>
    </li>
  );
}

function PersonDetail({
  person,
  onClose,
}: {
  person: PersonSummary | null;
  onClose: () => void;
}) {
  const t = useT();
  if (!person) {
    return (
      <div className="hidden items-center justify-center rounded-2xl border border-dashed border-border bg-card/15 p-6 lg:flex">
        <div className="text-center text-xs text-muted-foreground">
          {t("profile_view.person_detail_hint")}
        </div>
      </div>
    );
  }

  return (
    <div className="h-fit rounded-2xl border border-border/70 bg-card/40 p-5">
      <div className="mb-4 flex items-start gap-3">
        <CornerFrame>
          <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-lg border border-primary/30 bg-primary/10 font-display text-base font-bold text-primary">
            {initials(person.name)}
          </div>
        </CornerFrame>
        <div className="min-w-0 flex-1">
          <div className="truncate font-display text-base font-semibold">{person.name}</div>
          <div className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
            {person.relationship}
          </div>
        </div>
        <Button
          size="sm"
          variant="ghost"
          onClick={onClose}
          title={t("profile_view.close_tooltip")}
        >
          <X className="h-3.5 w-3.5" />
        </Button>
      </div>

      <dl className="space-y-3 border-t border-border/50 pt-4 text-xs">
        <div className="flex items-baseline justify-between gap-3">
          <dt className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
            {t("profile_view.person_relationship")}
          </dt>
          <dd className="font-medium text-foreground">{person.relationship}</dd>
        </div>
        <div>
          <dt className="mb-1.5 font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
            {t("profile_view.person_aliases")}
          </dt>
          <dd>
            {person.aliases.length === 0 ? (
              <span className="italic text-muted-foreground/60">
                {t("profile_view.person_no_aliases")}
              </span>
            ) : (
              <div className="flex flex-wrap gap-1.5">
                {person.aliases.map((a) => (
                  <span
                    key={a}
                    className="rounded-md border border-border bg-background px-1.5 py-0.5 font-mono"
                  >
                    {a}
                  </span>
                ))}
              </div>
            )}
          </dd>
        </div>
        <div className="flex items-baseline justify-between gap-3">
          <dt className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
            Slug
          </dt>
          <dd className="truncate font-mono text-foreground">{person.slug}</dd>
        </div>
      </dl>

      <p className="mt-4 flex items-start gap-2 rounded-xl bg-background/50 p-2.5 text-[10px] leading-relaxed text-muted-foreground">
        <Inbox className="mt-0.5 h-3 w-3 shrink-0" />
        {t("profile_view.person_file_hint").replace("{0}", person.slug)}
      </p>
    </div>
  );
}

// ----------------------------------------------------------------------
// Raw-Markdown-Section — Live-Anzeige der USER.md-Datei
// ----------------------------------------------------------------------
//
// Datenfluss: GET /api/profile/raw → React-Query-Cache. Live-Sync via WS:
// jeder Curator-Merge publisht ProfileUpdated auf den Bus, der WS-Server
// streamed das an die UI, und der Subscriber unten invalidiert beide
// Profile-Queries — ohne manuelles Refresh ist der File-Inhalt sekunden-
// nach-Schreibung aktuell. Das Pulse-Badge gibt visuelles Feedback wenn
// gerade ein Update reinkam.

interface RawProfileResponse {
  content: string;
  path: string;
  mtime_ms: number | null;
  size_bytes: number;
}

function RawMarkdownSection() {
  const t = useT();
  const queryClient = useQueryClient();
  const pushToast = useEventStore((s) => s.pushToast);
  const [pulseUntil, setPulseUntil] = useState<number>(0);

  // Edit mode. `draft` is the working copy; `editBaseMtime` is frozen at
  // edit-start so the optimistic-concurrency guard on the backend stays
  // meaningful even if a background refetch updates `data.mtime_ms`.
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [editBaseMtime, setEditBaseMtime] = useState<number | null>(null);

  const { data, isLoading, error, refetch, isRefetching, dataUpdatedAt } =
    useQuery<RawProfileResponse, Error>({
      queryKey: ["profile", "raw"],
      queryFn: () => fetchJson<RawProfileResponse>("/api/profile/raw"),
      retry: false,
      staleTime: 0,
    });

  // Live-Subscribe auf ProfileUpdated-Events vom Bus.
  useEffect(() => {
    const client = getWSClient();
    if (!client) return;
    const unsubscribe = client.subscribe((raw) => {
      const env = raw as { event_name?: unknown };
      if (env.event_name !== "ProfileUpdated") return;
      // Cluster cards always refresh…
      queryClient.invalidateQueries({ queryKey: ["profile"] });
      // …but never replace the raw text while the user is editing it — that
      // would wipe their draft mid-keystroke.
      if (!editing) {
        queryClient.invalidateQueries({ queryKey: ["profile", "raw"] });
      }
      setPulseUntil(Date.now() + 2000);
    });
    return unsubscribe;
  }, [queryClient, editing]);

  const save = useMutation({
    mutationFn: async (content: string) => {
      const res = await fetch("/api/profile/raw", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content, mtime_ms: editBaseMtime }),
      });
      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as { detail?: string };
        const err = new Error(body.detail ?? `HTTP ${res.status}`) as Error & {
          status?: number;
        };
        err.status = res.status;
        throw err;
      }
      return res.json() as Promise<{
        ok: boolean;
        mtime_ms: number | null;
        frontmatter_ok: boolean;
      }>;
    },
    onSuccess: (res) => {
      setEditing(false);
      if (res.frontmatter_ok === false) {
        pushToast("error", t("profile_view.raw_frontmatter_warning"));
      } else {
        pushToast("success", t("profile_view.raw_saved"));
      }
      queryClient.invalidateQueries({ queryKey: ["profile"] });
      queryClient.invalidateQueries({ queryKey: ["profile", "raw"] });
    },
    onError: (err: Error) => pushToast("error", err.message),
  });

  const startEditing = () => {
    setDraft(data?.content ?? "");
    setEditBaseMtime(data?.mtime_ms ?? null);
    setEditing(true);
  };

  const isPulsing = Date.now() < pulseUntil;
  const lastUpdate = useMemo(() => {
    if (!data?.mtime_ms) return null;
    return new Date(data.mtime_ms);
  }, [data?.mtime_ms]);

  return (
    <div>
      <div className="-mt-2 mb-4 flex items-center justify-end gap-3 text-[10px] text-muted-foreground">
        {editing ? (
          <>
            <span className="mr-auto flex items-center gap-1.5 font-mono text-[10px] text-muted-foreground/80">
              <Lock className="h-3 w-3 text-primary/70" />
              {t("profile_view.raw_editing_hint")}
            </span>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setEditing(false)}
              disabled={save.isPending}
            >
              {t("profile_view.raw_cancel")}
            </Button>
            <Button
              size="sm"
              variant="default"
              onClick={() => save.mutate(draft)}
              disabled={save.isPending}
            >
              <Save className={cn("mr-1.5 h-3.5 w-3.5", save.isPending && "animate-pulse")} />
              {save.isPending ? t("profile_view.raw_saving") : t("profile_view.raw_save")}
            </Button>
          </>
        ) : (
          <>
            {isPulsing && (
              <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-500/15 px-2 py-0.5 text-[10px] font-semibold text-emerald-400">
                <span className="relative flex h-1.5 w-1.5">
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
                  <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-emerald-500" />
                </span>
                {t("profile_view.just_updated")}
              </span>
            )}
            {lastUpdate && (
              <span className="inline-flex items-center gap-1 font-mono">
                <Clock className="h-3 w-3" />
                {lastUpdate.toLocaleString()}
              </span>
            )}
            {data && <span className="font-mono">{(data.size_bytes / 1024).toFixed(1)} KB</span>}
            <Button
              size="sm"
              variant="ghost"
              onClick={() => refetch()}
              disabled={isRefetching}
              title={t("profile_view.reload_tooltip")}
            >
              <RefreshCw className={cn("h-3.5 w-3.5", isRefetching && "animate-spin")} />
            </Button>
            {data && (
              // Prominent, labelled entry point — a bare pencil icon was too
              // easy to miss. This is THE way to start editing USER.md.
              <Button
                size="sm"
                variant="outline"
                onClick={startEditing}
                className="border-primary/50 font-semibold text-primary hover:bg-primary/10 hover:text-primary"
              >
                <Pencil className="mr-1.5 h-3.5 w-3.5" />
                {t("profile_view.raw_edit")}
              </Button>
            )}
          </>
        )}
      </div>

      {isLoading && (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <RefreshCw className="h-3.5 w-3.5 animate-spin" /> {t("profile_view.raw_loading")}
        </div>
      )}

      {error && (
        <div className="rounded-2xl border border-destructive/40 bg-destructive/10 p-4 text-sm text-destructive">
          {error.message}
        </div>
      )}

      {data && (
        <div
          className={cn(
            "overflow-hidden rounded-2xl border bg-card/30 transition-shadow",
            editing ? "border-primary/40" : "border-border/70",
            isPulsing && !editing && "ring-1 ring-emerald-500/40 shadow-[0_0_20px_rgba(16,185,129,0.15)]",
          )}
        >
          <div className="flex items-center gap-2 border-b border-border/60 bg-secondary/40 px-3 py-2.5">
            <div className="flex gap-1.5" aria-hidden>
              <span className="h-2.5 w-2.5 rounded-full bg-destructive/50" />
              <span className="h-2.5 w-2.5 rounded-full bg-amber-400/50" />
              <span className="h-2.5 w-2.5 rounded-full bg-emerald-400/50" />
            </div>
            <FileText className="ml-1.5 h-3.5 w-3.5 text-primary" />
            <span className="truncate font-mono text-[10px] text-muted-foreground">{data.path}</span>
            <span className="ml-auto font-mono text-[10px] text-muted-foreground/50">
              {editing ? `${(new Blob([draft]).size / 1024).toFixed(1)} KB` : new Date(dataUpdatedAt).toLocaleTimeString()}
            </span>
          </div>
          {editing ? (
            <textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              spellCheck={false}
              autoFocus
              aria-label="USER.md"
              className="block h-[60vh] max-h-[640px] min-h-[360px] w-full resize-y bg-transparent p-4 font-mono text-[11px] leading-relaxed text-foreground/90 outline-none scrollbar-jarvis"
            />
          ) : (
            <pre className="max-h-[480px] overflow-auto whitespace-pre-wrap break-words p-4 font-mono text-[11px] leading-relaxed text-foreground/90 scrollbar-jarvis">
              {data.content || t("profile_view.raw_empty")}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}
