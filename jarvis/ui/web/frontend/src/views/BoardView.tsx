import { useState } from "react";
import { Share2 } from "lucide-react";

import { PageHeader } from "@/components/layout/PageHeader";
import { TabBar } from "@/components/layout/SectionTabBar";
import { BoardIcon } from "@/components/icons/sectionIcons";
import { ShareDialog } from "@/components/board/ShareDialog";
import { UsageTab } from "@/components/board/insights/UsageTab";
import { AgentsTab } from "@/components/board/insights/AgentsTab";
import { RhythmTab } from "@/components/board/insights/RhythmTab";
import { InsightCard, SkeletonBlock } from "@/components/board/insights/primitives";
import { useBoardInsights } from "@/hooks/useBoardInsights";
import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";

type Tab = "usage" | "agents" | "rhythm";

const TAB_STORAGE_KEY = "jarvis.board.tab";

function readStoredTab(): Tab {
  try {
    const value = window.localStorage.getItem(TAB_STORAGE_KEY);
    if (value === "usage" || value === "agents" || value === "rhythm") return value;
  } catch {
    // Storage blocked (private window, previews): the default tab is fine.
  }
  return "usage";
}

/**
 * The Board: how someone works with Jarvis, from the stores the app already
 * keeps on this computer — dictation, voice sessions, chats and the coding
 * agents. Three tabs, each sized to read at a glance.
 */
export function BoardView() {
  const t = useT();
  const insights = useBoardInsights();
  const [tab, setTabState] = useState<Tab>(readStoredTab);
  const [shareOpen, setShareOpen] = useState(false);
  const data = insights.data;

  const setTab = (next: Tab) => {
    setTabState(next);
    try {
      window.localStorage.setItem(TAB_STORAGE_KEY, next);
    } catch {
      // Remembering the tab is a convenience; losing it changes nothing else.
    }
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="mx-auto w-full max-w-[1280px] px-6 lg:px-8">
        <PageHeader
          icon={<BoardIcon />}
          title={t("board_view.title")}
          description={t("board_insights.description")}
          actions={
            <Button
              variant="outline"
              size="sm"
              onClick={() => setShareOpen(true)}
              disabled={!data}
              data-testid="board-share-button"
            >
              <Share2 className="h-4 w-4" />
              {t("board_view.share.button")}
            </Button>
          }
          tabs={
            <TabBar
              tabs={[
                { id: "usage", label: t("board_insights.tabs.usage") },
                { id: "agents", label: t("board_insights.tabs.agents") },
                { id: "rhythm", label: t("board_insights.tabs.rhythm") },
              ]}
              active={tab}
              onChange={(id) => setTab(id as Tab)}
            />
          }
        />
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto scrollbar-jarvis">
        <div className="mx-auto w-full max-w-[1280px] px-6 py-6 lg:px-8">
          {insights.error && !data ? (
            <InsightCard>
              <p className="text-sm text-muted-foreground">{t("board_view.load_error")}</p>
            </InsightCard>
          ) : !data ? (
            <BoardSkeleton />
          ) : tab === "usage" ? (
            <UsageTab data={data} />
          ) : tab === "agents" ? (
            <AgentsTab data={data} />
          ) : (
            <RhythmTab data={data} />
          )}
        </div>
      </div>

      {data && (
        <ShareDialog
          open={shareOpen}
          onOpenChange={setShareOpen}
          stats={{
            userWords: data.dictation.words + data.voice.user_words,
            jarvisWords: data.voice.jarvis_words,
            conversationHours: data.voice.seconds / 3600,
            sessionCount: data.voice.sessions,
            longestStreak: data.streak.longest_days,
          }}
        />
      )}
    </div>
  );
}

function BoardSkeleton() {
  return (
    <div className="flex flex-col gap-4" data-testid="board-skeleton">
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        {[0, 1, 2].map((i) => (
          <InsightCard key={i} className="h-56">
            <SkeletonBlock className="h-7 w-24" />
            <SkeletonBlock className="mt-3 h-3 w-36" />
            <SkeletonBlock className="mt-auto h-20 w-full" />
          </InsightCard>
        ))}
      </div>
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
        {[0, 1].map((i) => (
          <InsightCard key={i} className="h-72">
            <SkeletonBlock className="h-6 w-40" />
            <SkeletonBlock className="mt-auto h-44 w-full" />
          </InsightCard>
        ))}
      </div>
    </div>
  );
}
