import { Component, Suspense, lazy, type ErrorInfo, type ReactNode } from "react";
import { AlertTriangle, RotateCcw } from "lucide-react";
import { translate, useLocaleChunk } from "@/i18n";
import { useIdeSidePanelStore } from "@/store/ideSidePanel";

// The office is a WebGL scene with its own chunk: nothing of it loads until
// someone opens this tab.
const OfficeStage = lazy(() => import("@/components/society/office/OfficeStage").then((m) => ({ default: m.OfficeStage })));

/** Keeps a failed scene inside the tab instead of taking the IDE down with it. */
class OfficeTabBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true };
  }

  componentDidCatch(error: unknown, info: ErrorInfo) {
    console.error("IDE office tab crashed", { error, componentStack: info.componentStack });
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <div data-testid="ide-office-tab-failed" className="flex h-full min-h-0 flex-col items-center justify-center gap-3 p-6 text-center">
        <AlertTriangle className="h-5 w-5 text-destructive" aria-hidden />
        <p className="text-sm text-muted-foreground">{translate("ide_side_panel.office.failed")}</p>
        <button
          type="button"
          onClick={() => this.setState({ failed: false })}
          className="inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1 text-sm text-foreground hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <RotateCcw className="h-3.5 w-3.5" aria-hidden />
          {translate("ide_side_panel.office.retry")}
        </button>
      </div>
    );
  }
}

/**
 * The coding floor of the agent office, inside the IDE's side panel.
 *
 * Every running coding-agent pane walks around here; a click on a monitor
 * focuses that pane in the grid next to it. The office's "open the ledger"
 * action has no ledger in the IDE, so it brings the Agents tab forward.
 */
export function OfficeTab() {
  const ready = useLocaleChunk("society");
  const openTab = useIdeSidePanelStore((state) => state.openTab);
  return (
    <div data-testid="ide-office-tab" className="h-full min-h-0 w-full">
      <OfficeTabBoundary>
        {ready && (
          <Suspense fallback={<div className="h-full w-full animate-pulse bg-secondary" aria-hidden />}>
            <OfficeStage initialFloor="coding" compact onOpenLedger={() => openTab("agents")} />
          </Suspense>
        )}
      </OfficeTabBoundary>
    </div>
  );
}
