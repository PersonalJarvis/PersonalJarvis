/**
 * ProfileView — the profile as ONE view, never a scrolling page.
 *
 * Layout doctrine: the whole section is a single viewport. Nothing here is
 * reached by scrolling the page; the view is three standing rails that each
 * scroll inside themselves only when their own content overflows.
 *
 *   ┌───────────────────────────────────────────────────────────────────┐
 *   │ ViewHeader                                                        │
 *   ├──────────────┬────────────────────────────┬───────────────────────┤
 *   │  The Plate   │  The Ledger                │  The Margin           │
 *   │  sigil +     │  every cluster, every      │  the open question,   │
 *   │  portrait,   │  field, editable in place  │  the review queue,    │
 *   │  who you are │                            │  the people           │
 *   └──────────────┴────────────────────────────┴───────────────────────┘
 *
 * The single non-negotiable of that shape is an unbroken `min-h-0` chain from
 * the root to each rail: a flex/grid child defaults to `min-height: auto` and
 * refuses to shrink below its content, which is what silently turns a
 * "one viewport" layout back into a scrolling page.
 *
 * USER.md is the one thing that cannot honestly fit a rail, so it opens as a
 * drawer over the view instead of living at the bottom of a long page.
 *
 * The mark in the plate is `Sigil` — geometry derived from which fields are
 * inked, delivering the generative mark `views/profile/ledger.ts` describes.
 */
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
  ChevronDown,
  Lock,
  Pencil,
  Plus,
  Save,
  Camera,
  Trash2,
  Loader2,
} from "lucide-react";
import { ViewHeader } from "@/views/ChatsView";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { useEventStore } from "@/store/events";
import { cn } from "@/lib/utils";
import { getWSClient } from "@/hooks/useWebSocket";
import { useT } from "@/i18n";
import { Sigil } from "@/views/profile/Sigil";
import {
  CLUSTER_FIELD_KEYS,
  CLUSTER_ORDER,
  TOTAL_FIELDS,
  acquaintanceStage,
  clusterFilledCount,
  collectOpenQuestions,
  countFilled,
  displayAddress,
  fieldKind,
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

interface RawProfileResponse {
  content: string;
  path: string;
  mtime_ms: number | null;
  size_bytes: number;
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

/** A rail's section heading — small caps, hairline rule, no box. */
function RailHeading({
  title,
  count,
  right,
}: {
  title: string;
  count?: string | number;
  right?: React.ReactNode;
}) {
  return (
    <div className="flex items-baseline gap-2 border-b border-sheen/[0.06] pb-1.5">
      <h3 className="font-display text-[11px] font-semibold uppercase tracking-[0.13em] text-muted-foreground">
        {title}
      </h3>
      {count !== undefined && count !== "" && (
        <span className="text-[11px] tabular-nums text-muted-foreground/60">{count}</span>
      )}
      <span className="ml-auto flex items-center">{right}</span>
    </div>
  );
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

  const [sourceOpen, setSourceOpen] = useState(false);
  // Pointing at a field in the ledger lights its spoke in the sigil, which
  // makes the mark a live legend instead of an ornament.
  const [hoveredField, setHoveredField] = useState<string | null>(null);

  const meta = (data?.user.meta ?? {}) as Record<string, unknown>;

  return (
    <div className="relative flex h-full flex-col overflow-hidden">
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
        // One viewport. On lg+ the three rails stand side by side and scroll
        // individually; below that they stack and the container takes over the
        // scrolling — the honest degradation for a window too narrow to hold
        // three rails at a readable width.
        <div
          className={cn(
            "grid min-h-0 flex-1 grid-cols-1 overflow-y-auto scrollbar-jarvis",
            "lg:grid-cols-[minmax(250px,300px)_minmax(0,1fr)_minmax(290px,340px)]",
            "lg:overflow-hidden",
          )}
        >
          <PlateRail
            data={data}
            meta={meta}
            highlight={hoveredField}
            onOpenSource={() => setSourceOpen(true)}
          />
          <LedgerRail meta={meta} onHoverField={setHoveredField} />
          <MarginRail data={data} meta={meta} />
        </div>
      )}

      {sourceOpen && <SourceDrawer onClose={() => setSourceOpen(false)} />}
    </div>
  );
}

// ----------------------------------------------------------------------
// The Plate — portrait inside the generative sigil, and who you are
// ----------------------------------------------------------------------
//
// The left rail is deliberately the only region that never scrolls on a
// normal window: it is the fixed identity of the view, the thing the eye
// returns to. Everything in it is derived, nothing is a decorative filler.

function PlateRail({
  data,
  meta,
  highlight,
  onOpenSource,
}: {
  data: ProfileResponse;
  meta: Record<string, unknown>;
  highlight: string | null;
  onOpenSource: () => void;
}) {
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

  const ratio = t("profile_view.entries_ratio")
    .replace("{0}", String(filled))
    .replace("{1}", String(TOTAL_FIELDS));

  const peopleLine =
    data.people.length === 1
      ? t("profile_view.person_known").replace("{0}", "1")
      : t("profile_view.people_known").replace("{0}", String(data.people.length));

  return (
    <aside className="profile-rise flex flex-col px-6 py-6 lg:min-h-0 lg:overflow-y-auto lg:py-8 scrollbar-jarvis">
      {/* The plate floats in the middle of its rail; only the source-file link
          is anchored to the foot. Top-aligning it left a tall empty gap that
          read as unfinished rather than as breathing room. */}
      <div className="flex flex-col gap-5 lg:my-auto">
        {/* The mark. The sigil's ring reads the ledger; the portrait sits in it. */}
        <div className="relative mx-auto h-[188px] w-[188px] shrink-0">
          <Sigil
            meta={meta}
            label={ratio}
            highlight={highlight}
            className="absolute inset-0 h-full w-full text-foreground"
          />
          <div className="absolute inset-0 flex items-center justify-center">
            <AvatarBlock name={name} hasAvatar={!!data.has_avatar} />
          </div>
        </div>

        <div className="min-w-0 text-center">
          <h1 className="font-display text-[1.45rem] font-semibold leading-[1.15] tracking-tight">
            {headline}
          </h1>
          <p className="mt-1.5 text-[11px] font-medium uppercase tracking-[0.13em] text-muted-foreground">
            {t(`profile_view.stages.${stage.key}`)}
          </p>
        </div>

        {/* One quiet summary line, then the two counters that matter. */}
        <div className="flex flex-col items-center gap-2.5 border-y border-sheen/[0.06] py-3.5">
          <span className="text-[13px] font-medium tabular-nums text-foreground/90">{ratio}</span>
          <span className="flex items-center text-[11px] text-muted-foreground">
            {peopleLine}
            {data.reviews_count > 0 && (
              <>
                <Dot />
                <span className="font-medium text-foreground/90">
                  {data.reviews_count} {t("profile_view.reviews_count")}
                </span>
              </>
            )}
          </span>
        </div>

        <p className="text-center text-xs leading-relaxed text-muted-foreground">
          {name ? t("profile_view.hero_sub") : t("profile_view.no_user_hint")}
        </p>
      </div>

      {/* USER.md — the raw truth behind every row, one click away. */}
      <button
        type="button"
        onClick={onOpenSource}
        className="mt-6 flex items-center gap-2.5 rounded-xl border border-sheen/[0.07] bg-sheen/[0.02] px-3.5 py-3 text-left transition-colors hover:border-primary/40 hover:bg-sheen/[0.05] lg:mt-0"
      >
        <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
        <span className="min-w-0 flex-1">
          <span className="block text-xs font-medium">{t("profile_view.section_source")}</span>
          <span className="block truncate font-mono text-[10px] text-muted-foreground">
            {data.user.path}
          </span>
        </span>
        <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground/50" />
      </button>
    </aside>
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
//
// Sitting at the centre of the sigil, it carries no chrome of its own: the
// remove control only appears on hover/focus so the mark stays clean.

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
    <div className="group/avatar relative">
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
        className="relative flex h-[7rem] w-[7rem] items-center justify-center overflow-hidden rounded-full border border-sheen/[0.09] bg-sheen/[0.04] outline-none transition-colors hover:border-primary/50 focus-visible:border-primary"
      >
        {hasAvatar ? (
          <img
            src={`/api/profile/avatar?t=${bust}`}
            alt={t("profile_view.avatar_alt")}
            className="h-full w-full object-cover"
            draggable={false}
          />
        ) : name ? (
          <span className="font-display text-2xl font-semibold tracking-tight text-foreground/80">
            {initials(name)}
          </span>
        ) : (
          <UserCircle2 className="h-9 w-9 text-muted-foreground/50" />
        )}

        {/* Hover/focus affordance — a camera scrim that invites the click. */}
        <span className="pointer-events-none absolute inset-0 flex items-center justify-center rounded-full bg-background/70 opacity-0 backdrop-blur-[1px] transition-opacity duration-200 group-hover/avatar:opacity-100 group-focus-visible/avatar:opacity-100">
          <Camera className="h-5 w-5 text-primary" />
        </span>

        {busy && (
          <span className="absolute inset-0 flex items-center justify-center rounded-full bg-background/70">
            <Loader2 className="h-5 w-5 animate-spin text-primary" />
          </span>
        )}
      </button>

      {hasAvatar && (
        <button
          type="button"
          onClick={() => remove.mutate()}
          disabled={busy}
          title={t("profile_view.avatar_remove")}
          aria-label={t("profile_view.avatar_remove")}
          className="absolute bottom-0 right-0 rounded-full border border-sheen/[0.1] bg-background p-1.5 text-muted-foreground opacity-0 outline-none transition-all hover:border-destructive/40 hover:text-destructive focus-visible:opacity-100 group-hover/avatar:opacity-100"
        >
          <Trash2 className="h-3 w-3" />
        </button>
      )}
    </div>
  );
}

// ----------------------------------------------------------------------
// The Ledger — every cluster, every field, nothing behind a tab
// ----------------------------------------------------------------------
//
// The old view capped blanks at two per card and stacked five cards down a
// scrolling page. In a one-viewer the opposite is right: eighteen fields fit
// a viewport comfortably in two columns, so everything the ledger holds is
// visible at once and a blank line is as informative as a written one.

function LedgerRail({
  meta,
  onHoverField,
}: {
  meta: Record<string, unknown>;
  onHoverField: (field: string | null) => void;
}) {
  const t = useT();

  return (
    <section className="flex min-w-0 flex-col border-t border-border px-6 py-6 lg:min-h-0 lg:overflow-y-auto lg:border-l lg:border-t-0 lg:py-8 scrollbar-jarvis">
      <div className="profile-rise" style={{ animationDelay: "60ms" }}>
        <h2 className="font-display text-base font-semibold tracking-tight">
          {t("profile_view.section_knowledge")}
        </h2>
        <p className="mt-0.5 text-xs text-muted-foreground">
          {t("profile_view.section_knowledge_sub")}
        </p>
      </div>

      {/* One continuous column, capped at a comfortable measure. Two balanced
          columns halved the content's height and left the lower half of the
          viewport empty; a single run of clusters fills the rail the way a
          ledger page fills a sheet, and the leader dots make the wide rows
          read cleanly. */}
      <div className="mt-5 max-w-[46rem]">
        {CLUSTER_ORDER.map((cid, i) => (
          <div
            key={cid}
            className="profile-rise mb-6 last:mb-0"
            style={{ animationDelay: `${100 + i * 45}ms` }}
          >
            <ClusterGroup cid={cid} meta={meta} onHoverField={onHoverField} />
          </div>
        ))}
      </div>
    </section>
  );
}

function ClusterGroup({
  cid,
  meta,
  onHoverField,
}: {
  cid: ClusterId;
  meta: Record<string, unknown>;
  onHoverField: (field: string | null) => void;
}) {
  const t = useT();
  const data = clusterDataOf(meta, cid);
  const fields = CLUSTER_FIELD_KEYS[cid];
  const filled = clusterFilledCount(meta, cid);

  return (
    <div>
      <RailHeading
        title={t(`profile_view.clusters.${cid}.label`)}
        count={`${filled}/${fields.length}`}
      />
      <p className="mt-1.5 text-[11px] leading-relaxed text-muted-foreground/80">
        {t(`profile_view.clusters.${cid}.description`)}
      </p>
      {/* Every row carries its own quiet pencil — learned fields can be
          overwritten or cleared, blank ones filled in, all edited in place. */}
      <dl className="mt-1.5">
        {fields.map((key) => (
          <EditableFieldRow
            key={key}
            cid={cid}
            fieldKey={key}
            value={data[key]}
            onHover={onHoverField}
          />
        ))}
      </dl>
    </div>
  );
}

// ----------------------------------------------------------------------
// Inline field editing — a quiet pencil per field, edited in place
// ----------------------------------------------------------------------

type FieldOp = "set" | "clear" | "append" | "remove";

/** Shared mutation for PATCH /api/profile/field. Invalidates the profile query
 *  on success so the card re-renders with the persisted value. */
function useFieldEdit() {
  const queryClient = useQueryClient();
  const pushToast = useEventStore((s) => s.pushToast);
  return useMutation({
    mutationFn: async (body: {
      cluster: ClusterId;
      field: string;
      operation: FieldOp;
      value?: unknown;
    }) => {
      const res = await fetch("/api/profile/field", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const data = (await res.json().catch(() => ({}))) as { detail?: string };
        throw new Error(data.detail ?? `HTTP ${res.status}`);
      }
      return res.json();
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["profile"] }),
    onError: (err: Error) => pushToast("error", err.message),
  });
}

function IconBtn({
  icon: Icon,
  onClick,
  title,
  tone = "muted",
  disabled,
}: {
  icon: typeof Check;
  onClick: () => void;
  title: string;
  tone?: "muted" | "primary" | "danger";
  disabled?: boolean;
}) {
  const toneCls =
    tone === "primary"
      ? "text-primary hover:bg-primary/10"
      : tone === "danger"
        ? "text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
        : "text-muted-foreground hover:bg-sheen/[0.06] hover:text-foreground";
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      aria-label={title}
      disabled={disabled}
      className={cn(
        "shrink-0 rounded p-1 outline-none transition-colors focus-visible:ring-1 focus-visible:ring-primary/50 disabled:opacity-40",
        toneCls,
      )}
    >
      <Icon className="h-3.5 w-3.5" />
    </button>
  );
}

/** One field row with inline editing: a hover-revealed pencil opens an in-place
 *  editor whose shape depends on the field kind (scalar text, yes/no toggle, or
 *  removable chips for lists). Persists through PATCH /api/profile/field. */
function EditableFieldRow({
  cid,
  fieldKey,
  value,
  onHover,
}: {
  cid: ClusterId;
  fieldKey: string;
  value: unknown;
  /** Reports the pointed-at field up to the sigil. Keyboard focus counts. */
  onHover?: (field: string | null) => void;
}) {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const edit = useFieldEdit();
  const kind = fieldKind(fieldKey);
  const empty = isEmptyValue(value);
  const label = t(`profile_view.fields.${fieldKey}`);

  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (editing) inputRef.current?.focus();
  }, [editing]);

  const busy = edit.isPending;
  const toast = () => pushToast("success", t("profile_view.field_saved"));

  const startEdit = () => {
    setDraft(kind === "scalar" && !empty ? String(value) : "");
    setEditing(true);
  };
  const cancel = () => {
    setDraft("");
    setEditing(false);
  };
  const mutate = (
    operation: FieldOp,
    v?: unknown,
    opts?: { keepOpen?: boolean; clearDraft?: boolean },
  ) => {
    edit.mutate(
      { cluster: cid, field: fieldKey, operation, value: v },
      {
        onSuccess: () => {
          toast();
          if (opts?.clearDraft) setDraft("");
          if (opts?.keepOpen) inputRef.current?.focus();
          else cancel();
        },
      },
    );
  };

  const saveScalar = () => {
    const v = draft.trim();
    if (!v) {
      mutate("clear");
    } else {
      mutate("set", v);
    }
  };
  const addItem = () => {
    const v = draft.trim();
    if (v) mutate("append", v, { keepOpen: true, clearDraft: true });
  };

  // ------------------------------------------------------------------ display
  if (!editing) {
    return (
      <div
        className="group flex items-baseline gap-2 py-[0.3rem]"
        onMouseEnter={() => onHover?.(fieldKey)}
        onMouseLeave={() => onHover?.(null)}
        onFocus={() => onHover?.(fieldKey)}
        onBlur={() => onHover?.(null)}
      >
        <dt
          className={cn(
            "shrink-0 text-xs transition-colors",
            empty
              ? "text-muted-foreground/55 group-hover:text-muted-foreground"
              : "text-muted-foreground group-hover:text-foreground",
          )}
        >
          {label}
        </dt>
        {/* Leader dots. An empty baseline-aligned flex item puts its bottom
            border exactly on the text baseline, which is what carries the eye
            from a field's name across to its value — and what makes the
            column read as a ledger line rather than a wide, empty row. */}
        <span
          aria-hidden="true"
          className="min-w-[1rem] flex-1 border-b border-dotted border-sheen/[0.18] transition-colors group-hover:border-sheen/[0.32]"
        />
        <dd className="flex min-w-0 max-w-[68%] items-center justify-end gap-1">
          {kind === "list" && !empty ? (
            <div className="flex flex-wrap justify-end gap-1">
              {(value as unknown[]).map((item) => (
                <span
                  key={String(item)}
                  className="rounded-full border border-sheen/[0.08] bg-sheen/[0.04] px-2 py-0.5 text-[11px] font-medium"
                >
                  {String(item)}
                </span>
              ))}
            </div>
          ) : (
            <span
              className={cn(
                "leading-snug [overflow-wrap:anywhere]",
                empty ? "text-xs italic text-muted-foreground/45" : "text-[13px] font-medium",
              )}
            >
              {empty ? t("profile_view.field_unknown") : renderValue(t, value)}
            </span>
          )}
          {/* Quiet pencil — appears on row hover / keyboard focus only. */}
          <button
            type="button"
            onClick={startEdit}
            title={t("profile_view.field_edit")}
            aria-label={`${t("profile_view.field_edit")}: ${label}`}
            className="shrink-0 rounded p-1 text-muted-foreground opacity-0 outline-none transition-opacity hover:text-primary focus-visible:opacity-100 focus-visible:ring-1 focus-visible:ring-primary/50 group-hover:opacity-100"
          >
            <Pencil className="h-3 w-3" />
          </button>
        </dd>
      </div>
    );
  }

  // ------------------------------------------------------------------- editing
  return (
    <div className="group flex items-baseline justify-between gap-3 py-[0.3rem]">
      <dt className="shrink-0 text-xs text-foreground">{label}</dt>
      <dd className="flex min-w-0 max-w-[68%] flex-col items-end gap-1.5">
        {kind === "bool" ? (
          <div className="flex items-center gap-1">
            {([true, false] as const).map((b) => (
              <button
                key={String(b)}
                type="button"
                disabled={busy}
                onClick={() => mutate("set", b)}
                className={cn(
                  "rounded-full border px-2.5 py-0.5 text-xs font-medium transition-colors disabled:opacity-40",
                  value === b
                    ? "border-primary/50 bg-primary/[0.12] text-primary"
                    : "border-sheen/[0.08] bg-sheen/[0.03] text-muted-foreground hover:border-primary/40",
                )}
              >
                {b ? t("profile_view.value_yes") : t("profile_view.value_no")}
              </button>
            ))}
            <IconBtn icon={X} onClick={cancel} title={t("profile_view.raw_cancel")} disabled={busy} />
            {!empty && (
              <IconBtn
                icon={Trash2}
                onClick={() => mutate("clear")}
                title={t("profile_view.field_clear")}
                tone="danger"
                disabled={busy}
              />
            )}
          </div>
        ) : kind === "list" ? (
          <>
            {!empty && (
              <div className="flex flex-wrap justify-end gap-1">
                {(value as unknown[]).map((item) => (
                  <span
                    key={String(item)}
                    className="inline-flex items-center gap-1 rounded-full border border-sheen/[0.1] bg-sheen/[0.05] py-0.5 pl-2 pr-1 text-[11px] font-medium"
                  >
                    {String(item)}
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => mutate("remove", String(item), { keepOpen: true })}
                      title={t("profile_view.field_remove_item")}
                      aria-label={`${t("profile_view.field_remove_item")}: ${String(item)}`}
                      className="rounded-full p-0.5 text-muted-foreground outline-none transition-colors hover:bg-destructive/15 hover:text-destructive focus-visible:ring-1 focus-visible:ring-primary/50 disabled:opacity-40"
                    >
                      <X className="h-2.5 w-2.5" />
                    </button>
                  </span>
                ))}
              </div>
            )}
            <div className="flex items-center gap-1">
              <input
                ref={inputRef}
                value={draft}
                disabled={busy}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") addItem();
                  if (e.key === "Escape") cancel();
                }}
                placeholder={t("profile_view.field_add_placeholder")}
                className="w-28 min-w-0 rounded border border-primary/40 bg-background/60 px-2 py-1 text-sm outline-none focus:border-primary"
              />
              <IconBtn
                icon={Plus}
                onClick={addItem}
                title={t("profile_view.field_add")}
                tone="primary"
                disabled={busy}
              />
              <IconBtn icon={Check} onClick={cancel} title={t("profile_view.raw_save")} disabled={busy} />
              {!empty && (
                <IconBtn
                  icon={Trash2}
                  onClick={() => mutate("clear")}
                  title={t("profile_view.field_clear")}
                  tone="danger"
                  disabled={busy}
                />
              )}
            </div>
          </>
        ) : (
          <div className="flex w-full items-center justify-end gap-1">
            <input
              ref={inputRef}
              value={draft}
              disabled={busy}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") saveScalar();
                if (e.key === "Escape") cancel();
              }}
              placeholder={t("profile_view.field_value_placeholder")}
              className="min-w-0 flex-1 rounded border border-primary/40 bg-background/60 px-2 py-1 text-right text-sm outline-none focus:border-primary"
            />
            <IconBtn
              icon={Check}
              onClick={saveScalar}
              title={t("profile_view.raw_save")}
              tone="primary"
              disabled={busy}
            />
            <IconBtn icon={X} onClick={cancel} title={t("profile_view.raw_cancel")} disabled={busy} />
            {!empty && (
              <IconBtn
                icon={Trash2}
                onClick={() => mutate("clear")}
                title={t("profile_view.field_clear")}
                tone="danger"
                disabled={busy}
              />
            )}
          </div>
        )}
      </dd>
    </div>
  );
}

// ----------------------------------------------------------------------
// The Margin — what the ledger still wants: a question, the queue, the people
// ----------------------------------------------------------------------

function MarginRail({
  data,
  meta,
}: {
  data: ProfileResponse;
  meta: Record<string, unknown>;
}) {
  return (
    <aside className="flex min-w-0 flex-col gap-6 border-t border-border px-5 py-6 lg:min-h-0 lg:overflow-y-auto lg:border-l lg:border-t-0 lg:py-8 scrollbar-jarvis">
      <div className="profile-rise" style={{ animationDelay: "80ms" }}>
        <AskCard meta={meta} />
      </div>
      <div className="profile-rise" style={{ animationDelay: "150ms" }}>
        <ReviewsSection reviewsCount={data.reviews_count} />
      </div>
      <div className="profile-rise" style={{ animationDelay: "220ms" }}>
        <PeopleSection people={data.people} />
      </div>
    </aside>
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
    <div className="rounded-2xl border border-sheen/[0.08] bg-sheen/[0.03] p-4">
      <div className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-[0.12em] text-muted-foreground">
        <Sparkles className="h-3.5 w-3.5" />
        {t("profile_view.ask_title")}
        <span className="ml-auto tabular-nums text-muted-foreground/60">
          {(idx % open.length) + 1}/{open.length}
        </span>
      </div>

      <p className="mt-2.5 font-display text-base font-semibold leading-snug tracking-tight">
        {t(`profile_view.questions.${q.field}`)}
      </p>

      <div className="mt-3 flex items-start gap-2 rounded-xl border border-sheen/[0.07] bg-background/40 px-3 py-2 text-[11px] leading-relaxed text-muted-foreground">
        <Mic className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        <span className="min-w-0">
          {t("profile_view.ask_say_prefix")}:{" "}
          <span className="font-medium text-foreground/90">
            “{t(`profile_view.says.${q.field}`)}”
          </span>
        </span>
      </div>

      <div className="mt-3 flex items-center justify-between gap-2">
        <span className="text-[11px] text-muted-foreground/70">
          {t(`profile_view.clusters.${q.cluster}.label`)}
        </span>
        <button
          type="button"
          data-testid="ask-next"
          onClick={() => setIdx((i) => i + 1)}
          className="inline-flex items-center gap-1 rounded-full border border-sheen/[0.08] bg-sheen/[0.03] px-3 py-1 text-[11px] font-medium transition-colors hover:border-primary/40 hover:bg-sheen/[0.07]"
        >
          {t("profile_view.ask_next")}
          <ChevronRight className="h-3 w-3" />
        </button>
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------
// Reviews — observations waiting for the user's OK
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
      fetchJson<{ ok: boolean; applied: number }>(`/api/profile/reviews/${idx}/accept`, {
        method: "POST",
      }),
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
      fetchJson<{ ok: boolean }>(`/api/profile/reviews/${idx}/reject`, { method: "POST" }),
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
      <RailHeading
        title={t("profile_view.section_reviews")}
        count={reviewsCount > 0 ? `${reviewsCount} ${t("profile_view.review_open")}` : ""}
        right={
          <button
            type="button"
            onClick={() => refetch()}
            disabled={isRefetching}
            title={t("profile_view.reload_tooltip")}
            aria-label={t("profile_view.reload_tooltip")}
            className="rounded p-1 text-muted-foreground transition-colors hover:text-foreground disabled:opacity-40"
          >
            <RefreshCw className={cn("h-3 w-3", isRefetching && "animate-spin")} />
          </button>
        }
      />

      {isLoading && (
        <div className="mt-2.5 flex items-center gap-2 text-xs text-muted-foreground">
          <RefreshCw className="h-3 w-3 animate-spin" /> {t("profile_view.raw_loading")}
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
            className="mt-2.5 rounded-xl border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive"
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
        <ul className="mt-2.5 space-y-2">
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

/** Calm in-rail empty state shared by People and Reviews. */
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
    <div data-testid={testId} className="mt-2.5 flex items-start gap-3 py-1">
      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-sheen/[0.07] bg-sheen/[0.03]">
        <Icon className="h-[15px] w-[15px] text-muted-foreground" />
      </div>
      <div className="min-w-0 flex-1">
        <div className="text-xs font-medium">{title}</div>
        <p className="mt-0.5 text-[11px] leading-relaxed text-muted-foreground">{body}</p>
      </div>
    </div>
  );
}

/** One review candidate, sized for the margin rail: the quote leads, the
 *  cluster→field path and the verdict buttons sit under it. */
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

  return (
    <li className="rounded-xl border border-sheen/[0.06] bg-sheen/[0.02] p-3 transition-colors hover:border-primary/25">
      {candidate.evidence && (
        <blockquote className="text-xs italic leading-snug text-foreground/90">
          “{candidate.evidence}”
        </blockquote>
      )}

      <div className="mt-2 flex flex-wrap items-center gap-1 text-[11px] text-muted-foreground">
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
        <span className="ml-auto font-semibold tabular-nums text-foreground/80">
          {(candidate.confidence * 100).toFixed(0)}%
        </span>
      </div>

      <div className="mt-1.5 text-xs">
        <span className="text-muted-foreground">{t("profile_view.review_value")}: </span>
        <span className="font-medium text-foreground [overflow-wrap:anywhere]">
          {renderValue(t, candidate.value) || "—"}
        </span>
      </div>

      {candidate.reason && (
        <p className="mt-1 text-[10px] leading-relaxed text-muted-foreground/70">
          {t("profile_view.review_reason")}: {candidate.reason}
        </p>
      )}

      <div className="mt-2.5 flex gap-1.5">
        <Button
          size="sm"
          variant="default"
          className="h-7 flex-1 text-[11px]"
          disabled={pending}
          onClick={onAccept}
          title={t("profile_view.accept_tooltip")}
        >
          <Check className="mr-1 h-3 w-3" />
          {t("profile_view.review_confirm")}
        </Button>
        <Button
          size="sm"
          variant="outline"
          className="h-7 flex-1 text-[11px]"
          disabled={pending}
          onClick={onReject}
          title={t("profile_view.reject_tooltip")}
        >
          <X className="mr-1 h-3 w-3" />
          {t("profile_view.review_strike")}
        </Button>
      </div>
    </li>
  );
}

// ----------------------------------------------------------------------
// People — quiet rows that expand in place
// ----------------------------------------------------------------------
//
// A side-by-side list + detail card cannot fit a 320px rail, so the detail
// opens inside the row instead. One click selects and expands — the row-click
// doctrine the rest of the app follows.

function PeopleSection({ people }: { people: PersonSummary[] }) {
  const t = useT();
  const [openSlug, setOpenSlug] = useState<string | null>(null);

  return (
    <div>
      <RailHeading
        title={t("profile_view.section_people")}
        count={people.length > 0 ? people.length : ""}
      />

      {people.length === 0 ? (
        <EmptyHint
          icon={UsersIcon}
          title={t("profile_view.people_empty_title")}
          body={t("profile_view.people_empty_body")}
        />
      ) : (
        <ul className="mt-1.5">
          {people.map((p) => (
            <PersonRow
              key={p.slug}
              person={p}
              open={p.slug === openSlug}
              onToggle={() => setOpenSlug(p.slug === openSlug ? null : p.slug)}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

function PersonRow({
  person,
  open,
  onToggle,
}: {
  person: PersonSummary;
  open: boolean;
  onToggle: () => void;
}) {
  const t = useT();

  return (
    <li className="border-b border-sheen/[0.05] last:border-b-0">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="flex w-full items-center gap-2.5 py-2 text-left transition-colors"
      >
        <span
          className={cn(
            "flex h-7 w-7 shrink-0 items-center justify-center rounded-full border text-[10px] font-semibold transition-colors",
            open
              ? "border-primary/40 bg-primary/10 text-primary"
              : "border-sheen/[0.08] bg-sheen/[0.05] text-foreground/80",
          )}
        >
          {initials(person.name)}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-xs font-medium">{person.name}</span>
          <span className="block truncate text-[10px] text-muted-foreground">
            {person.relationship}
          </span>
        </span>
        <ChevronDown
          className={cn(
            "h-3.5 w-3.5 shrink-0 text-muted-foreground/50 transition-transform",
            open && "rotate-180 text-primary",
          )}
        />
      </button>

      {open && (
        <div className="pb-3 pl-[2.375rem] pr-1">
          <dl className="space-y-1.5 text-[11px]">
            <div className="flex items-baseline justify-between gap-3">
              <dt className="text-muted-foreground">{t("profile_view.person_relationship")}</dt>
              <dd className="font-medium text-foreground">{person.relationship}</dd>
            </div>
            <div className="flex items-baseline justify-between gap-3">
              <dt className="text-muted-foreground">{t("profile_view.person_aliases")}</dt>
              <dd className="text-right [overflow-wrap:anywhere]">
                {person.aliases.length === 0 ? (
                  <span className="italic text-muted-foreground/60">
                    {t("profile_view.person_no_aliases")}
                  </span>
                ) : (
                  <span className="text-foreground/90">{person.aliases.join(" · ")}</span>
                )}
              </dd>
            </div>
          </dl>
          <p className="mt-2 flex items-start gap-1.5 rounded-lg bg-sheen/[0.03] p-2 text-[10px] leading-relaxed text-muted-foreground">
            <Inbox className="mt-0.5 h-3 w-3 shrink-0" />
            {t("profile_view.person_file_hint").replace("{0}", person.slug)}
          </p>
        </div>
      )}
    </li>
  );
}

// ----------------------------------------------------------------------
// Source drawer — live USER.md, over the view instead of below it
// ----------------------------------------------------------------------
//
// Data flow: GET /api/profile/raw → React-Query cache. Live sync via WS:
// every Curator merge publishes ProfileUpdated on the bus, the WS server
// streams it to the UI, and the subscriber below invalidates both profile
// queries — the file content is current seconds after a write. The pulse
// badge gives visual feedback when an update lands.
//
// It is a drawer and not a rail because a Markdown file is the one piece of
// this view that genuinely needs a full column of height to be readable.

function SourceDrawer({ onClose }: { onClose: () => void }) {
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

  const { data, isLoading, error, refetch, isRefetching } = useQuery<RawProfileResponse, Error>({
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
      // Knowledge rows always refresh…
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

  // Escape closes — but never out from under an unsaved draft.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !editing) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [editing, onClose]);

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
    <div className="absolute inset-0 z-30 flex flex-col bg-background/80 backdrop-blur-sm">
      {/* The scrim closes the drawer; the sheet below stops the click. */}
      <button
        type="button"
        aria-label={t("profile_view.close_tooltip")}
        onClick={() => !editing && onClose()}
        className="absolute inset-0 cursor-default outline-none"
        tabIndex={-1}
      />

      <div className="profile-rise relative m-4 flex min-h-0 flex-1 flex-col overflow-hidden rounded-2xl border border-sheen/[0.09] bg-card shadow-[0_24px_60px_-24px_rgba(0,0,0,0.8)] lg:m-6">
        <header className="flex flex-wrap items-center gap-2.5 border-b border-sheen/[0.07] px-4 py-3">
          <FileText className="h-4 w-4 shrink-0 text-primary" />
          <div className="min-w-0">
            <div className="font-display text-sm font-semibold">
              {t("profile_view.section_source")}
            </div>
            {data && (
              <div className="truncate font-mono text-[10px] text-muted-foreground">{data.path}</div>
            )}
          </div>

          <div className="ml-auto flex items-center gap-2 text-[11px] text-muted-foreground">
            {editing ? (
              <>
                <span className="hidden items-center gap-1.5 sm:flex">
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
                  <span className="inline-flex items-center gap-1.5 rounded-full bg-muted-foreground/15 px-2 py-0.5 text-[10px] font-semibold text-muted-foreground">
                    <span className="relative flex h-1.5 w-1.5">
                      <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-muted-foreground opacity-75" />
                      <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-muted-foreground" />
                    </span>
                    {t("profile_view.just_updated")}
                  </span>
                )}
                {lastUpdate && (
                  <span className="hidden items-center gap-1 md:inline-flex">
                    <Clock className="h-3 w-3" />
                    {lastUpdate.toLocaleString()}
                  </span>
                )}
                {data && <span className="tabular-nums">{(data.size_bytes / 1024).toFixed(1)} KB</span>}
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
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={onClose}
                  title={t("profile_view.close_tooltip")}
                  aria-label={t("profile_view.close_tooltip")}
                >
                  <X className="h-3.5 w-3.5" />
                </Button>
              </>
            )}
          </div>
        </header>

        <div className="min-h-0 flex-1 overflow-hidden">
          {isLoading && (
            <div className="flex items-center gap-2 p-5 text-sm text-muted-foreground">
              <RefreshCw className="h-3.5 w-3.5 animate-spin" /> {t("profile_view.raw_loading")}
            </div>
          )}

          {error && (
            <div className="m-4 rounded-xl border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">
              {error.message}
            </div>
          )}

          {data &&
            (editing ? (
              <textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                spellCheck={false}
                autoFocus
                aria-label="USER.md"
                className="block h-full w-full resize-none bg-transparent p-5 font-mono text-[11px] leading-relaxed text-foreground/90 outline-none scrollbar-jarvis"
              />
            ) : (
              <pre className="h-full overflow-auto whitespace-pre-wrap break-words p-5 font-mono text-[11px] leading-relaxed text-foreground/90 scrollbar-jarvis">
                {data.content || t("profile_view.raw_empty")}
              </pre>
            ))}
        </div>
      </div>
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
        <div className="max-w-md rounded-2xl border border-sheen/[0.08] bg-sheen/[0.03] p-8 text-center">
          <div className="mx-auto mb-5 flex h-14 w-14 items-center justify-center rounded-full border border-sheen/[0.08] bg-sheen/[0.04]">
            <UserCircle2 className="h-6 w-6 text-primary" />
          </div>
          <h3 className="font-display text-lg font-semibold tracking-tight">
            {t("profile_view.hero_name_placeholder")}
          </h3>
          <p className="mt-2 text-sm text-muted-foreground">{error.message}</p>
          <p className="mt-4 text-xs text-muted-foreground/70">{t("profile_view.no_user_hint")}</p>
          <Button className="mt-6" size="sm" variant="outline" onClick={onRetry}>
            <RefreshCw className="mr-2 h-3.5 w-3.5" /> {t("apikeys_view.retry")}
          </Button>
        </div>
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
