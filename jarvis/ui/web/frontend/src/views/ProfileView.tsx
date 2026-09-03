/**
 * ProfileView — the file the assistant keeps on you.
 *
 * The section answers one question: *what do you know about me, and how do you
 * know it?* Everything on the page is either a fact, the receipt for a fact,
 * or the boundary the assistant will not cross.
 *
 *   ┌──────────────────────────────────────────────────────────────────┐
 *   │ PageHeader — Profile · refresh · [ The file | Source ]           │
 *   ├──────────────────────────────────────────────────────────────────┤
 *   │ Name · language · timezone · since · conversations   6 of 18     │
 *   ├───────────────────────────────────┬──────────────────────────────┤
 *   │ Entries — five sections, known    │ How the assistant describes  │
 *   │ facts listed, gaps folded into    │ you · your standing rules ·  │
 *   │ one line, every entry showing     │ what is never stored · the   │
 *   │ the date it was learned           │ long-form page in the wiki   │
 *   └───────────────────────────────────┴──────────────────────────────┘
 *
 * Three cards were removed rather than restyled, because no styling makes an
 * empty pipe full: the review queue (the legacy curator has been disabled
 * since 2026-05-17, so it could never fill), the people list (it read
 * `data/workspace/people/`, which is empty — Contacts owns people), and the
 * "would love to know" prompt (it wrote to no endpoint; the gap expander in
 * the ledger now fills fields for real).
 *
 * The raw-file query and its live WS subscription are held HERE rather than in
 * the source tab, because both tabs need it: the source tab renders it, and
 * the file tab parses its audit trail for provenance.
 */
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, RefreshCw, UserCircle2 } from "lucide-react";

import { PageHeader } from "@/components/layout/PageHeader";
import { TabBar } from "@/components/layout/SectionTabBar";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { DossierHeader } from "@/views/profile/DossierHeader";
import { EntryLedger } from "@/views/profile/EntryLedger";
import { NeverStoredCard } from "@/views/profile/NeverStoredCard";
import { PortraitCard } from "@/views/profile/PortraitCard";
import { RulesCard } from "@/views/profile/RulesCard";
import { SourceCard, useSourceDocument } from "@/views/profile/SourceCard";
import { WikiCard } from "@/views/profile/WikiCard";
import { fetchJson, statusOf, type ProfileResponse } from "@/views/profile/api";
import { parseObservations } from "@/views/profile/provenance";

type TabId = "file" | "source";

/** Entries left, rail right, and one column below 1280 px. */
const SPLIT = "grid gap-5 xl:grid-cols-[minmax(0,1fr)_360px]";
/** The rail: a row of cards under the entries until it can stand beside them. */
const RAIL = "grid gap-5 content-start sm:grid-cols-2 xl:grid-cols-1";

export function ProfileView() {
  const t = useT();
  const [tab, setTab] = useState<TabId>("file");

  const { data, isLoading, error, refetch, isRefetching } = useQuery<ProfileResponse, Error>({
    queryKey: ["profile"],
    queryFn: () => fetchJson<ProfileResponse>("/api/profile"),
    retry: false,
  });

  // Held at the root on purpose — see the module note.
  const source = useSourceDocument();
  const raw = source.data?.content ?? null;
  const observations = useMemo(() => parseObservations(raw), [raw]);

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
                { id: "file", label: t("profile_view.tab_file") },
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

        {data && tab === "file" && (
          <div className="flex flex-col gap-5">
            <DossierHeader data={data} meta={meta} />
            <div className={SPLIT}>
              <EntryLedger meta={meta} observations={observations} />
              <aside className={RAIL}>
                <PortraitCard />
                <RulesCard />
                <NeverStoredCard raw={raw} />
                <WikiCard name={data.user.name?.trim() || null} />
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
      <div className="flex items-center gap-5 border-b border-border pb-5">
        <div className="h-16 w-16 shrink-0 animate-pulse rounded-full bg-secondary" />
        <div className="flex min-w-0 flex-1 flex-col gap-2">
          <div className="h-6 w-56 max-w-full animate-pulse rounded-md bg-secondary" />
          <div className="h-3 w-72 max-w-full animate-pulse rounded-full bg-secondary" />
        </div>
        <div className="hidden h-3 w-32 shrink-0 animate-pulse rounded-full bg-secondary sm:block" />
      </div>

      <div className={SPLIT}>
        <div className="rounded-lg border border-border bg-card">
          {[0, 1, 2, 3, 4].map((i) => (
            <div key={i} className="border-b border-border px-5 py-4 last:border-b-0">
              <div className="h-4 w-32 animate-pulse rounded-md bg-secondary" />
              <div className="mt-3 flex flex-col gap-2">
                <div className="h-3 w-full animate-pulse rounded-full bg-secondary" />
                <div className="h-3 w-2/3 animate-pulse rounded-full bg-secondary" />
              </div>
            </div>
          ))}
        </div>
        <div className={RAIL}>
          {[0, 1, 2].map((i) => (
            <div key={i} className="rounded-lg border border-border bg-card p-5">
              <div className="h-4 w-28 animate-pulse rounded-md bg-secondary" />
              <div className="mt-4 flex flex-col gap-2">
                <div className="h-3 w-full animate-pulse rounded-full bg-secondary" />
                <div className="h-3 w-4/5 animate-pulse rounded-full bg-secondary" />
              </div>
            </div>
          ))}
        </div>
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
        title={t("profile_view.header_unnamed")}
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
