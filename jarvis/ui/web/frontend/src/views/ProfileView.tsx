/**
 * ProfileView — who you are to the assistant, on one screen.
 *
 *                               [Details 4/19] [Memory and privacy]
 *          ┌──────────────────────────────────────┐
 *          │        photo · name · facts          │
 *          │     a sentence or two about you      │
 *          │ Conversations │ Agent runs │ … │ … │ │
 *          │ Activity           All Voice Chats … │
 *          │ ▁▂▃▅▇ one bar per week               │
 *          │ Favorite agents: the roster figures  │
 *          │ Insights: label ……………… figure        │
 *          └──────────────────────────────────────┘
 *
 * One centred column, top to bottom. It scrolls only when the window is too
 * short for it. Everything else — the detail groups, the portrait, memory and
 * privacy, the raw file — opens in a side panel from the two quiet links at
 * the top right.
 *
 * The raw-file query and its live WS subscription are held HERE because both
 * panels need it: the details panel parses its audit trail for provenance,
 * the memory panel renders it and reads its Do Not Record list.
 */
import { useCallback, useMemo, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, ArrowLeft, Lock, RefreshCw, UserCircle2, UserRound } from "lucide-react";

import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { fill, useT } from "@/i18n";
import { ActivityChart } from "@/views/profile/ActivityChart";
import { FieldGroup } from "@/views/profile/FieldGroup";
import { MemorySection } from "@/views/profile/MemorySection";
import { PortraitSection } from "@/views/profile/PortraitSection";
import { ProfileDrawer } from "@/views/profile/ProfileDrawer";
import { ProfileHero } from "@/views/profile/ProfileHero";
import { FavoriteAgents, InsightList } from "@/views/profile/ProfileInsights";
import { ProfileStats } from "@/views/profile/ProfileStats";
import { SourceCard, useSourceDocument } from "@/views/profile/SourceCard";
import { fetchJson, statusOf, type ProfileResponse } from "@/views/profile/api";
import { PAGE_GROUPS, TOTAL_FIELDS, countFilled } from "@/views/profile/ledger";
import { parseObservations } from "@/views/profile/provenance";

type Panel = "details" | "memory" | null;

export function ProfileView() {
  const t = useT();
  const [panel, setPanel] = useState<Panel>(null);
  const [showSource, setShowSource] = useState(false);

  const { data, isLoading, error, refetch } = useQuery<ProfileResponse, Error>({
    queryKey: ["profile"],
    queryFn: () => fetchJson<ProfileResponse>("/api/profile"),
    retry: false,
  });

  // Held at the root on purpose — see the module note.
  const source = useSourceDocument();
  const raw = source.data?.content ?? null;
  const observations = useMemo(() => parseObservations(raw), [raw]);

  const meta = useMemo(() => (data?.user.meta ?? {}) as Record<string, unknown>, [data]);
  const filled = useMemo(() => countFilled(meta), [meta]);

  const closePanel = useCallback(() => {
    source.cancelEditing();
    setShowSource(false);
    setPanel(null);
  }, [source]);

  return (
    <div className="relative h-full overflow-hidden bg-background">
      <div className="relative h-full overflow-y-auto px-8 scrollbar-jarvis">
        <h1 className="sr-only">{t("profile_view.title")}</h1>
        {/* Inside the scroller, so the links scroll away with the page
            instead of floating over it. */}
        {data && (
          <div className="absolute right-6 top-3 z-10 flex items-center gap-1">
            <PanelLink
              testId="open-details"
              icon={<UserRound />}
              label={t("profile_view.door_details_title")}
              count={`${filled}/${TOTAL_FIELDS}`}
              onClick={() => setPanel("details")}
            />
            <PanelLink
              testId="open-memory"
              icon={<Lock />}
              label={t("profile_view.memory_title")}
              onClick={() => setPanel("memory")}
            />
          </div>
        )}
        <div className="mx-auto flex min-h-full w-full max-w-3xl flex-col justify-center gap-8 py-14">
          {isLoading && <ProfileSkeleton label={t("common.loading")} />}

          {error && <ProfileErrorState error={error} onRetry={() => refetch()} />}

          {data && (
            <>
              <ProfileHero data={data} meta={meta} />
              <ProfileStats />
              <ActivityChart />
              <FavoriteAgents />
              <InsightList />
            </>
          )}
        </div>
      </div>


      {data && panel === "details" && (
        <ProfileDrawer
          testId="details-panel"
          title={t("profile_view.door_details_title")}
          description={fill(t("profile_view.door_details_sub"), { 0: filled, 1: TOTAL_FIELDS })}
          onClose={closePanel}
        >
          <div className="flex flex-col gap-8">
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
          </div>
        </ProfileDrawer>
      )}

      {data && panel === "memory" && (
        <ProfileDrawer
          testId="memory-panel"
          title={t("profile_view.memory_title")}
          description={t("profile_view.memory_description")}
          onClose={closePanel}
        >
          {showSource ? (
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
          ) : (
            <MemorySection
              bare
              name={data.user.name?.trim() || null}
              raw={raw}
              fileUpdatedMs={source.data?.mtime_ms ?? null}
              onOpenSource={() => setShowSource(true)}
            />
          )}
        </ProfileDrawer>
      )}
    </div>
  );
}

/** A quiet text link at the top right; each opens a side panel. */
function PanelLink({
  icon,
  label,
  count,
  onClick,
  testId,
}: {
  icon: ReactNode;
  label: string;
  count?: string;
  onClick: () => void;
  testId: string;
}) {
  return (
    <button
      type="button"
      data-testid={testId}
      onClick={onClick}
      className="flex h-9 items-center gap-2 rounded-md px-2.5 text-sm text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring [&>svg]:h-4 [&>svg]:w-4"
    >
      {icon}
      <span>{label}</span>
      {count && <span className="tabular-nums text-muted-foreground">{count}</span>}
    </button>
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
    <div role="status" aria-busy="true" aria-label={label} className="flex flex-col gap-9">
      <div className="flex flex-col items-center gap-4">
        <div className="h-20 w-20 animate-pulse rounded-3xl bg-secondary" />
        <div className="h-7 w-40 animate-pulse rounded-md bg-secondary" />
        <div className="h-3.5 w-72 max-w-full animate-pulse rounded-full bg-secondary" />
        <div className="h-16 w-full max-w-lg animate-pulse rounded-xl bg-secondary" />
      </div>
      <div className="h-20 animate-pulse rounded-xl bg-secondary" />
      <div className="h-48 animate-pulse rounded-xl bg-secondary" />
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
