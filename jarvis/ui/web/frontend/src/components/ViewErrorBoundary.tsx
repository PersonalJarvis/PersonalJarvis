import { Component, type ErrorInfo, type ReactNode } from "react";
import { AlertTriangle, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";

interface ViewErrorBoundaryProps {
  children: ReactNode;
  viewName: string;
  resetKey: string;
  onRecover: () => void;
}

interface ViewErrorBoundaryState {
  hasError: boolean;
  message: string;
  resetKey: string;
}

export class ViewErrorBoundary extends Component<
  ViewErrorBoundaryProps,
  ViewErrorBoundaryState
> {
  state: ViewErrorBoundaryState = {
    hasError: false,
    message: "",
    resetKey: this.props.resetKey,
  };

  static getDerivedStateFromError(error: unknown): Partial<ViewErrorBoundaryState> {
    return {
      hasError: true,
      message: error instanceof Error ? error.message : String(error),
    };
  }

  static getDerivedStateFromProps(
    props: ViewErrorBoundaryProps,
    state: ViewErrorBoundaryState,
  ): Partial<ViewErrorBoundaryState> | null {
    if (props.resetKey !== state.resetKey) {
      return { hasError: false, message: "", resetKey: props.resetKey };
    }
    return null;
  }

  componentDidCatch(error: unknown, info: ErrorInfo) {
    console.error("Jarvis view crashed", {
      view: this.props.viewName,
      error,
      componentStack: info.componentStack,
    });
  }

  private recover = () => {
    this.setState({ hasError: false, message: "", resetKey: this.props.resetKey });
    this.props.onRecover();
  };

  render() {
    if (!this.state.hasError) return this.props.children;

    return (
      <div className="flex h-full min-h-0 flex-col bg-background">
        <div className="flex flex-1 items-center justify-center p-6">
          <div className="w-full max-w-xl rounded-lg border border-destructive/30 bg-card/80 p-5 shadow-xl">
            <div className="flex items-start gap-3">
              <div className="rounded-md bg-destructive/10 p-2 text-destructive">
                <AlertTriangle className="h-5 w-5" />
              </div>
              <div className="min-w-0 flex-1">
                <h2 className="font-display text-base font-semibold">
                  Ansicht konnte nicht geladen werden
                </h2>
                <p className="mt-1 text-sm text-muted-foreground">
                  {this.props.viewName} ist abgestuerzt. Jarvis bleibt bedienbar.
                </p>
                {this.state.message && (
                  <pre className="mt-3 max-h-32 overflow-auto rounded-md border border-border bg-background/80 p-3 text-xs text-muted-foreground">
                    {this.state.message}
                  </pre>
                )}
                <Button className="mt-4" size="sm" onClick={this.recover}>
                  <RotateCcw className="h-3.5 w-3.5" />
                  <span className="ml-1.5">Zurueck zu Chats</span>
                </Button>
              </div>
            </div>
          </div>
        </div>
      </div>
    );
  }
}
