import { lazy, Suspense } from "react";
import { ViewErrorBoundary } from "@/components/ViewErrorBoundary";
import { useT } from "@/i18n";
import { useEventStore } from "@/store/events";
import { useIdeThreadsStore } from "@/store/ideThreads";

const IdeProjectTree = lazy(() => import("./IdeProjectTree").then((module) => ({ default: module.IdeProjectTree })));
const ThreadTree = lazy(() => import("./threads/ThreadTree").then((module) => ({ default: module.ThreadTree })));

/** The normal chat sidebar needs no project/file operations or workspace UI. */
export function LazyIdeProjectTree() {
  const t = useT();
  const setActive = useEventStore((state) => state.setActiveSection);
  // The thread layout lists threads instead of workspaces.
  const layout = useIdeThreadsStore((state) => state.layout);
  return <ViewErrorBoundary viewName={t("nav.agentic_ide")} resetKey="ide-project-tree" onRecover={() => setActive("chats")}>
    <Suspense fallback={<p role="status" aria-busy="true" className="px-3 py-4 text-sm text-muted-foreground">{t("common.loading")}</p>}>
      {layout === "threads" ? <ThreadTree /> : <IdeProjectTree />}
    </Suspense>
  </ViewErrorBoundary>;
}
