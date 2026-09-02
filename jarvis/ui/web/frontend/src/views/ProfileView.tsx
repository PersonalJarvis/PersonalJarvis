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
import { IdentityAvatar } from "@/components/identity/IdentityAvatar";
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
  return <span className="px-1.5 text-muted-foreground">·</span>;
}

/**
 * A group's heading. It used to be an 11px all-caps label over a hairline —
 * the exact construction that makes an interface read as an admin panel, and
 * twelve of them shared one viewport here. It is now a plain title, and the
 * 32px between groups does the separating a rule used to do badly.
 */
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
    <div className="flex items-baseline gap-2">
      <h3 className="text-sm font-medium uppercase tracking-wide text-foreground-faint">{title}</h3>
      {count !== undefined && count !== "" && (
        <span className="text-sm tabular-nums text-foreground-faint">{count}</span>
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
    <div className="relative flex h-full flex-col overflow-hidden bg-background">
      <ViewHeader
        icon={<UserCircle2 className="h-4 w-4" />}
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
              too narrow to hold three rails at a readable width.

              Each rail carries its own ground rather than a hairline: the two
              standing rails take --sidebar, the document in the middle keeps
              --background. That is the reference layout (a rail is lighter
              than the stage it flanks) and it is the only separation device
              that survives at these widths — a full-height column is far too
              wide to earn --card. */}
          <div
            className={cn(
              "grid min-h-0 flex-1 grid-cols-1 overflow-y-auto scrollbar-jarvis",
              "lg:grid-cols-[minmax(360px,1fr)_minmax(0,1.25fr)]",
              "lg:overflow-hidden",
            )}
          >
            <LedgerRail meta={meta} />
            {/* Two columns (v4): the source, with the "would love to know"
                prompt and the review queue stacked under it as cards. */}
            <div className="flex min-w-0 flex-col lg:min-h-0 lg:overflow-y-auto scrollbar-jarvis">
              <SourceRail />
              <MarginRail data={data} meta={meta} />
            </div>
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
    <div className="profile-rise mx-8 mb-5 flex items-center gap-4 rounded-lg border border-border bg-card p-5">
      <AvatarButton name={name} hasAvatar={!!data.has_avatar} />

      <div className="flex min-w-0 flex-col gap-0.5">
        <h1 className="truncate text-xl font-semibold text-foreground-strong">{headline}</h1>
        <span className="text-base text-muted-foreground">
          {t(`profile_view.stages.${stage.key}`)}
        </span>
      </div>

      <div className="ml-auto flex shrink-0 items-center text-sm text-muted-foreground">
        <span
          className="mr-3 hidden h-1.5 w-40 overflow-hidden rounded-full bg-secondary sm:block"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={TOTAL_FIELDS}
          aria-valuenow={filled}
          data-testid="profile-progress"
        >
          <span
            className="block h-full rounded-full bg-accent"
            style={{ width: `${Math.round((filled / Math.max(TOTAL_FIELDS, 1)) * 100)}%` }}
          />
        </span>
        <span className="tabular-nums text-foreground">{ratio}</span>
        <Dot />
        {peopleLine}
        {data.reviews_count > 0 && (
          <>
            <Dot />
            <span className="text-foreground">
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
        className="relative flex h-12 w-12 items-center justify-center overflow-hidden rounded-full bg-secondary outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring"
      >
        {/* The person's own coloured mark, not a grey disc: identity is one of
            the three jobs colour has, and this is the app's own owner. */}
        {name ? (
          <IdentityAvatar
            name={name}
            src={hasAvatar ? `/api/profile/avatar?t=${bust}` : null}
            alt={t("profile_view.avatar_alt")}
          />
        ) : hasAvatar ? (
          <img
            src={`/api/profile/avatar?t=${bust}`}
            alt={t("profile_view.avatar_alt")}
            className="h-full w-full object-cover"
            draggable={false}
          />
        ) : (
          <UserCircle2 className="h-6 w-6 text-muted-foreground" />
        )}

        <span className="pointer-events-none absolute inset-0 flex items-center justify-center rounded-full bg-scrim/70 opacity-0 transition-opacity duration-200 group-hover/avatar:opacity-100 group-focus-visible/avatar:opacity-100">
          <Camera className="h-3.5 w-3.5 text-foreground-strong" />
        </span>

        {busy && (
          <span className="absolute inset-0 flex items-center justify-center rounded-full bg-scrim/70">
            <Loader2 className="h-3.5 w-3.5 animate-spin text-foreground-strong" />
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
          className="absolute -bottom-1 -right-1 rounded-full bg-secondary p-1 text-muted-foreground opacity-0 outline-none transition-all hover:text-destructive focus-visible:opacity-100 group-hover/avatar:opacity-100"
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
    <section className="flex min-w-0 flex-col border-r border-border px-8 pb-6 pt-1 lg:min-h-0 lg:overflow-y-auto scrollbar-jarvis">
      <div className="profile-rise" style={{ animationDelay: "60ms" }}>
        <h2 className="text-lg font-semibold text-foreground-strong">
          {t("profile_view.section_knowledge")}
        </h2>
        <p className="mt-1 text-base text-muted-foreground">
          {t("profile_view.section_knowledge_sub")}
        </p>
      </div>

      {/* One continuous run of clusters, separated by the 32px group step.
          The per-cluster blurb and the "3/4 filled" counter that used to sit
          under every heading were both removed: the blurb restated the field
          names below it, and nobody acts on a per-cluster count — the strip
          at the top already says how complete the profile is. */}
      <div className="mt-group space-y-group">
        {CLUSTER_ORDER.map((cid, i) => (
          <div
            key={cid}
            className="profile-rise"
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

  return (
    <div>
      <RailHeading title={t(`profile_view.clusters.${cid}.label`)} />
      {/* Every row carries its own quiet pencil — learned fields can be
          overwritten or cleared, blank ones filled in, all edited in place. */}
      <dl className="mt-stack">
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
  // --primary is a FILL, never an ink: the affirmative action is a filled
  // square, the other two are ghosts that answer hover by stepping up.
  const toneCls =
    tone === "primary"
      ? "bg-primary text-primary-foreground"
      : tone === "danger"
        ? "text-muted-foreground hover:bg-secondary hover:text-destructive"
        : "text-muted-foreground hover:bg-secondary hover:text-foreground";
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      aria-label={title}
      disabled={disabled}
      className={cn(
        "shrink-0 rounded-md p-1 outline-none transition-colors focus-visible:ring-2 focus-visible:ring-border-strong disabled:opacity-40",
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
  //
  // No leader dots, and no fifteen repetitions of "not known yet". The dotted
  // rule was the construction that made this column read as a printed ledger
  // form, and a sentence repeated once per blank field is noise, not an empty
  // state: a blank row now says so once, with a dash, and the whole row is the
  // affordance that fills it in.
  if (!editing) {
    return (
      <div className="group -mx-2 flex min-h-10 items-center justify-between gap-3 rounded-md px-2 py-1 transition-colors hover:bg-secondary">
        <dt className="shrink-0 text-base text-muted-foreground">{label}</dt>
        <dd className="flex min-w-0 max-w-[68%] items-center justify-end gap-2">
          {kind === "list" && !empty ? (
            <div className="flex flex-wrap justify-end gap-1">
              {(value as unknown[]).map((item) => (
                <span
                  key={String(item)}
                  className="rounded-md border border-border bg-secondary px-2 text-sm leading-6 text-foreground"
                >
                  {String(item)}
                </span>
              ))}
            </div>
          ) : empty ? (
            <span
              aria-label={t("profile_view.field_unknown")}
              title={t("profile_view.field_unknown")}
              className="text-base text-foreground-faint"
            >
              —
            </span>
          ) : (
            <span className="text-base text-foreground [overflow-wrap:anywhere]">
              {renderValue(t, value)}
            </span>
          )}
          {/* Quiet pencil — appears on row hover / keyboard focus only. */}
          <button
            type="button"
            onClick={startEdit}
            title={t("profile_view.field_edit")}
            aria-label={`${t("profile_view.field_edit")}: ${label}`}
            className="shrink-0 rounded-md p-1 text-muted-foreground opacity-0 outline-none transition-opacity hover:text-foreground focus-visible:opacity-100 focus-visible:ring-2 focus-visible:ring-border-strong group-hover:opacity-100"
          >
            {empty ? <Plus className="h-3.5 w-3.5" /> : <Pencil className="h-3.5 w-3.5" />}
          </button>
        </dd>
      </div>
    );
  }

  // ------------------------------------------------------------------- editing
  return (
    <div className="group flex items-baseline justify-between gap-3 rounded-md bg-secondary px-2 py-1.5 -mx-2">
      <dt className="shrink-0 text-meta text-foreground-strong">{label}</dt>
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
                  "rounded-full px-2.5 py-0.5 text-meta font-medium transition-colors disabled:opacity-40",
                  value === b
                    ? "bg-primary text-primary-foreground"
                    : "bg-popover text-muted-foreground hover:text-foreground",
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
                    className="inline-flex items-center gap-1 rounded-full bg-popover py-0.5 pl-2 pr-1 text-meta text-foreground"
                  >
                    {String(item)}
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => mutate("remove", String(item), { keepOpen: true })}
                      title={t("profile_view.field_remove_item")}
                      aria-label={`${t("profile_view.field_remove_item")}: ${String(item)}`}
                      className="rounded-full p-0.5 text-muted-foreground outline-none transition-colors hover:text-destructive focus-visible:ring-2 focus-visible:ring-border-strong disabled:opacity-40"
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
                className="w-28 min-w-0 rounded-md bg-popover px-2 py-1 text-body text-foreground outline-none placeholder:text-faint-foreground focus:ring-2 focus:ring-border-strong"
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
              className="min-w-0 flex-1 rounded-md bg-popover px-2 py-1 text-right text-body text-foreground outline-none placeholder:text-faint-foreground focus:ring-2 focus:ring-border-strong"
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
    <aside className="flex min-w-0 flex-col gap-6 border-t border-border px-8 py-6">
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

  // A card, and it has earned it: this is sized to one question, not to the
  // rail. The nested "say this" well steps UP to --secondary rather than down
  // into a translucent wash of the page behind it.
  return (
    <div className="rounded-lg border border-border bg-card p-5">
      <div className="flex items-center gap-1.5 text-sm text-muted-foreground">
        <Sparkles className="h-3.5 w-3.5" />
        {t("profile_view.ask_title")}
        <span className="ml-auto tabular-nums">
          {(idx % open.length) + 1}/{open.length}
        </span>
      </div>

      <p className="mt-2.5 text-lg font-semibold text-foreground-strong">
        {t(`profile_view.questions.${q.field}`)}
      </p>

      <div className="mt-3 flex items-start gap-2 rounded-md bg-secondary px-3 py-2 text-meta text-muted-foreground">
        <Mic className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        <span className="min-w-0">
          {t("profile_view.ask_say_prefix")}:{" "}
          <span className="text-foreground">“{t(`profile_view.says.${q.field}`)}”</span>
        </span>
      </div>

      <div className="mt-3 flex items-center justify-between gap-2">
        <span className="text-meta text-muted-foreground">
          {t(`profile_view.clusters.${q.cluster}.label`)}
        </span>
        <button
          type="button"
          data-testid="ask-next"
          onClick={() => setIdx((i) => i + 1)}
          className="inline-flex h-8 items-center gap-1 rounded-md border border-border-strong px-3 text-sm font-medium text-foreground transition-colors hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
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
            className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground disabled:opacity-40"
          >
            <RefreshCw className={cn("h-3.5 w-3.5", isRefetching && "animate-spin")} />
          </button>
        }
      />

      {/* The real container at its real height with skeleton bars — never a
          spinner beside a grey word, and never an invented zero. */}
      {isLoading && (
        <ul className="mt-stack space-y-stack" aria-hidden>
          {[0, 1].map((i) => (
            <li key={i} className="space-y-2 rounded-lg bg-card p-3 shadow-rim">
              <div className="h-3 w-4/5 animate-pulse rounded-full bg-sheen/[0.06]" />
              <div className="h-3 w-2/5 animate-pulse rounded-full bg-sheen/[0.06]" />
              <div className="h-7 animate-pulse rounded-md bg-sheen/[0.06]" />
            </li>
          ))}
        </ul>
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
          <p data-testid="reviews-error" className="mt-stack text-meta text-destructive">
            {error.message}
          </p>
        ))}

      {data && items.length === 0 && (
        <EmptyHint
          icon={ShieldQuestion}
          title={t("profile_view.reviews_empty_title")}
          body={t("profile_view.reviews_empty_body")}
        />
      )}

      {items.length > 0 && (
        <ul className="mt-stack space-y-stack">
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
    <div data-testid={testId} className="mt-stack flex items-start gap-3">
      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-secondary">
        <Icon className="h-4 w-4 text-muted-foreground" />
      </div>
      <div className="min-w-0 flex-1">
        <div className="text-body text-foreground">{title}</div>
        <p className="mt-0.5 text-meta text-muted-foreground">{body}</p>
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
  // Who the observation is about. Every person in the app carries a coloured
  // mark, this row included — it is what tells "about you" from "about Anna"
  // at a glance, without reading the line.
  const subject = candidate.is_person
    ? (candidate.person_name ?? t("profile_view.review_subject_user"))
    : t("profile_view.review_subject_user");

  return (
    <li className="rounded-lg bg-card p-3 shadow-rim">
      <div className="flex items-center gap-2 text-meta text-muted-foreground">
        <IdentityAvatar name={subject} size="sm" />
        <span className="min-w-0 truncate text-foreground">{subject}</span>
        <span className="ml-auto shrink-0 tabular-nums">
          {(candidate.confidence * 100).toFixed(0)}%
        </span>
      </div>

      {candidate.evidence && (
        <blockquote className="mt-2 text-meta italic text-foreground">
          “{candidate.evidence}”
        </blockquote>
      )}

      <div className="mt-2 flex flex-wrap items-center gap-1 text-meta text-muted-foreground">
        <span>{candidate.cluster}</span>
        <ChevronRight className="h-3 w-3" />
        <span className="text-foreground">{candidate.field}</span>
        <Badge variant="outline">{candidate.operation}</Badge>
      </div>

      <div className="mt-1.5 text-meta">
        <span className="text-muted-foreground">{t("profile_view.review_value")}: </span>
        <span className="text-foreground [overflow-wrap:anywhere]">
          {renderValue(t, candidate.value) || "—"}
        </span>
      </div>

      {candidate.reason && (
        <p className="mt-1 text-meta text-muted-foreground">
          {t("profile_view.review_reason")}: {candidate.reason}
        </p>
      )}

      <div className="mt-2.5 flex gap-1.5">
        <Button
          size="sm"
          variant="default"
          className="h-7 flex-1 text-meta"
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
          className="h-7 flex-1 text-meta"
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
        <ul className="mt-stack">
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

  // Selection is drawn on the WHOLE row, inset from the column edge — not on
  // the 28px avatar box. Rest is the rail's own ground; hover and open both
  // step up to --secondary, which is the only direction the ladder goes.
  return (
    <li>
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className={cn(
          "-mx-2 flex w-[calc(100%+1rem)] items-center gap-row rounded-md px-2 py-2 text-left transition-colors",
          open ? "bg-secondary" : "hover:bg-secondary",
        )}
      >
        <IdentityAvatar name={person.name} size="sm" />
        <span className="min-w-0 flex-1">
          <span
            className={cn(
              "block truncate text-body",
              open ? "text-foreground-strong" : "text-foreground",
            )}
          >
            {person.name}
          </span>
          <span className="block truncate text-meta text-muted-foreground">
            {person.relationship}
          </span>
        </span>
        <ChevronDown
          className={cn(
            "h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform",
            open && "rotate-180",
          )}
        />
      </button>

      {open && (
        <div className="py-stack pl-[2.375rem] pr-1">
          <dl className="space-y-stack text-meta">
            <div className="flex items-baseline justify-between gap-3">
              <dt className="text-muted-foreground">{t("profile_view.person_relationship")}</dt>
              <dd className="text-foreground">{person.relationship}</dd>
            </div>
            <div className="flex items-baseline justify-between gap-3">
              <dt className="text-muted-foreground">{t("profile_view.person_aliases")}</dt>
              <dd className="text-right [overflow-wrap:anywhere]">
                {person.aliases.length === 0 ? (
                  <span className="text-faint-foreground">—</span>
                ) : (
                  <span className="text-foreground">{person.aliases.join(" · ")}</span>
                )}
              </dd>
            </div>
          </dl>
          <p className="mt-stack flex items-start gap-1.5 text-meta text-muted-foreground">
            <Inbox className="mt-0.5 h-3.5 w-3.5 shrink-0" />
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
    <section className="flex min-w-0 flex-col bg-background lg:min-h-0">
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-5 py-2.5">
        <FileText className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        <h2 className="text-title font-semibold text-foreground-strong">
          {t("profile_view.section_source")}
        </h2>
        {data && (
          <span className="min-w-0 truncate font-mono text-micro text-muted-foreground">
            {data.path}
          </span>
        )}

        <div className="ml-auto flex items-center gap-1.5 text-meta text-muted-foreground">
          {editing ? (
            <>
              <span className="hidden items-center gap-1.5 xl:flex">
                <Lock className="h-3 w-3" />
                {t("profile_view.raw_editing_hint")}
              </span>
              <Button
                size="sm"
                variant="ghost"
                className="h-6 px-2 text-meta"
                onClick={() => setEditing(false)}
                disabled={save.isPending}
              >
                {t("profile_view.raw_cancel")}
              </Button>
              <Button
                size="sm"
                variant="default"
                className="h-6 px-2 text-meta"
                onClick={() => save.mutate(draft)}
                disabled={save.isPending}
              >
                <Save className={cn("mr-1 h-3 w-3", save.isPending && "animate-pulse")} />
                {save.isPending ? t("profile_view.raw_saving") : t("profile_view.raw_save")}
              </Button>
            </>
          ) : (
            <>
              {/* Something just wrote to the file: that is the section's one
                  live signal, so it is spent on --success rather than on
                  another grey pill. */}
              {isPulsing && (
                <span className="inline-flex items-center gap-1.5 rounded-full bg-secondary px-2 py-0.5 text-foreground">
                  <span className="relative flex h-1.5 w-1.5">
                    <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-success opacity-75" />
                    <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-success" />
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
                className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground disabled:opacity-40"
              >
                <RefreshCw className={cn("h-3.5 w-3.5", isRefetching && "animate-spin")} />
              </button>
              {data && (
                <Button
                  size="sm"
                  variant="outline"
                  className="h-6 px-2 text-meta"
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
        {/* The document at its real width with skeleton lines, not a spinner
            beside a word. */}
        {isLoading && (
          <div className="max-w-reading space-y-stack px-5 py-4" aria-hidden>
            <div className="h-4 w-1/3 animate-pulse rounded-full bg-sheen/[0.06]" />
            <div className="h-3 w-full animate-pulse rounded-full bg-sheen/[0.06]" />
            <div className="h-3 w-11/12 animate-pulse rounded-full bg-sheen/[0.06]" />
            <div className="h-3 w-4/5 animate-pulse rounded-full bg-sheen/[0.06]" />
          </div>
        )}

        {error && <p className="px-5 py-4 text-body text-destructive">{error.message}</p>}

        {data &&
          (editing ? (
            <textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              spellCheck={false}
              autoFocus
              aria-label="USER.md"
              className="block h-full min-h-[24rem] w-full resize-none bg-transparent px-5 py-4 font-mono text-meta text-foreground outline-none scrollbar-jarvis"
            />
          ) : (
            <div className="h-full overflow-y-auto px-5 py-4 scrollbar-jarvis">
              {body ? (
                // A profile is a document, so it is set at the reading step in
                // full ink and bounded to a reading measure. It used to run at
                // muted ink under small-caps headings, which reads as text
                // somebody disabled rather than text somebody wrote.
                <article
                  data-testid="profile-source-markdown"
                  className={cn(
                    PROSE_BASE,
                    "max-w-3xl text-base leading-7 text-foreground",
                    "prose-headings:text-foreground-strong",
                    "prose-h1:text-xl prose-h2:mt-group prose-h2:text-lg prose-h3:text-lg",
                    "prose-blockquote:border-l-2 prose-blockquote:border-border prose-blockquote:pl-4 prose-blockquote:font-normal prose-blockquote:not-italic prose-blockquote:text-muted-foreground",
                    "prose-p:text-foreground prose-li:text-foreground prose-strong:text-foreground-strong",
                  )}
                >
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{body}</ReactMarkdown>
                </article>
              ) : (
                <p className="text-body text-muted-foreground">
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

/**
 * The real three-rail shape at its real height, with skeleton bars. A centred
 * spinner in an empty window is the state that reads as "broken", because it
 * throws away every bit of structure the section is about to have.
 */
function LoadingState() {
  return (
    <div
      role="status"
      aria-busy="true"
      className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[minmax(320px,1fr)_minmax(0,1.15fr)_minmax(280px,330px)]"
    >
      <div className="space-y-group bg-sidebar px-6 py-7">
        {[0, 1, 2].map((group) => (
          <div key={group} className="space-y-stack">
            <div className="h-4 w-32 animate-pulse rounded-full bg-sheen/[0.06]" />
            {[0, 1, 2, 3].map((row) => (
              <div key={row} className="flex items-center justify-between gap-8">
                <div className="h-3 w-24 animate-pulse rounded-full bg-sheen/[0.06]" />
                <div className="h-3 w-28 animate-pulse rounded-full bg-sheen/[0.06]" />
              </div>
            ))}
          </div>
        ))}
      </div>
      <div className="max-w-reading space-y-stack bg-background px-5 py-6">
        <div className="h-5 w-2/5 animate-pulse rounded-full bg-sheen/[0.06]" />
        {[0, 1, 2, 3, 4].map((line) => (
          <div key={line} className="h-3 w-full animate-pulse rounded-full bg-sheen/[0.06]" />
        ))}
      </div>
      <div className="space-y-stack bg-sidebar px-5 py-8">
        <div className="h-28 animate-pulse rounded-lg bg-sheen/[0.06]" />
        <div className="h-20 animate-pulse rounded-lg bg-sheen/[0.06]" />
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
        <div className="max-w-form rounded-lg bg-card p-8 text-center shadow-rim">
          <div className="mx-auto mb-5 flex h-14 w-14 items-center justify-center rounded-full bg-secondary">
            <UserCircle2 className="h-6 w-6 text-muted-foreground" />
          </div>
          <h3 className="font-display text-page font-semibold text-foreground-strong">
            {t("profile_view.hero_name_placeholder")}
          </h3>
          <p className="mt-2 text-body text-foreground">{error.message}</p>
          <p className="mt-4 text-meta text-muted-foreground">{t("profile_view.no_user_hint")}</p>
          <Button className="mt-6" size="sm" variant="outline" onClick={onRetry}>
            <RefreshCw className="mr-2 h-3.5 w-3.5" /> {t("apikeys_view.retry")}
          </Button>
        </div>
      </div>
    );
  }
  return (
    <div className="flex items-center gap-3 p-6 text-body">
      <span className="text-destructive">
        {t("common.error_generic")}: {error.message}
      </span>
      <Button size="sm" variant="outline" onClick={onRetry}>
        {t("apikeys_view.retry")}
      </Button>
    </div>
  );
}
