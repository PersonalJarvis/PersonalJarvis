/**
 * ProfileView — what the assistant knows about you, and how to change it.
 *
 * One column, read top to bottom like any settings page:
 *
 *   ┌────────────────────────────────────────────────────────────┐
 *   │ Photo · name · language · timezone                         │
 *   │ Since │ Conversations │ Details known │ Last change         │
 *   ├────────────────────────────────────────────────────────────┤
 *   │ About you · How {name} talks to you · How you work ·       │
 *   │ What matters to you — known details as rows, missing ones  │
 *   │ as one line of "add" chips per group                       │
 *   ├────────────────────────────────────────────────────────────┤
 *   │ How {name} sees you — the written portrait + feedback      │
 *   ├────────────────────────────────────────────────────────────┤
 *   │ Memory and privacy — rules, wiki page, the file, and what  │
 *   │ is never recorded                                          │
 *   └────────────────────────────────────────────────────────────┘
 *
 * The raw file (USER.md) opens in place as a sub-page with a back link,
 * rather than as a tab: it is the source behind the page, not a peer of it.
 *
 * The raw-file query and its live WS subscription are held HERE because both
 * views need it: the sub-page renders it, and the main page parses its audit
 * trail for provenance and its Do Not Record list.
 */
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, ArrowLeft, RefreshCw, UserCircle2 } from "lucide-react";

import { PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { FieldGroup } from "@/views/profile/FieldGroup";
import { MemorySection } from "@/views/profile/MemorySection";
import { PortraitSection } from "@/views/profile/PortraitSection";
import { ProfileHero } from "@/views/profile/ProfileHero";
import { SourceCard, useSourceDocument } from "@/views/profile/SourceCard";
import { fetchJson, statusOf, type ProfileResponse } from "@/views/profile/api";
import { PAGE_GROUPS } from "@/views/profile/ledger";
import { parseObservations } from "@/views/profile/provenance";

/** The reading column: wide enough for a row, narrow enough to scan. */
const COLUMN = "mx-auto w-full max-w-[880px]";

export function ProfileView() {
  const t = useT();
  const [showSource, setShowSource] = useState(false);

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
    <div className="flex h-full flex-col overflow-y-auto bg-background px-8 pb-10 scrollbar-jarvis">
      <div className={COLUMN}>
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
              onClick={() => {
                void refetch();
                source.refetch();
              }}
              disabled={isRefetching}
              title={t("profile_view.reload_tooltip")}
              aria-label={t("profile_view.reload_tooltip")}
            >
              <RefreshCw className={cn(isRefetching && "animate-spin")} />
            </Button>
          }
        />

        {isLoading && <ProfileSkeleton label={t("common.loading")} />}

        {error && <ProfileErrorState error={error} onRetry={() => refetch()} />}

        {data && showSource && (
          <div className="flex flex-col gap-4">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="self-start text-muted-foreground"
              onClick={() => {
                source.cancelEditing();
                setShowSource(false);
              }}
              data-testid="source-back"
            >
              <ArrowLeft aria-hidden />
              {t("profile_view.back_to_profile")}
            </Button>
            <SourceCard doc={source} />
          </div>
        )}

        {data && !showSource && (
          <div className="flex flex-col gap-10">
            <ProfileHero data={data} meta={meta} />
            {PAGE_GROUPS.map((g) => (
              <FieldGroup
                key={g.id}
                id={g.id}
                fields={g.fields}
                meta={meta}
                observations={observations}
              />
            ))}
            <PortraitSection />
            <MemorySection
              name={data.user.name?.trim() || null}
              raw={raw}
              fileUpdatedMs={source.data?.mtime_ms ?? null}
              onOpenSource={() => setShowSource(true)}
            />
          </div>
        )}
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------
// Loading and failure
// ----------------------------------------------------------------------

/**
 * The real layout at its real height with skeleton bars. A zero in a slot
 * that has not loaded yet would read as a fact, so there are none.
 */
function ProfileSkeleton({ label }: { label: string }) {
  return (
    <div role="status" aria-busy="true" aria-label={label} className="flex flex-col gap-10">
      <div className="rounded-xl border border-border bg-card">
        <div className="flex items-center gap-5 p-6">
          <div className="h-20 w-20 shrink-0 animate-pulse rounded-full bg-secondary" />
          <div className="flex min-w-0 flex-1 flex-col gap-2.5">
            <div className="h-6 w-48 max-w-full animate-pulse rounded-md bg-secondary" />
            <div className="h-3.5 w-64 max-w-full animate-pulse rounded-full bg-secondary" />
          </div>
        </div>
        <div className="grid grid-cols-2 border-t border-border sm:grid-cols-4">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="px-5 py-4">
              <div className="h-3 w-16 animate-pulse rounded-full bg-secondary" />
              <div className="mt-2 h-5 w-20 animate-pulse rounded-md bg-secondary" />
            </div>
          ))}
        </div>
      </div>
      {[0, 1].map((i) => (
        <div key={i} className="flex flex-col gap-3">
          <div className="h-5 w-40 animate-pulse rounded-md bg-secondary" />
          <div className="divide-y divide-border rounded-xl border border-border bg-card">
            {[0, 1, 2].map((j) => (
              <div key={j} className="flex items-center justify-between px-5 py-4">
                <div className="h-3.5 w-28 animate-pulse rounded-full bg-secondary" />
                <div className="h-3.5 w-24 animate-pulse rounded-full bg-secondary" />
              </div>
            ))}
          </div>
        </div>
      ))}
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
        title={t("profile_view.unavailable_title")}
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
    <div className="flex items-start gap-3 rounded-xl border border-destructive/20 bg-destructive/[0.12] p-5">
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
