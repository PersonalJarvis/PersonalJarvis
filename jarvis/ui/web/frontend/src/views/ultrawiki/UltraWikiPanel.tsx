/**
 * Ultra-mode body of the Wiki section (design doc 04) — rendered by WikiView
 * INSTEAD of the normal wiki workspace while UltraWiki mode is on (D-5).
 *
 * Lazy-loaded from WikiView (the WikiGraph pattern) so normal-mode users
 * never pay for this chunk. Owns the Ultra status poll: every 30 s at rest,
 * every 4 s while a sync job is active (the wiki health polling idiom).
 *
 * Layout: honest degradation banner → staged import-progress strip → the
 * five tabs Check | Ask | Sources | Contents | Settings. A dead required slot
 * banners the problem and links to the settings tab — the mode is never
 * flipped silently (design doc 04, mode-switch rules).
 *
 * "Check" leads on purpose. Every other tab answers a question you already
 * know to ask; a user whose knowledge base looks empty does not know which
 * one of them holds the reason, and hunting through four screens is how a
 * single missing import step went undiagnosed for days.
 */
import { useState } from "react";
import {
  AlertTriangle,
  Compass,
  Database,
  ListChecks,
  MessageCircleQuestion,
  Plug,
  Settings2,
} from "lucide-react";
import { useQuery } from "@tanstack/react-query";

import { cn } from "@/lib/utils";
import { useT } from "@/i18n";
import {
  fetchUltraWikiStatus,
  hasActiveUltraWikiJobs,
} from "@/lib/ultrawikiApi";
import { AskPanel } from "@/components/ultrawiki/AskPanel";
import { HealthPanel } from "@/components/ultrawiki/HealthPanel";
import { SourcesPanel } from "@/components/ultrawiki/SourcesPanel";
import { ContentsPanel } from "@/components/ultrawiki/ContentsPanel";
import { SlotsPanel } from "@/components/ultrawiki/SlotsPanel";
import { ImportProgress } from "@/components/ultrawiki/ImportProgress";
import { ExplorePanel } from "@/components/ultrawiki/ExplorePanel";

type UltraTab =
  | "check"
  | "explore"
  | "ask"
  | "sources"
  | "contents"
  | "settings";

export function UltraWikiPanel(): JSX.Element {
  const t = useT();
  const [tab, setTab] = useState<UltraTab>("ask");

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

      <ImportProgress
        counts={status.counts}
        pipeline={status.pipeline}
        jobs={status.jobs}
        onChanged={refetch}
        onOpenSources={() => setTab("sources")}
      />

      <div className="flex items-stretch border-b border-border bg-card/40">
        <UltraTabButton
          active={tab === "check"}
          onClick={() => setTab("check")}
          icon={<ListChecks className="h-3.5 w-3.5" aria-hidden />}
          label={t("ultrawiki.panel.tab_check")}
          testId="ultrawiki-tab-check"
        />
        <UltraTabButton
          active={tab === "explore"}
          onClick={() => setTab("explore")}
          icon={<Compass className="h-3.5 w-3.5" aria-hidden />}
          label={t("ultrawiki.explore.tab")}
          testId="ultrawiki-tab-explore"
        />
        <UltraTabButton
          active={tab === "ask"}
          onClick={() => setTab("ask")}
          icon={<MessageCircleQuestion className="h-3.5 w-3.5" aria-hidden />}
          label={t("ultrawiki.panel.tab_ask")}
          testId="ultrawiki-tab-ask"
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

      <div className="min-h-0 flex-1 overflow-y-auto">
        {tab === "check" && (
          <HealthPanel
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
        {tab === "ask" && (
          <AskPanel
            searchLegs={status.search_legs}
            slots={status.slots}
            ingestedItems={status.counts.total ?? 0}
            onOpenSources={() => setTab("sources")}
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
        "flex items-center gap-1.5 border-b-2 px-4 py-2.5 text-xs transition-colors",
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
