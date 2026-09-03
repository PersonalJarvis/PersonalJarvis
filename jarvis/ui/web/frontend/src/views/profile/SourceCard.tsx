/**
 * USER.md itself — rendered as the document it is, and editable by hand.
 *
 * Data flow: GET /api/profile/raw → React-Query cache. Live sync over the WS
 * bus: every Curator merge publishes ProfileUpdated, the subscriber below
 * invalidates both profile queries, and the file on screen is current seconds
 * after a write. The subscription lives in `useSourceDocument`, which the view
 * holds at its root — so it keeps running while the reader is on the knowledge
 * tab, and a background write still refreshes the ledger.
 *
 * The front matter is split off before rendering: the ledger already IS that
 * block drawn as fields, and printing it twice is the same facts in a worse
 * format. The curator's HTML anchors (`<!-- curator:observations:start -->`)
 * are machinery, not text — react-markdown escapes raw HTML instead of
 * rendering it, so leaving them in prints them verbatim on the page.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { AlertTriangle, Clock, FileText, Lock, Pencil, RefreshCw, Save } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Textarea } from "@/components/ui/textarea";
import { PROSE_BASE, splitFrontMatter } from "@/components/outputs/MarkdownProse";
import { getWSClient } from "@/hooks/useWebSocket";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";
import { fetchJson, type RawProfileResponse } from "@/views/profile/api";

export interface SourceDocument {
  data: RawProfileResponse | undefined;
  isLoading: boolean;
  error: Error | null;
  isRefetching: boolean;
  refetch: () => void;
  editing: boolean;
  draft: string;
  setDraft: (value: string) => void;
  startEditing: () => void;
  cancelEditing: () => void;
  save: () => void;
  isSaving: boolean;
  /** True for a couple of seconds after a ProfileUpdated event landed. */
  pulsing: boolean;
  /** The document body with front matter and curator anchors removed. */
  body: string;
}

/**
 * Owns the raw file: the query, the live subscription, the draft and the save.
 * Held by the view root so the subscription survives a tab switch.
 */
export function useSourceDocument(): SourceDocument {
  const t = useT();
  const queryClient = useQueryClient();
  const pushToast = useEventStore((s) => s.pushToast);
  const [pulsing, setPulsing] = useState(false);

  // `draft` is the working copy; `editBaseMtime` is frozen at edit-start so the
  // backend's optimistic-concurrency guard stays meaningful even if a
  // background refetch moves `data.mtime_ms` underneath us.
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [editBaseMtime, setEditBaseMtime] = useState<number | null>(null);

  const { data, isLoading, error, refetch, isRefetching } = useQuery<RawProfileResponse, Error>({
    queryKey: ["profile", "raw"],
    queryFn: () => fetchJson<RawProfileResponse>("/api/profile/raw"),
    retry: false,
    staleTime: 0,
  });

  useEffect(() => {
    const client = getWSClient();
    if (!client) return;
    const unsubscribe = client.subscribe((raw) => {
      const env = raw as { event_name?: unknown };
      if (env.event_name !== "ProfileUpdated") return;
      // The ledger always refreshes…
      queryClient.invalidateQueries({ queryKey: ["profile"] });
      // …but the raw text is never replaced while it is being edited: that
      // would wipe the draft mid-keystroke.
      if (!editing) {
        queryClient.invalidateQueries({ queryKey: ["profile", "raw"] });
      }
      setPulsing(true);
    });
    return unsubscribe;
  }, [queryClient, editing]);

  useEffect(() => {
    if (!pulsing) return;
    const id = window.setTimeout(() => setPulsing(false), 2000);
    return () => window.clearTimeout(id);
  }, [pulsing]);

  const saveMutation = useMutation({
    mutationFn: async (content: string) => {
      const res = await fetch("/api/profile/raw", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content, mtime_ms: editBaseMtime }),
      });
      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as { detail?: string };
        const err = new Error(body.detail ?? `HTTP ${res.status}`) as Error & { status?: number };
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

  const startEditing = useCallback(() => {
    setDraft(data?.content ?? "");
    setEditBaseMtime(data?.mtime_ms ?? null);
    setEditing(true);
  }, [data?.content, data?.mtime_ms]);

  const cancelEditing = useCallback(() => setEditing(false), []);

  // Escape leaves edit mode, the way it cancels every other inline editor in
  // this view. The draft is dropped, which is why the button says Cancel.
  useEffect(() => {
    if (!editing) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setEditing(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [editing]);

  const body = useMemo(() => {
    if (!data) return "";
    return splitFrontMatter(data.content)
      .body.replace(/<!--[\s\S]*?-->/g, "")
      .replace(/[ \t]+$/gm, "")
      .replace(/\n{3,}/g, "\n\n")
      .trim();
  }, [data]);

  return {
    data,
    isLoading,
    error,
    isRefetching,
    refetch: () => void refetch(),
    editing,
    draft,
    setDraft,
    startEditing,
    cancelEditing,
    save: () => saveMutation.mutate(draft),
    isSaving: saveMutation.isPending,
    pulsing,
    body,
  };
}

export function SourceCard({ doc }: { doc: SourceDocument }) {
  const t = useT();
  const lastUpdate = doc.data?.mtime_ms ? new Date(doc.data.mtime_ms) : null;

  return (
    <Card className="profile-rise flex flex-col">
      <CardHeader className="flex-row flex-wrap items-center justify-between gap-x-3 gap-y-2 pb-3">
        <span className="flex min-w-0 items-center gap-2">
          <FileText aria-hidden className="h-4 w-4 shrink-0 text-muted-foreground" />
          <span className="text-lg font-semibold text-foreground-strong">
            {t("profile_view.section_source")}
          </span>
          {doc.data && (
            <span className="min-w-0 truncate font-mono text-sm text-muted-foreground">
              {doc.data.path}
            </span>
          )}
        </span>

        <span className="flex shrink-0 items-center gap-2">
          {doc.editing ? (
            <>
              <span className="hidden items-center gap-1.5 text-sm text-muted-foreground xl:flex">
                <Lock aria-hidden className="h-3.5 w-3.5 shrink-0" />
                {t("profile_view.raw_editing_hint")}
              </span>
              <Button
                size="sm"
                variant="ghost"
                onClick={doc.cancelEditing}
                disabled={doc.isSaving}
              >
                {t("profile_view.raw_cancel")}
              </Button>
              <Button size="sm" variant="default" onClick={doc.save} disabled={doc.isSaving}>
                <Save className={cn(doc.isSaving && "animate-pulse")} />
                {doc.isSaving ? t("profile_view.raw_saving") : t("profile_view.raw_save")}
              </Button>
            </>
          ) : (
            <>
              {/* Something just wrote to the file. That is the section's one
                  live signal, so it is what --success is spent on here. */}
              {doc.pulsing && (
                <span className="inline-flex items-center gap-1.5 rounded-md border border-success/20 bg-success/[0.12] px-2 py-0.5 text-sm text-success">
                  <span className="relative flex h-1.5 w-1.5">
                    <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-success opacity-75" />
                    <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-success" />
                  </span>
                  {t("profile_view.just_updated")}
                </span>
              )}
              {lastUpdate && (
                <span className="hidden items-center gap-1.5 text-sm text-muted-foreground xl:inline-flex">
                  <Clock aria-hidden className="h-3.5 w-3.5 shrink-0" />
                  {t("profile_view.source_updated").replace(
                    "{0}",
                    lastUpdate.toLocaleDateString(),
                  )}
                </span>
              )}
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="h-8 w-8 text-muted-foreground"
                onClick={doc.refetch}
                disabled={doc.isRefetching}
                title={t("profile_view.reload_tooltip")}
                aria-label={t("profile_view.reload_tooltip")}
              >
                <RefreshCw className={cn(doc.isRefetching && "animate-spin")} />
              </Button>
              {doc.data && (
                <Button size="sm" variant="outline" onClick={doc.startEditing}>
                  <Pencil />
                  {t("profile_view.raw_edit")}
                </Button>
              )}
            </>
          )}
        </span>
      </CardHeader>

      <CardContent>
        {doc.isLoading && (
          <div className="flex max-w-reading flex-col gap-3" aria-hidden>
            <div className="h-5 w-1/3 animate-pulse rounded-md bg-secondary" />
            {[0, 1, 2, 3].map((i) => (
              <div
                key={i}
                className={cn(
                  "h-3 animate-pulse rounded-full bg-secondary",
                  i === 3 ? "w-4/5" : "w-full",
                )}
              />
            ))}
          </div>
        )}

        {doc.error && (
          <p className="flex items-start gap-2 rounded-md border border-destructive/20 bg-destructive/[0.12] p-3 text-base text-destructive">
            <AlertTriangle aria-hidden className="mt-0.5 h-4 w-4 shrink-0" />
            <span className="min-w-0 [overflow-wrap:anywhere]">{doc.error.message}</span>
          </p>
        )}

        {doc.data &&
          (doc.editing ? (
            <Textarea
              value={doc.draft}
              onChange={(e) => doc.setDraft(e.target.value)}
              spellCheck={false}
              autoFocus
              rows={26}
              aria-label={doc.data.path}
              className="resize-y font-mono leading-6 scrollbar-jarvis"
            />
          ) : doc.body ? (
            // A profile is a document, so it is set at the reading step in full
            // ink and bounded to a reading measure — not muted text under
            // small-caps headings, which reads as something somebody disabled.
            <article
              data-testid="profile-source-markdown"
              className={cn(
                PROSE_BASE,
                "max-w-reading text-base leading-7 text-foreground",
                "prose-headings:text-foreground-strong",
                "prose-h1:text-xl prose-h2:text-lg prose-h3:text-lg",
                "prose-blockquote:border-l-2 prose-blockquote:border-border prose-blockquote:pl-4 prose-blockquote:font-normal prose-blockquote:not-italic prose-blockquote:text-muted-foreground",
                "prose-p:text-foreground prose-li:text-foreground prose-strong:text-foreground-strong",
              )}
            >
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{doc.body}</ReactMarkdown>
            </article>
          ) : (
            <EmptyState
              icon={<FileText />}
              title={t("profile_view.source_empty_title")}
              description={t("profile_view.source_empty_body")}
              actions={
                <Button size="sm" variant="default" onClick={doc.startEditing}>
                  <Pencil />
                  {t("profile_view.raw_edit")}
                </Button>
              }
            />
          ))}
      </CardContent>
    </Card>
  );
}
