import { lazy, Suspense } from "react";
import { ViewErrorBoundary } from "@/components/ViewErrorBoundary";
import { useT } from "@/i18n";
import { useEventStore } from "@/store/events";

const IdeProjectTree = lazy(() => import("./IdeProjectTree").then((module) => ({ default: module.IdeProjectTree })));

/** The normal chat sidebar needs no project/file operations or workspace UI. */
export function LazyIdeProjectTree() {
  const t = useT();
  const setActive = useEventStore((state) => state.setActiveSection);
  return <ViewErrorBoundary viewName={t("nav.agentic_ide")} resetKey="ide-project-tree" onRecover={() => setActive("chats")}>
    <Suspense fallback={<p role="status" aria-busy="true" className="px-3 py-4 text-sm text-muted-foreground">{t("common.loading")}</p>}>
      <IdeProjectTree />
    </Suspense>
  </ViewErrorBoundary>;
}
