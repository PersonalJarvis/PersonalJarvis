/**
 * ProfileView — what Jarvis knows about you, and the file it keeps it in.
 *
 * Two tabs, because the view answers two different questions and mixing them
 * was the old layout's mistake: "what does it know about me" is a ledger of
 * fields, "what does the file say" is a document. They shared one screen as
 * three standing rails, which meant neither got a readable width and both were
 * permanently half-visible.
 *
 *   ┌──────────────────────────────────────────────────────────────────┐
 *   │ PageHeader — Profile · refresh · [ Knowledge | Source file ]     │
 *   ├──────────────────────────────────────────────────────────────────┤
 *   │ Identity card — portrait, address, stage, progress, people       │
 *   ├───────────────────────────────────┬──────────────────────────────┤
 *   │ Cluster cards, two columns        │ Rail: the open question,     │
 *   │ every field, editable in place    │ the review queue, the people │
 *   └───────────────────────────────────┴──────────────────────────────┘
 *
 * Below 1280 px the rail becomes a row under the ledger, and below 1024 px
 * everything is one column. The header never scrolls; the body below it does.
 *
 * The raw-file query and its live WS subscription are held HERE rather than in
 * the source tab, so a Curator write still refreshes the ledger while the
 * reader is looking at the knowledge tab.
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, RefreshCw, UserCircle2 } from "lucide-react";

import { PageHeader } from "@/components/layout/PageHeader";
import { TabBar } from "@/components/layout/SectionTabBar";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { AskCard } from "@/views/profile/AskCard";
import { IdentityCard } from "@/views/profile/IdentityCard";
import { KnowledgeLedger } from "@/views/profile/KnowledgeLedger";
import { PeopleCard } from "@/views/profile/PeopleCard";
import { ReviewsCard } from "@/views/profile/ReviewsCard";
import { SourceCard, useSourceDocument } from "@/views/profile/SourceCard";
import { fetchJson, statusOf, type ProfileResponse } from "@/views/profile/api";

type TabId = "knowledge" | "source";

/** The main grid: ledger left, rail right, and one column below 1280 px. */
const SPLIT = "grid gap-5 xl:grid-cols-[minmax(0,1fr)_336px]";
/** The rail: a row of cards under the ledger until it can stand beside it. */
const RAIL = "grid gap-5 content-start sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-1";

export function ProfileView() {
  const t = useT();
  const [tab, setTab] = useState<TabId>("knowledge");

  const { data, isLoading, error, refetch, isRefetching } = useQuery<ProfileResponse, Error>({
    queryKey: ["profile"],
    queryFn: () => fetchJson<ProfileResponse>("/api/profile"),
    retry: false,
  });

  // Held at the root on purpose — see the module note.
  const source = useSourceDocument();

  const meta = (data?.user.meta ?? {}) as Record<string, unknown>;

  return (
    <div className="flex h-full flex-col overflow-hidden bg-background">
      <div className="shrink-0 px-8">
        <PageHeader
          icon={<UserCircle2 />}
          title={t("profile_view.title")}
          description={t("profile_view.subtitle")}
          actions={
            <Button
              type="button"
              size="icon"
              variant="ghost"
              className="text-muted-foreground"
              onClick={() => refetch()}
              disabled={isRefetching}
              title={t("profile_view.reload_tooltip")}
              aria-label={t("profile_view.reload_tooltip")}
            >
              <RefreshCw className={cn(isRefetching && "animate-spin")} />
            </Button>
          }
          tabs={
            <TabBar
              tabs={[
                { id: "knowledge", label: t("profile_view.section_knowledge") },
                { id: "source", label: t("profile_view.section_source") },
              ]}
              active={tab}
              onChange={(id) => setTab(id as TabId)}
            />
          }
        />
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-8 pb-8 pt-6 scrollbar-jarvis">
        {isLoading && <ProfileSkeleton label={t("common.loading")} />}

        {error && <ProfileErrorState error={error} onRetry={() => refetch()} />}

        {data && tab === "knowledge" && (
          <div className="flex flex-col gap-5">
            <IdentityCard data={data} meta={meta} onOpenSource={() => setTab("source")} />
            <div className={SPLIT}>
              <KnowledgeLedger meta={meta} />
              <aside className={RAIL}>
                <AskCard meta={meta} />
                <ReviewsCard reviewsCount={data.reviews_count} />
                <PeopleCard people={data.people} />
              </aside>
            </div>
          </div>
        )}

        {data && tab === "source" && <SourceCard doc={source} />}
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------
// Loading and failure
// ----------------------------------------------------------------------

/**
 * The real layout at its real height with skeleton bars. A centred spinner in
 * an empty window is the state that reads as "broken", because it throws away
 * every bit of structure the section is about to have — and a zero in a slot
 * that has not loaded yet is worse, because it reads as a fact.
 */
function ProfileSkeleton({ label }: { label: string }) {
  return (
    <div role="status" aria-busy="true" aria-label={label} className="flex flex-col gap-5">
      <div className="rounded-lg border border-border bg-card p-5">
        <div className="flex items-center gap-5">
          <div className="h-14 w-14 shrink-0 animate-pulse rounded-full bg-secondary" />
          <div className="flex min-w-0 flex-1 flex-col gap-2">
            <div className="h-5 w-56 max-w-full animate-pulse rounded-md bg-secondary" />
            <div className="h-3 w-32 max-w-full animate-pulse rounded-full bg-secondary" />
          </div>
          <div className="hidden w-64 shrink-0 flex-col gap-2 sm:flex">
            <div className="h-3 w-40 animate-pulse rounded-full bg-secondary" />
            <div className="h-1.5 w-full animate-pulse rounded-full bg-secondary" />
          </div>
        </div>
      </div>

      <div className={SPLIT}>
        <div className="grid gap-5 lg:grid-cols-2">
          {[0, 1, 2, 3].map((i) => (
            <SkeletonCard key={i} rows={4} />
          ))}
        </div>
        <div className={RAIL}>
          {[0, 1, 2].map((i) => (
            <SkeletonCard key={i} rows={3} />
          ))}
        </div>
      </div>
    </div>
  );
}

function SkeletonCard({ rows }: { rows: number }) {
  return (
    <div className="rounded-lg border border-border bg-card p-5">
      <div className="h-4 w-28 animate-pulse rounded-md bg-secondary" />
      <div className="mt-4 flex flex-col gap-3">
        {Array.from({ length: rows }, (_, i) => (
          <div key={i} className="flex items-center justify-between gap-8">
            <div className="h-3 w-24 animate-pulse rounded-full bg-secondary" />
            <div className="h-3 w-20 animate-pulse rounded-full bg-secondary" />
          </div>
        ))}
      </div>
    </div>
  );
}

/**
 * 503 means the profile subsystem is deliberately not running in this session
 * (a mock brain, a provider without memory integration). That is a state, not
 * a fault, and it gets the calm treatment; everything else is a real failure
 * and says so.
 */
function ProfileErrorState({ error, onRetry }: { error: Error; onRetry: () => void }) {
  const t = useT();

  if (statusOf(error) === 503) {
    return (
      <EmptyState
        icon={<UserCircle2 />}
        title={t("profile_view.hero_name_placeholder")}
        description={t("profile_view.no_user_hint")}
        actions={
          <Button size="sm" variant="outline" onClick={onRetry}>
            <RefreshCw />
            {t("common.retry")}
          </Button>
        }
      />
    );
  }

  return (
    <div className="flex items-start gap-3 rounded-lg border border-destructive/20 bg-destructive/[0.12] p-5">
      <AlertTriangle aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
      <div className="min-w-0 flex-1">
        <p className="text-base font-medium text-foreground-strong">
          {t("profile_view.error_title")}
        </p>
        <p className="mt-1 text-base text-foreground-secondary [overflow-wrap:anywhere]">
          {error.message}
        </p>
      </div>
      <Button size="sm" variant="outline" className="shrink-0" onClick={onRetry}>
        {t("common.retry")}
      </Button>
    </div>
  );
}
