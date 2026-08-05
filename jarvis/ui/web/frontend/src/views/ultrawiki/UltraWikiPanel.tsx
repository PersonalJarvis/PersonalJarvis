/**
 * Ultra-mode body of the Wiki section (design doc 04) — rendered by WikiView
 * INSTEAD of the normal wiki workspace while UltraWiki mode is on (D-5).
 *
 * Lazy-loaded from WikiView (the WikiGraph pattern) so normal-mode users
 * never pay for this chunk. Owns the Ultra status poll: every 30 s at rest,
 * every 4 s while a sync job is active (the wiki health polling idiom).
 *
 * Layout: honest degradation banner → thin status ribbon → the five tabs
 * Overview | Ask | Sources | Contents | Settings. A dead required slot banners
 * the problem and links to the settings tab — the mode is never flipped
 * silently (design doc 04, mode-switch rules).
 *
 * "Overview" leads on purpose. Every other tab answers a question you already
 * know to ask; a user whose knowledge base looks empty does not know which one
 * of them holds the reason, and hunting through four screens is how a single
 * missing import step went undiagnosed for days. It replaced a seven-row
 * checklist that led with six green rows and one that was wrong — the
 * checklist itself is still there, one disclosure inside the overview.
 *
 * The ribbon is hidden on the overview: that tab already draws the corpus and
 * its backlog in full, and a screen that says the same number twice invites
 * the reader to check whether the two agree.
 */
import { useState } from "react";
import {
  AlertTriangle,
  Compass,
  Database,
  Gauge,
  MessageCircleQuestion,
  Plug,
  Settings2,
  Type,
  UsersRound,
} from "lucide-react";
import { useQuery } from "@tanstack/react-query";

import { cn } from "@/lib/utils";
import { useT } from "@/i18n";
import {
  fetchUltraWikiStatus,
  hasActiveUltraWikiJobs,
} from "@/lib/ultrawikiApi";
import { AskPanel } from "@/components/ultrawiki/AskPanel";
import { OverviewTab } from "@/components/ultrawiki/overview/OverviewTab";
import { SourcesPanel } from "@/components/ultrawiki/SourcesPanel";
import { ContentsPanel } from "@/components/ultrawiki/ContentsPanel";
import { SlotsPanel } from "@/components/ultrawiki/SlotsPanel";
import { ImportProgress } from "@/components/ultrawiki/ImportProgress";
import { ExplorePanel } from "@/components/ultrawiki/ExplorePanel";
import { PeoplePanel } from "@/components/ultrawiki/PeoplePanel";
import { WordSearchPanel } from "@/components/ultrawiki/WordSearchPanel";

type UltraTab =
  | "overview"
  | "explore"
  | "people"
  | "ask"
  | "words"
  | "sources"
  | "contents"
  | "settings";

/** Tabs that own their scrolling: a workspace, not a document (see below). */
const SELF_SCROLLING: ReadonlySet<UltraTab> = new Set<UltraTab>([
  "explore",
  "people",
]);

export function UltraWikiPanel(): JSX.Element {
  const t = useT();
  const [tab, setTab] = useState<UltraTab>("overview");

  const statusQuery = useQuery({
    queryKey: ["ultrawiki", "status"],
    queryFn: fetchUltraWikiStatus,
    staleTime: 2_000,
    // While an import runs its item counter is what the source cards show
    // ticking, so the poll tightens to a couple of seconds; at rest it stays
    // calm (the status probe walks credentials in a worker thread).
    refetchInterval: (query) =>
      hasActiveUltraWikiJobs(query.state.data?.jobs) ? 2_000 : 30_000,
  });

  const status = statusQuery.data ?? null;
  const refetch = () => void statusQuery.refetch();

  if (statusQuery.isLoading) {
    return (
      <div
        className="flex flex-1 items-center justify-center p-6 text-sm text-muted-foreground"
        data-testid="ultrawiki-panel-loading"
      >
        {t("ultrawiki.panel.loading")}
      </div>
    );
  }

  if (!status) {
    return (
      <div className="flex flex-1 items-center justify-center p-6">
        <div
          role="alert"
          className="max-w-md rounded-md border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive"
          data-testid="ultrawiki-panel-unavailable"
        >
          {t("ultrawiki.panel.status_unavailable")}
        </div>
      </div>
    );
  }

  const embedding = status.slots.embedding;
  const embeddingDead = Boolean(embedding && !embedding.ready);

  return (
    <div
      className="flex flex-1 min-h-0 flex-col"
      data-testid="ultrawiki-panel"
    >
      {(status.degradations.length > 0 || embeddingDead) && (
        <div
          className="space-y-1 border-b border-border bg-[#ffb84d]/10 px-4 py-2 text-xs text-[#ffb84d]"
          data-testid="ultrawiki-degradations"
        >
          {embeddingDead && (
            <p className="flex flex-wrap items-center gap-1.5">
              <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden />
              {t("ultrawiki.panel.embedding_dead").replace(
                "{0}",
                embedding?.reason ?? "",
              )}
              <button
                type="button"
                onClick={() => setTab("settings")}
                className="underline underline-offset-2 hover:text-foreground"
                data-testid="ultrawiki-open-settings-link"
              >
                {t("ultrawiki.panel.open_settings")}
              </button>
            </p>
          )}
          {status.degradations.map((line) => (
            <p key={line} className="flex items-center gap-1.5">
              <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden />
              {line}
            </p>
          ))}
        </div>
      )}

      {tab !== "overview" && (
        <ImportProgress
          progress={status.progress ?? null}
          pipeline={status.pipeline}
          jobs={status.jobs}
          onChanged={refetch}
          onOpenSources={() => setTab("sources")}
        />
      )}

      <div className="scrollbar-jarvis flex items-stretch overflow-x-auto border-b border-border bg-card/40">
        <UltraTabButton
          active={tab === "overview"}
          onClick={() => setTab("overview")}
          icon={<Gauge className="h-3.5 w-3.5" aria-hidden />}
          label={t("ultrawiki.panel.tab_overview")}
          testId="ultrawiki-tab-overview"
        />
        <UltraTabButton
          active={tab === "explore"}
          onClick={() => setTab("explore")}
          icon={<Compass className="h-3.5 w-3.5" aria-hidden />}
          label={t("ultrawiki.explore.tab")}
          testId="ultrawiki-tab-explore"
        />
        <UltraTabButton
          active={tab === "people"}
          onClick={() => setTab("people")}
          icon={<UsersRound className="h-3.5 w-3.5" aria-hidden />}
          label={t("ultrawiki.people.tab")}
          testId="ultrawiki-tab-people"
        />
        <UltraTabButton
          active={tab === "ask"}
          onClick={() => setTab("ask")}
          icon={<MessageCircleQuestion className="h-3.5 w-3.5" aria-hidden />}
          label={t("ultrawiki.panel.tab_ask")}
          testId="ultrawiki-tab-ask"
        />
        <UltraTabButton
          active={tab === "words"}
          onClick={() => setTab("words")}
          icon={<Type className="h-3.5 w-3.5" aria-hidden />}
          label={t("ultrawiki.words.tab")}
          testId="ultrawiki-tab-words"
        />
        <UltraTabButton
          active={tab === "sources"}
          onClick={() => setTab("sources")}
          icon={<Plug className="h-3.5 w-3.5" aria-hidden />}
          label={t("ultrawiki.panel.tab_sources")}
          testId="ultrawiki-tab-sources"
        />
        <UltraTabButton
          active={tab === "contents"}
          onClick={() => setTab("contents")}
          icon={<Database className="h-3.5 w-3.5" aria-hidden />}
          label={t("ultrawiki.panel.tab_contents")}
          testId="ultrawiki-tab-contents"
        />
        <UltraTabButton
          active={tab === "settings"}
          onClick={() => setTab("settings")}
          icon={<Settings2 className="h-3.5 w-3.5" aria-hidden />}
          label={t("ultrawiki.panel.tab_settings")}
          testId="ultrawiki-tab-settings"
        />
      </div>

      {/* Explore and People manage their own scrolling: both are column
          workspaces whose panes fill the height they are given, and an outer
          scroll container would let that height grow without limit instead of
          pinning it to the window. */}
      <div
        className={cn(
          "min-h-0 flex-1",
          SELF_SCROLLING.has(tab)
            ? "overflow-hidden"
            : "scrollbar-jarvis overflow-y-auto",
        )}
      >
        {tab === "overview" && (
          <OverviewTab
            status={status}
            onChanged={refetch}
            onOpenSources={() => setTab("sources")}
            onOpenSettings={() => setTab("settings")}
          />
        )}
        {tab === "explore" && (
          <ExplorePanel
            onOpenSources={() => setTab("sources")}
            onOpenSettings={() => setTab("settings")}
          />
        )}
        {tab === "people" && (
          <PeoplePanel onOpenSources={() => setTab("sources")} />
        )}
        {tab === "ask" && (
          <AskPanel
            searchLegs={status.search_legs}
            slots={status.slots}
            ingestedItems={status.counts.total ?? 0}
            onOpenSources={() => setTab("sources")}
          />
        )}
        {tab === "words" && (
          <WordSearchPanel
            onOpenSources={() => setTab("sources")}
            onOpenSettings={() => setTab("settings")}
          />
        )}
        {tab === "sources" && (
          <SourcesPanel sources={status.sources} onChanged={refetch} />
        )}
        {tab === "contents" && (
          <ContentsPanel
            status={status}
            onOpenSources={() => setTab("sources")}
          />
        )}
        {tab === "settings" && (
          <SlotsPanel status={status} onChanged={refetch} />
        )}
      </div>
    </div>
  );
}

function UltraTabButton({
  active,
  onClick,
  icon,
  label,
  testId,
}: {
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  label: string;
  testId: string;
}): JSX.Element {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex shrink-0 items-center gap-1.5 whitespace-nowrap border-b-2 px-4 py-2.5 text-xs transition-colors",
        active
          ? "border-primary text-foreground"
          : "border-transparent text-muted-foreground hover:text-foreground",
      )}
      data-active={active ? "true" : "false"}
      data-testid={testId}
    >
      {icon}
      {label}
    </button>
  );
}
