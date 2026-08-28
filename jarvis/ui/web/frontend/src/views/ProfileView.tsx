/**
 * ProfileView — the profile as ONE view, never a scrolling page.
 *
 * Layout doctrine: the whole section is a single viewport. Nothing here is
 * reached by scrolling the page; the view is a thin identity strip over three
 * standing rails that each scroll inside themselves only when their own
 * content overflows.
 *
 *   ┌───────────────────────────────────────────────────────────────────┐
 *   │ ViewHeader                                                        │
 *   ├───────────────────────────────────────────────────────────────────┤
 *   │ [portrait]  who you are · stage        things learned · people    │
 *   ├────────────────────┬──────────────────────┬───────────────────────┤
 *   │  The Ledger        │  The Source          │  The Margin           │
 *   │  every cluster,    │  USER.md rendered as │  the open question,   │
 *   │  every field,      │  the document it is, │  the review queue,    │
 *   │  editable in place │  editable in place   │  the people           │
 *   └────────────────────┴──────────────────────┴───────────────────────┘
 *
 * The two halves of the middle answer different questions and are worth
 * seeing at the same time: the ledger is USER.md's front matter as structured
 * fields, the source rail is the prose the curator writes underneath it. That
 * pairing is why the file is rendered in place rather than hidden behind a
 * button — reading the profile and reading the file are the same act.
 *
 * The single non-negotiable of that shape is an unbroken `min-h-0` chain from
 * the root to each rail: a flex/grid child defaults to `min-height: auto` and
 * refuses to shrink below its content, which is what silently turns a
 * "one viewport" layout back into a scrolling page.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
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
import { PROSE_BASE, splitFrontMatter } from "@/components/outputs/MarkdownProse";
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
        <>
          <IdentityStrip data={data} meta={meta} />

          {/* One viewport. On lg+ the three rails stand side by side and
              scroll individually; below that they stack and the container
              takes over the scrolling — the honest degradation for a window
              too narrow to hold three rails at a readable width. */}
          <div
            className={cn(
              "grid min-h-0 flex-1 grid-cols-1 overflow-y-auto scrollbar-jarvis",
              "lg:grid-cols-[minmax(320px,1fr)_minmax(0,1.15fr)_minmax(280px,330px)]",
              "lg:overflow-hidden",
            )}
          >
            <LedgerRail meta={meta} />
            <SourceRail />
            <MarginRail data={data} meta={meta} />
          </div>
        </>
      )}
    </div>
  );
}

// ----------------------------------------------------------------------
// Identity strip — the portrait and who you are, on one line
// ----------------------------------------------------------------------
//
// Everything a person needs to recognise the page as theirs, in the height of
// a toolbar: the portrait (which is also the upload control), how Jarvis
// addresses them, the acquaintance stage, and the two counts. It replaced a
// full-height left rail built around a generative mark — that rail spent a
// third of the viewport on ornament, and ornament is not what this view is
// for. The facts stayed; the furniture went.

function IdentityStrip({
  data,
  meta,
}: {
  data: ProfileResponse;
  meta: Record<string, unknown>;
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
    <div className="profile-rise flex items-center gap-3.5 border-b border-border px-6 py-3">
      <AvatarButton name={name} hasAvatar={!!data.has_avatar} />

      <div className="flex min-w-0 flex-wrap items-baseline gap-x-2.5 gap-y-0.5">
        <h1 className="font-display text-[15px] font-semibold tracking-tight">{headline}</h1>
        <span className="text-[11px] font-medium uppercase tracking-[0.12em] text-muted-foreground">
          {t(`profile_view.stages.${stage.key}`)}
        </span>
      </div>

      <div className="ml-auto flex shrink-0 items-center text-[11px] text-muted-foreground">
        <span className="font-medium tabular-nums text-foreground/90">{ratio}</span>
        <Dot />
        {peopleLine}
        {data.reviews_count > 0 && (
          <>
            <Dot />
            <span className="font-medium text-foreground/90">
              {data.reviews_count} {t("profile_view.reviews_count")}
            </span>
          </>
        )}
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------
// AvatarButton — the portrait, and the whole upload control
// ----------------------------------------------------------------------
//
// The avatar bytes live under user_data_dir()/data and are served by
// GET /api/profile/avatar (see profile_routes.py). A hidden <input
// type="file"> is .click()'d to open the OS picker; a cache-bust query
// param forces the <img> to reload after a replace/delete.
//
// At strip height there is no room for labelled buttons, so the portrait is
// the control: click to pick a file, and a small remove badge appears on
// hover or keyboard focus once there is a picture to remove.

function AvatarButton({ name, hasAvatar }: { name: string | null; hasAvatar: boolean }) {
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
    <div className="group/avatar relative shrink-0">
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
        className="relative flex h-9 w-9 items-center justify-center overflow-hidden rounded-full border border-sheen/[0.09] bg-sheen/[0.04] outline-none transition-colors hover:border-primary/50 focus-visible:border-primary"
      >
        {hasAvatar ? (
          <img
            src={`/api/profile/avatar?t=${bust}`}
            alt={t("profile_view.avatar_alt")}
            className="h-full w-full object-cover"
            draggable={false}
          />
        ) : name ? (
          <span className="font-display text-xs font-semibold tracking-tight text-foreground/80">
            {initials(name)}
          </span>
        ) : (
          <UserCircle2 className="h-5 w-5 text-muted-foreground/50" />
        )}

        <span className="pointer-events-none absolute inset-0 flex items-center justify-center rounded-full bg-background/70 opacity-0 transition-opacity duration-200 group-hover/avatar:opacity-100 group-focus-visible/avatar:opacity-100">
          <Camera className="h-3.5 w-3.5 text-primary" />
        </span>

        {busy && (
          <span className="absolute inset-0 flex items-center justify-center rounded-full bg-background/70">
            <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />
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
          className="absolute -bottom-1 -right-1 rounded-full border border-sheen/[0.1] bg-background p-1 text-muted-foreground opacity-0 outline-none transition-all hover:border-destructive/40 hover:text-destructive focus-visible:opacity-100 group-hover/avatar:opacity-100"
        >
          <Trash2 className="h-2.5 w-2.5" />
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

function LedgerRail({ meta }: { meta: Record<string, unknown> }) {
  const t = useT();

  return (
    <section className="flex min-w-0 flex-col px-6 py-6 lg:min-h-0 lg:overflow-y-auto lg:py-7 scrollbar-jarvis">
      <div className="profile-rise" style={{ animationDelay: "60ms" }}>
        <h2 className="font-display text-base font-semibold tracking-tight">
          {t("profile_view.section_knowledge")}
        </h2>
        <p className="mt-0.5 text-xs text-muted-foreground">
          {t("profile_view.section_knowledge_sub")}
        </p>
      </div>

      {/* One continuous run of clusters. Balanced CSS columns halved the
          content's height and left the lower viewport empty; a single column
          fills the rail the way a ledger page fills a sheet, and the leader
          dots keep even a wide row readable. */}
      <div className="mt-4">
        {CLUSTER_ORDER.map((cid, i) => (
          <div
            key={cid}
            className="profile-rise mb-6 last:mb-0"
            style={{ animationDelay: `${100 + i * 45}ms` }}
          >
            <ClusterGroup cid={cid} meta={meta} />
          </div>
        ))}
      </div>
    </section>
  );
}

function ClusterGroup({ cid, meta }: { cid: ClusterId; meta: Record<string, unknown> }) {
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
          <EditableFieldRow key={key} cid={cid} fieldKey={key} value={data[key]} />
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
}: {
  cid: ClusterId;
  fieldKey: string;
  value: unknown;
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
      <div className="group flex items-baseline gap-2 py-[0.3rem]">
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
// The Source — USER.md, rendered as the document it is
// ----------------------------------------------------------------------
//
// Data flow: GET /api/profile/raw → React-Query cache. Live sync via WS:
// every Curator merge publishes ProfileUpdated on the bus, the WS server
// streams it to the UI, and the subscriber below invalidates both profile
// queries — the file is current seconds after a write. The pulse badge gives
// visual feedback when an update lands.
//
// The file is rendered, not dumped as monospace: the curator writes real
// prose under the front matter ("Observations over time", "Active projects"),
// and reading it as a document is the point of having it on the page. The
// front matter itself is split off — the ledger to the left already IS that
// block, drawn as fields. Editing switches the same rail to the raw text, so
// nothing is hidden from the person who wants to fix a line by hand.

function SourceRail() {
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
      // The ledger always refreshes…
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

  // Escape leaves edit mode, the way it cancels every other inline editor
  // in this view. The draft is dropped, which is why the button says Cancel.
  useEffect(() => {
    if (!editing) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setEditing(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [editing]);

  const isPulsing = Date.now() < pulseUntil;
  const lastUpdate = useMemo(() => {
    if (!data?.mtime_ms) return null;
    return new Date(data.mtime_ms);
  }, [data?.mtime_ms]);

  // The front matter is the ledger's own content; showing it twice would just
  // be the same facts in a worse format.
  //
  // The curator's anchors (`<!-- curator:observations:start -->`) mark where
  // it splices its own writes. They are machinery, not text — and because
  // react-markdown escapes raw HTML instead of rendering it, leaving them in
  // prints them verbatim on the page. Strip them, then close the run of blank
  // lines they leave behind.
  const body = useMemo(() => {
    if (!data) return "";
    return splitFrontMatter(data.content)
      .body.replace(/<!--[\s\S]*?-->/g, "")
      .replace(/[ \t]+$/gm, "")
      .replace(/\n{3,}/g, "\n\n")
      .trim();
  }, [data]);

  return (
    <section className="flex min-w-0 flex-col border-t border-border lg:min-h-0 lg:border-l lg:border-t-0">
      <div className="flex flex-wrap items-center gap-2 border-b border-sheen/[0.06] px-5 py-2.5">
        <FileText className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        <h2 className="font-display text-[11px] font-semibold uppercase tracking-[0.13em] text-muted-foreground">
          {t("profile_view.section_source")}
        </h2>
        {data && (
          <span className="min-w-0 truncate font-mono text-[10px] text-muted-foreground/60">
            {data.path}
          </span>
        )}

        <div className="ml-auto flex items-center gap-1.5 text-[10px] text-muted-foreground">
          {editing ? (
            <>
              <span className="hidden items-center gap-1.5 xl:flex">
                <Lock className="h-3 w-3 text-primary/70" />
                {t("profile_view.raw_editing_hint")}
              </span>
              <Button
                size="sm"
                variant="ghost"
                className="h-6 px-2 text-[11px]"
                onClick={() => setEditing(false)}
                disabled={save.isPending}
              >
                {t("profile_view.raw_cancel")}
              </Button>
              <Button
                size="sm"
                variant="default"
                className="h-6 px-2 text-[11px]"
                onClick={() => save.mutate(draft)}
                disabled={save.isPending}
              >
                <Save className={cn("mr-1 h-3 w-3", save.isPending && "animate-pulse")} />
                {save.isPending ? t("profile_view.raw_saving") : t("profile_view.raw_save")}
              </Button>
            </>
          ) : (
            <>
              {isPulsing && (
                <span className="inline-flex items-center gap-1.5 rounded-full bg-muted-foreground/15 px-2 py-0.5 font-semibold text-muted-foreground">
                  <span className="relative flex h-1.5 w-1.5">
                    <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-muted-foreground opacity-75" />
                    <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-muted-foreground" />
                  </span>
                  {t("profile_view.just_updated")}
                </span>
              )}
              {lastUpdate && (
                <span className="hidden items-center gap-1 xl:inline-flex">
                  <Clock className="h-3 w-3" />
                  {lastUpdate.toLocaleDateString()}
                </span>
              )}
              {data && <span className="tabular-nums">{(data.size_bytes / 1024).toFixed(1)} KB</span>}
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
              {data && (
                <Button
                  size="sm"
                  variant="outline"
                  className="h-6 px-2 text-[11px]"
                  onClick={startEditing}
                >
                  <Pencil className="mr-1 h-3 w-3" />
                  {t("profile_view.raw_edit")}
                </Button>
              )}
            </>
          )}
        </div>
      </div>

      <div className="min-h-0 flex-1 lg:overflow-hidden">
        {isLoading && (
          <div className="flex items-center gap-2 px-5 py-4 text-xs text-muted-foreground">
            <RefreshCw className="h-3 w-3 animate-spin" /> {t("profile_view.raw_loading")}
          </div>
        )}

        {error && (
          <div className="m-4 rounded-xl border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive">
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
              className="block h-full min-h-[24rem] w-full resize-none bg-transparent px-5 py-4 font-mono text-[11px] leading-relaxed text-foreground/90 outline-none scrollbar-jarvis"
            />
          ) : (
            <div className="h-full overflow-y-auto px-5 py-4 scrollbar-jarvis">
              {body ? (
                <article
                  data-testid="profile-source-markdown"
                  className={cn(
                    PROSE_BASE,
                    "max-w-none text-[13px]",
                    "prose-headings:font-display prose-h1:text-lg prose-h2:mt-6 prose-h2:text-sm",
                    "prose-h2:uppercase prose-h2:tracking-[0.1em] prose-h2:text-muted-foreground",
                    "prose-h3:text-[13px] prose-p:leading-relaxed",
                  )}
                >
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{body}</ReactMarkdown>
                </article>
              ) : (
                <p className="text-xs italic text-muted-foreground/60">
                  {t("profile_view.raw_empty")}
                </p>
              )}
            </div>
          ))}
      </div>
    </section>
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
