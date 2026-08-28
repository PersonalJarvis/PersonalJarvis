import { Component, type ErrorInfo, type ReactNode } from "react";
import { AlertTriangle, RefreshCw, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { translate } from "@/i18n";
import { cn } from "@/lib/utils";
import { isChunkLoadError } from "@/lib/preloadRecovery";
import { browserSafeReloadDeps, reloadWhenServable } from "@/lib/safeReload";
import { useEventStore } from "@/store/events";

interface ViewErrorBoundaryProps {
  children: ReactNode;
  viewName: string;
  resetKey: string;
  onRecover: () => void;
}

interface ViewErrorBoundaryState {
  hasError: boolean;
  message: string;
  /**
   * The crash was a lazy chunk that is no longer on disk, not a bug.
   *
   * A rebuild replaces every hashed chunk, so a window that was already open
   * asks for files that have been deleted under it. Nothing is broken and
   * nothing is worth reporting — the window simply has to fetch the new build.
   */
  stale: boolean;
  resetKey: string;
}

export class ViewErrorBoundary extends Component<
  ViewErrorBoundaryProps,
  ViewErrorBoundaryState
> {
  state: ViewErrorBoundaryState = {
    hasError: false,
    message: "",
    stale: false,
    resetKey: this.props.resetKey,
  };

  static getDerivedStateFromError(error: unknown): Partial<ViewErrorBoundaryState> {
    return {
      hasError: true,
      message: error instanceof Error ? error.message : String(error),
      stale: isChunkLoadError(error),
    };
  }

  static getDerivedStateFromProps(
    props: ViewErrorBoundaryProps,
    state: ViewErrorBoundaryState,
  ): Partial<ViewErrorBoundaryState> | null {
    if (props.resetKey !== state.resetKey) {
      return {
        hasError: false,
        message: "",
        stale: false,
        resetKey: props.resetKey,
      };
    }
    return null;
  }

  componentDidCatch(error: unknown, info: ErrorInfo) {
    if (isChunkLoadError(error)) {
      // Expected during a rebuild, and the recovery is already under way.
      // Logging it as a crash is what makes a routine rebuild look like a bug
      // in whichever section the user happened to open.
      console.info("Jarvis view is running an old bundle", {
        view: this.props.viewName,
        error,
      });
      return;
    }
    console.error("Jarvis view crashed", {
      view: this.props.viewName,
      error,
      componentStack: info.componentStack,
    });
  }

  private recover = () => {
    this.setState({
      hasError: false,
      message: "",
      stale: false,
      resetKey: this.props.resetKey,
    });
    this.props.onRecover();
  };

  /**
   * Take the new build, once it is whole.
   *
   * The preload recovery spends one automatic reload per incident; a second
   * failure inside its settle window lands here instead, which is why this
   * button exists at all. It goes through `reloadWhenServable` for the same
   * reason every other reload in this app does — reloading into a half-written
   * `dist/` leaves a window with no JavaScript left to try again.
   */
  private reloadBundle = () => {
    reloadWhenServable(browserSafeReloadDeps());
  };

  render() {
    if (!this.state.hasError) return this.props.children;

    // A stale chunk is a rebuild, not a fault: no destructive colour, no raw
    // engine message, and an action that fixes the actual situation.
    const stale = this.state.stale;

    return (
      <div className="flex h-full min-h-0 flex-col bg-background">
        <div className="flex flex-1 items-center justify-center p-6">
          <div
            className={cn(
              "w-full max-w-xl rounded-lg border bg-card/80 p-5",
              stale ? "border-border" : "border-destructive/30",
            )}
          >
            <div className="flex items-start gap-3">
              <div
                className={cn(
                  "rounded-md p-2",
                  stale
                    ? "bg-muted text-muted-foreground"
                    : "bg-destructive/10 text-destructive",
                )}
              >
                {stale ? (
                  <RefreshCw className="h-5 w-5" />
                ) : (
                  <AlertTriangle className="h-5 w-5" />
                )}
              </div>
              <div className="min-w-0 flex-1">
                <h2 className="font-display text-base font-semibold">
                  {translate(
                    stale
                      ? "view_error_boundary.stale_title"
                      : "view_error_boundary.title",
                  )}
                </h2>
                {stale ? (
                  <p className="mt-1 text-sm text-muted-foreground">
                    {translate("view_error_boundary.stale_hint")}
                  </p>
                ) : (
                  <p className="mt-1 text-sm text-muted-foreground">
                    {this.props.viewName} {translate("view_error_boundary.crashed_prefix")}{" "}
                    {useEventStore.getState().assistantName}{" "}
                    {translate("view_error_boundary.crashed_suffix")}
                  </p>
                )}
                {!stale && this.state.message && (
                  <pre className="mt-3 max-h-32 overflow-auto rounded-md border border-border bg-background/80 p-3 text-xs text-muted-foreground">
                    {this.state.message}
                  </pre>
                )}
                {stale ? (
                  <Button className="mt-4" size="sm" onClick={this.reloadBundle}>
                    <RefreshCw className="h-3.5 w-3.5" />
                    <span className="ml-1.5">
                      {translate("view_error_boundary.stale_action")}
                    </span>
                  </Button>
                ) : (
                  <Button className="mt-4" size="sm" onClick={this.recover}>
                    <RotateCcw className="h-3.5 w-3.5" />
                    <span className="ml-1.5">
                      {translate("view_error_boundary.back_to_chats")}
                    </span>
                  </Button>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
    );
  }
}
