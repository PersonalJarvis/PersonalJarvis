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
  Mic,
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
import { BoardCard } from "@/components/board/BoardCard";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { useEventStore } from "@/store/events";
import { cn } from "@/lib/utils";
import { getWSClient } from "@/hooks/useWebSocket";
import { useT } from "@/i18n";
import {
  CLUSTER_FIELD_KEYS,
  CLUSTER_ORDER,
  TOTAL_FIELDS,
  acquaintanceStage,
  clusterFilledCount,
  collectOpenQuestions,
  countFilled,
  displayAddress,
  isEmptyValue,
  type ClusterId,
} from "@/views/profile/ledger";

// ----------------------------------------------------------------------
// Types — mirror the backend responses from profile_routes.py
// ----------------------------------------------------------------------

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
// Small shared helpers
// ----------------------------------------------------------------------

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

function clusterDataOf(meta: Record<string, unknown>, cid: ClusterId): Record<string, unknown> {
  const raw = meta[cid];
  return raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
}

/** Render a field value for display; booleans go through i18n yes/no. */
function renderValue(t: (k: string) => string, value: unknown): string {
  if (Array.isArray(value)) return value.map(String).join(" · ");
  if (typeof value === "boolean") {
    return value ? t("profile_view.value_yes") : t("profile_view.value_no");
  }
  return String(value ?? "");
}

function Dot() {
  return <span className="px-1.5 text-muted-foreground/40">·</span>;
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

  const meta = (data?.user.meta ?? {}) as Record<string, unknown>;

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
        <div className="flex-1 overflow-y-auto scrollbar-jarvis">
          <div className="mx-auto flex w-full max-w-[1100px] flex-col gap-4 p-5 lg:gap-5 lg:p-6">
            <div className="profile-rise" style={{ animationDelay: "0ms" }}>
              <HeroIntro data={data} meta={meta} />
            </div>

            <AskCard meta={meta} />

            <div className="profile-rise" style={{ animationDelay: "140ms" }}>
              <div className="mb-3 mt-2 px-1">
                <h3 className="font-display text-sm font-semibold">
                  {t("profile_view.section_knowledge")}
                </h3>
                <p className="text-xs text-muted-foreground">
                  {t("profile_view.section_knowledge_sub")}
                </p>
              </div>
              <div className="grid gap-4 lg:grid-cols-2 lg:gap-5">
                {CLUSTER_ORDER.map((cid) => (
                  <ClusterCard
                    key={cid}
                    cid={cid}
                    meta={meta}
                    className={cid === "relationship" ? "lg:col-span-2" : undefined}
                  />
                ))}
              </div>
            </div>

            <div className="profile-rise" style={{ animationDelay: "210ms" }}>
              <PeopleSection
                people={data.people}
                activeSlug={activeSlug}
                onSelect={setActiveSlug}
              />
            </div>

            <div className="profile-rise" style={{ animationDelay: "280ms" }}>
              <ReviewsCard reviewsCount={data.reviews_count} />
            </div>

            <div className="profile-rise" style={{ animationDelay: "350ms" }}>
              <SourceCard />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ----------------------------------------------------------------------
// Hero — an open stage, no container. Just the portrait, one warm
// sentence, and a single quiet summary line on the page background.
// The sidebar already frames the page; boxing the greeting again is what
// made earlier drafts feel manufactured.
// ----------------------------------------------------------------------

function HeroIntro({ data, meta }: { data: ProfileResponse; meta: Record<string, unknown> }) {
  const t = useT();
  const name = data.user.name?.trim() || null;

  const filled = useMemo(() => countFilled(meta), [meta]);
  const stage = acquaintanceStage(filled, TOTAL_FIELDS);

  // Address the user the way they asked to be addressed ("Chef"), falling
  // back to their first name — warmer than the full legal name.
  const address = displayAddress(meta, name);
  const headline = address
    ? t(`profile_view.stage_headline.${stage.key}`).replace("{0}", `, ${address}`)
    : t("profile_view.hero_name_placeholder");
  const sub = name ? t("profile_view.hero_sub") : t("profile_view.no_user_hint");

  const peopleLine =
    data.people.length === 1
      ? t("profile_view.person_known").replace("{0}", "1")
      : t("profile_view.people_known").replace("{0}", String(data.people.length));

  return (
    <header className="flex items-start gap-5 px-1 pb-3 pt-5 lg:gap-6 lg:pt-9">
      <AvatarBlock name={name} hasAvatar={!!data.has_avatar} />
      <div className="min-w-0">
        <h1 className="font-display text-[1.85rem] font-semibold leading-[1.12] tracking-tight lg:text-[2.35rem]">
          {headline}
        </h1>
        <p className="mt-2.5 max-w-xl text-sm leading-relaxed text-muted-foreground">{sub}</p>
        <p className="mt-3.5 flex flex-wrap items-center text-[13px] text-muted-foreground">
          <span className="font-medium text-foreground/90">
            {t("profile_view.entries_ratio")
              .replace("{0}", String(filled))
              .replace("{1}", String(TOTAL_FIELDS))}
          </span>
          <Dot />
          {t(`profile_view.stages.${stage.key}`)}
          {data.people.length > 0 && (
            <>
              <Dot />
              {peopleLine}
            </>
          )}
          {data.reviews_count > 0 && (
            <>
              <Dot />
              <span className="text-primary">
                {data.reviews_count} {t("profile_view.reviews_count")}
              </span>
            </>
          )}
        </p>
      </div>
    </header>
  );
}

// ----------------------------------------------------------------------
// AvatarBlock — round portrait; the photo itself is the upload trigger
// ----------------------------------------------------------------------
//
// The avatar bytes live under user_data_dir()/data and are served by
// GET /api/profile/avatar (see profile_routes.py). A hidden <input
// type="file"> is .click()'d to open the OS picker; a cache-bust query
// param forces the <img> to reload after a replace/delete.

function AvatarBlock({ name, hasAvatar }: { name: string | null; hasAvatar: boolean }) {
  const t = useT();
  const queryClient = useQueryClient();
  const pushToast = useEventStore((s) => s.pushToast);
  const inputRef = useRef<HTMLInputElement | null>(null);
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
    <div className="flex shrink-0 flex-col items-center gap-2">
      <input
        ref={inputRef}
        type="file"
        accept="image/png,image/jpeg,image/webp,image/gif"
        className="hidden"
        aria-hidden="true"
        tabIndex={-1}
        onChange={onFileChosen}
      />
      <button
        type="button"
        onClick={openPicker}
        disabled={busy}
        title={hasAvatar ? t("profile_view.avatar_change") : t("profile_view.avatar_upload")}
        aria-label={hasAvatar ? t("profile_view.avatar_change") : t("profile_view.avatar_upload")}
        className="group relative flex h-[4.5rem] w-[4.5rem] items-center justify-center overflow-hidden rounded-full border border-white/[0.09] bg-white/[0.04] outline-none transition-colors hover:border-primary/50 focus-visible:border-primary"
      >
        {hasAvatar ? (
          <img
            src={`/api/profile/avatar?t=${bust}`}
            alt={t("profile_view.avatar_alt")}
            className="h-full w-full object-cover"
            draggable={false}
          />
        ) : name ? (
          <span className="font-display text-xl font-semibold tracking-tight text-primary">
            {initials(name)}
          </span>
        ) : (
          <UserCircle2 className="h-8 w-8 text-muted-foreground/50" />
        )}

        {/* Hover/focus affordance — a camera scrim that invites the click. */}
        <span className="pointer-events-none absolute inset-0 flex items-center justify-center rounded-full bg-background/70 opacity-0 backdrop-blur-[1px] transition-opacity duration-200 group-hover:opacity-100 group-focus-visible:opacity-100">
          <Camera className="h-5 w-5 text-primary" />
        </span>

        {busy && (
          <span className="absolute inset-0 flex items-center justify-center rounded-full bg-background/70">
            <Loader2 className="h-5 w-5 animate-spin text-primary" />
          </span>
        )}
      </button>

      {/* Always-visible controls — no hover hunting. */}
      <div className="flex items-center gap-1">
        <button
          type="button"
          onClick={openPicker}
          disabled={busy}
          className="rounded-full border border-white/[0.08] bg-white/[0.03] px-2.5 py-1 text-[10px] font-medium text-muted-foreground transition-colors hover:border-primary/40 hover:text-primary"
        >
          {hasAvatar ? t("profile_view.avatar_change") : t("profile_view.avatar_upload")}
        </button>
        {hasAvatar && (
          <button
            type="button"
            onClick={() => remove.mutate()}
            disabled={busy}
            title={t("profile_view.avatar_remove")}
            aria-label={t("profile_view.avatar_remove")}
            className="rounded-full border border-white/[0.08] bg-white/[0.03] p-1.5 text-muted-foreground transition-colors hover:border-destructive/40 hover:text-destructive"
          >
            <Trash2 className="h-3 w-3" />
          </button>
        )}
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------
// Ask card — ONE question at a time, with the sentence to speak
// ----------------------------------------------------------------------

function AskCard({ meta }: { meta: Record<string, unknown> }) {
  const t = useT();
  const [idx, setIdx] = useState(0);

  const open = useMemo(() => collectOpenQuestions(meta, TOTAL_FIELDS), [meta]);
  if (open.length === 0) return null;

  const q = open[idx % open.length];

  return (
    <div className="profile-rise" style={{ animationDelay: "70ms" }}>
      <BoardCard className="p-5 lg:p-6">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-x-1 text-xs text-muted-foreground">
              <Sparkles className="mr-1 h-3.5 w-3.5 text-primary" />
              {t("profile_view.ask_title")}
              <Dot />
              {t(`profile_view.clusters.${q.cluster}.label`)}
            </div>
            <p className="mt-2 font-display text-lg font-semibold tracking-tight lg:text-xl">
              {t(`profile_view.questions.${q.field}`)}
            </p>
            <div className="mt-3 inline-flex max-w-full items-center gap-2 rounded-full border border-primary/25 bg-primary/[0.07] px-3 py-1.5 text-xs text-primary">
              <Mic className="h-3.5 w-3.5 shrink-0" />
              <span className="truncate">
                {t("profile_view.ask_say_prefix")}: “{t(`profile_view.says.${q.field}`)}”
              </span>
            </div>
          </div>

          <div className="flex shrink-0 flex-col items-end gap-2">
            <span className="text-[11px] tabular-nums text-muted-foreground">
              {(idx % open.length) + 1}/{open.length}
            </span>
            <button
              type="button"
              data-testid="ask-next"
              onClick={() => setIdx((i) => i + 1)}
              className="inline-flex items-center gap-1 rounded-full border border-white/[0.08] bg-white/[0.03] px-3 py-1.5 text-xs font-medium transition-colors hover:border-primary/40 hover:bg-primary/[0.06]"
            >
              {t("profile_view.ask_next")}
              <ChevronRight className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
      </BoardCard>
    </div>
  );
}

// ----------------------------------------------------------------------
// Cluster cards — quiet rows; learned first, blanks capped at two
// ----------------------------------------------------------------------

function ClusterCard({
  cid,
  meta,
  className,
}: {
  cid: ClusterId;
  meta: Record<string, unknown>;
  className?: string;
}) {
  const t = useT();
  const data = clusterDataOf(meta, cid);
  const fields = CLUSTER_FIELD_KEYS[cid];
  const filled = clusterFilledCount(meta, cid);

  const known = fields.filter((k) => !isEmptyValue(data[k]));
  const blanks = fields.filter((k) => isEmptyValue(data[k]));
  const blanksShown = blanks.slice(0, 2);
  const blanksHidden = blanks.length - blanksShown.length;

  return (
    <BoardCard className={cn("p-5", className)}>
      <div className="flex items-baseline justify-between gap-3">
        <h4 className="font-display text-sm font-semibold">
          {t(`profile_view.clusters.${cid}.label`)}
        </h4>
        <span className="text-[11px] tabular-nums text-muted-foreground">
          {filled}/{fields.length}
        </span>
      </div>
      <p className="mt-0.5 text-xs text-muted-foreground">
        {t(`profile_view.clusters.${cid}.description`)}
      </p>

      <dl className="mt-3">
        {known.map((key) => (
          <div
            key={key}
            className="flex items-baseline justify-between gap-4 border-b border-white/[0.05] py-2 last:border-b-0"
          >
            <dt className="shrink-0 text-xs text-muted-foreground">
              {t(`profile_view.fields.${key}`)}
            </dt>
            <dd className="max-w-[65%] text-right text-sm font-medium leading-snug [overflow-wrap:anywhere]">
              {renderValue(t, data[key])}
            </dd>
          </div>
        ))}
        {blanksShown.map((key) => (
          <div
            key={key}
            className="flex items-baseline justify-between gap-4 border-b border-white/[0.05] py-2 last:border-b-0"
          >
            <dt className="shrink-0 text-xs text-muted-foreground/60">
              {t(`profile_view.fields.${key}`)}
            </dt>
            {/* Blank fields stay plainly readable — nothing is concealed,
                they simply haven't been learned yet. */}
            <dd className="text-right text-xs italic text-muted-foreground/45">
              {t("profile_view.field_unknown")}
            </dd>
          </div>
        ))}
      </dl>
      {blanksHidden > 0 && (
        <div className="pt-2.5 text-[11px] text-muted-foreground/60">
          {t("profile_view.more_to_learn").replace("{0}", String(blanksHidden))}
        </div>
      )}
    </BoardCard>
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
        <BoardCard glow className="max-w-md p-8 text-center">
          <div className="mx-auto mb-5 flex h-14 w-14 items-center justify-center rounded-full border border-white/[0.08] bg-white/[0.04]">
            <UserCircle2 className="h-6 w-6 text-primary" />
          </div>
          <h3 className="font-display text-lg font-semibold tracking-tight">
            {t("profile_view.hero_name_placeholder")}
          </h3>
          <p className="mt-2 text-sm text-muted-foreground">{error.message}</p>
          <p className="mt-4 text-xs text-muted-foreground/70">
            {t("profile_view.no_user_hint")}
          </p>
          <Button className="mt-6" size="sm" variant="outline" onClick={onRetry}>
            <RefreshCw className="mr-2 h-3.5 w-3.5" /> {t("apikeys_view.retry")}
          </Button>
        </BoardCard>
      </div>
    );
  }
  return (
    <div className="p-6">
      <div className="rounded-xl border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">
        {t("common.error_generic")}: {error.message}
        <button className="ml-2 underline" onClick={onRetry}>
          {t("apikeys_view.retry")}
        </button>
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------
// People — quiet list rows + detail card
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
  const t = useT();
  const active = useMemo(
    () => people.find((p) => p.slug === activeSlug) ?? null,
    [people, activeSlug],
  );

  return (
    <div className={cn("grid gap-4 lg:gap-5", people.length > 0 && "lg:grid-cols-[1fr_320px]")}>
      <BoardCard className="p-5">
        <div className="flex items-baseline justify-between gap-3">
          <h3 className="font-display text-sm font-semibold">
            {t("profile_view.section_people")}
          </h3>
          {people.length > 0 && (
            <span className="text-[11px] tabular-nums text-muted-foreground">
              {people.length}
            </span>
          )}
        </div>

        {people.length === 0 ? (
          <EmptyHint
            icon={UsersIcon}
            title={t("profile_view.people_empty_title")}
            body={t("profile_view.people_empty_body")}
          />
        ) : (
          <ul className="-mx-2 mt-2.5">
            {people.map((p) => (
              <PersonRow
                key={p.slug}
                person={p}
                active={p.slug === activeSlug}
                onClick={() => onSelect(p.slug === activeSlug ? null : p.slug)}
              />
            ))}
          </ul>
        )}
      </BoardCard>

      {people.length > 0 && <PersonDetail person={active} onClose={() => onSelect(null)} />}
    </div>
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
          "flex w-full items-center gap-3 rounded-xl px-2.5 py-2.5 text-left transition-colors",
          active ? "bg-primary/[0.07]" : "hover:bg-white/[0.04]",
        )}
      >
        <span
          className={cn(
            "flex h-9 w-9 shrink-0 items-center justify-center rounded-full border text-xs font-semibold transition-colors",
            active
              ? "border-primary/40 bg-primary/15 text-primary"
              : "border-white/[0.08] bg-white/[0.05] text-foreground/80",
          )}
        >
          {initials(person.name)}
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex items-baseline gap-2">
            <span className="truncate text-sm font-medium">{person.name}</span>
            {person.aliases.length > 0 && (
              <span className="truncate text-[11px] italic text-muted-foreground">
                aka {person.aliases.join(", ")}
              </span>
            )}
          </span>
        </span>
        <span className="shrink-0 rounded-full border border-white/[0.08] bg-white/[0.03] px-2 py-0.5 text-[10px] text-muted-foreground">
          {person.relationship}
        </span>
        <ChevronRight
          className={cn(
            "h-4 w-4 shrink-0 transition-all",
            active ? "translate-x-0.5 text-primary" : "text-muted-foreground/40",
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
      <BoardCard className="hidden items-center justify-center p-6 lg:flex">
        <div className="text-center text-xs text-muted-foreground">
          {t("profile_view.person_detail_hint")}
        </div>
      </BoardCard>
    );
  }

  return (
    <BoardCard className="h-fit p-5">
      <div className="mb-4 flex items-start gap-3">
        <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full border border-primary/30 bg-primary/10 text-sm font-semibold text-primary">
          {initials(person.name)}
        </div>
        <div className="min-w-0 flex-1">
          <div className="truncate font-display text-base font-semibold">{person.name}</div>
          <div className="text-xs text-muted-foreground">{person.relationship}</div>
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

      <dl className="space-y-3 border-t border-white/[0.06] pt-4 text-xs">
        <div className="flex items-baseline justify-between gap-3">
          <dt className="text-muted-foreground">{t("profile_view.person_relationship")}</dt>
          <dd className="font-medium text-foreground">{person.relationship}</dd>
        </div>
        <div className="flex items-baseline justify-between gap-3">
          <dt className="text-muted-foreground">{t("profile_view.person_aliases")}</dt>
          <dd className="text-right">
            {person.aliases.length === 0 ? (
              <span className="italic text-muted-foreground/60">
                {t("profile_view.person_no_aliases")}
              </span>
            ) : (
              <span className="text-foreground/90">{person.aliases.join(" · ")}</span>
            )}
          </dd>
        </div>
        <div className="flex items-baseline justify-between gap-3">
          <dt className="text-muted-foreground">Slug</dt>
          <dd className="truncate font-mono text-foreground">{person.slug}</dd>
        </div>
      </dl>

      <p className="mt-4 flex items-start gap-2 rounded-xl bg-white/[0.03] p-2.5 text-[10px] leading-relaxed text-muted-foreground">
        <Inbox className="mt-0.5 h-3 w-3 shrink-0" />
        {t("profile_view.person_file_hint").replace("{0}", person.slug)}
      </p>
    </BoardCard>
  );
}

// ----------------------------------------------------------------------
// Reviews — observations waiting for the user's OK
// ----------------------------------------------------------------------

function ReviewsCard({ reviewsCount }: { reviewsCount: number }) {
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
    <BoardCard className="p-5">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="flex items-baseline gap-2.5">
          <h3 className="font-display text-sm font-semibold">
            {t("profile_view.section_reviews")}
          </h3>
          {reviewsCount > 0 && (
            <span className="rounded-full border border-primary/30 bg-primary/[0.08] px-2 py-0.5 text-[10px] font-medium tabular-nums text-primary">
              {reviewsCount} {t("profile_view.review_open")}
            </span>
          )}
        </div>
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
          <EmptyHint
            icon={ShieldQuestion}
            title={t("profile_view.reviews_disabled_title")}
            body={t("profile_view.reviews_disabled_body")}
            testId="reviews-disabled"
          />
        ) : (
          <div
            data-testid="reviews-error"
            className="rounded-xl border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive"
          >
            {error.message}
          </div>
        ))}

      {data && items.length === 0 && (
        <EmptyHint
          icon={ShieldQuestion}
          title={t("profile_view.reviews_empty_title")}
          body={t("profile_view.reviews_empty_body")}
        />
      )}

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
    </BoardCard>
  );
}

// Calm in-card empty state shared by People and Reviews.
function EmptyHint({
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
    <div data-testid={testId} className="mt-2 flex items-center gap-3.5 py-2">
      <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full border border-white/[0.07] bg-white/[0.03]">
        <Icon className="h-4.5 w-4.5 h-[18px] w-[18px] text-muted-foreground" />
      </div>
      <div className="min-w-0 flex-1">
        <div className="text-sm font-medium">{title}</div>
        <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">{body}</p>
      </div>
    </div>
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

  return (
    <li className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4 transition-colors hover:border-primary/25">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1 space-y-2">
          {candidate.evidence && (
            <blockquote className="text-sm italic leading-snug text-foreground/90">
              “{candidate.evidence}”
            </blockquote>
          )}
          <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
            <span>
              {candidate.is_person
                ? `${t("profile_view.review_subject_user")} → ${candidate.person_name}`
                : t("profile_view.review_subject_user")}
            </span>
            <Dot />
            <span>{candidate.cluster}</span>
            <ChevronRight className="h-3 w-3 text-muted-foreground/40" />
            <span className="font-medium text-foreground">{candidate.field}</span>
            <Badge variant="outline" className="text-[10px]">
              {candidate.operation}
            </Badge>
          </div>
          <div className="text-sm">
            <span className="text-muted-foreground">{t("profile_view.review_value")}: </span>
            <span className="font-medium text-foreground [overflow-wrap:anywhere]">
              {renderValue(t, candidate.value) || "—"}
            </span>
          </div>
          {candidate.reason && (
            <p className="text-[11px] text-muted-foreground/70">
              {t("profile_view.review_reason")}: {candidate.reason}
            </p>
          )}
        </div>

        <div className="flex shrink-0 flex-col items-end gap-2.5">
          <span className={cn("text-sm font-semibold tabular-nums", confColor)}>
            {(conf * 100).toFixed(0)}%
          </span>
          <div className="flex gap-1.5">
            <Button
              size="sm"
              variant="default"
              disabled={pending}
              onClick={onAccept}
              title={t("profile_view.accept_tooltip")}
            >
              <Check className="mr-1.5 h-3.5 w-3.5" />
              {t("profile_view.review_confirm")}
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={pending}
              onClick={onReject}
              title={t("profile_view.reject_tooltip")}
            >
              <X className="mr-1.5 h-3.5 w-3.5" />
              {t("profile_view.review_strike")}
            </Button>
          </div>
        </div>
      </div>
    </li>
  );
}

// ----------------------------------------------------------------------
// Source card — live USER.md with in-place editing
// ----------------------------------------------------------------------
//
// Data flow: GET /api/profile/raw → React-Query cache. Live sync via WS:
// every Curator merge publishes ProfileUpdated on the bus, the WS server
// streams it to the UI, and the subscriber below invalidates both profile
// queries — the file content is current seconds after a write. The pulse
// badge gives visual feedback when an update lands.

interface RawProfileResponse {
  content: string;
  path: string;
  mtime_ms: number | null;
  size_bytes: number;
}

function SourceCard() {
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

  // Live-subscribe to ProfileUpdated events from the bus.
  useEffect(() => {
    const client = getWSClient();
    if (!client) return;
    const unsubscribe = client.subscribe((raw) => {
      const env = raw as { event_name?: unknown };
      if (env.event_name !== "ProfileUpdated") return;
      // Knowledge cards always refresh…
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
    <BoardCard className="p-5">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <h3 className="font-display text-sm font-semibold">
          {t("profile_view.section_source")}
        </h3>

        <div className="flex items-center gap-2.5 text-[11px] text-muted-foreground">
          {editing ? (
            <>
              <span className="flex items-center gap-1.5">
                <Lock className="h-3 w-3 text-primary/70" />
                <span className="hidden sm:inline">{t("profile_view.raw_editing_hint")}</span>
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
                <span className="hidden items-center gap-1 sm:inline-flex">
                  <Clock className="h-3 w-3" />
                  {lastUpdate.toLocaleString()}
                </span>
              )}
              {data && <span>{(data.size_bytes / 1024).toFixed(1)} KB</span>}
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
      </div>

      {isLoading && (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <RefreshCw className="h-3.5 w-3.5 animate-spin" /> {t("profile_view.raw_loading")}
        </div>
      )}

      {error && (
        <div className="rounded-xl border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">
          {error.message}
        </div>
      )}

      {data && (
        <div
          className={cn(
            "overflow-hidden rounded-xl border bg-black/30 transition-shadow",
            editing ? "border-primary/40" : "border-white/[0.06]",
            isPulsing && !editing && "ring-1 ring-emerald-500/40 shadow-[0_0_20px_rgba(16,185,129,0.15)]",
          )}
        >
          <div className="flex items-center gap-2.5 border-b border-white/[0.05] bg-white/[0.02] px-3.5 py-2.5">
            <FileText className="h-3.5 w-3.5 text-primary" />
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
    </BoardCard>
  );
}
