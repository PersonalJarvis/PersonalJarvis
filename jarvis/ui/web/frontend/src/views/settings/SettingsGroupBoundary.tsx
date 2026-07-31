import { Component, type ErrorInfo, type ReactNode } from "react";
import { AlertTriangle, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { translate } from "@/i18n";

interface SettingsGroupBoundaryProps {
  children: ReactNode;
  /** Group name for the console log — never shown to the user. */
  group: string;
}

interface SettingsGroupBoundaryState {
  hasError: boolean;
  message: string;
  /** Bumped by "Try again" to remount the subtree with fresh state. */
  attempt: number;
}

/**
 * Fault isolation for ONE Settings group.
 *
 * Settings is a stack of independent panels, each backed by its own route. The
 * view-level boundary treats them as one unit, so a single panel throwing
 * replaced the ENTIRE page with an error card — the user could not change any
 * setting, including the ones that were working (reported 2026-07-28: a
 * keybind row hit an undefined combo from a backend that predated the action,
 * and the whole Settings view died with "Cannot read properties of undefined").
 *
 * Frontend and backend are updated separately by design, so a payload the UI
 * does not expect is a NORMAL state, not an edge case. Wrapping each group
 * keeps that blast radius at one panel: the broken one shows an honest card
 * with a retry, every other setting stays editable.
 */
export class SettingsGroupBoundary extends Component<
  SettingsGroupBoundaryProps,
  SettingsGroupBoundaryState
> {
  state: SettingsGroupBoundaryState = {
    hasError: false,
    message: "",
    attempt: 0,
  };

  static getDerivedStateFromError(
    error: unknown,
  ): Partial<SettingsGroupBoundaryState> {
    return {
      hasError: true,
      message: error instanceof Error ? error.message : String(error),
    };
  }

  componentDidCatch(error: unknown, info: ErrorInfo) {
    console.error("Jarvis settings group crashed", {
      group: this.props.group,
      error,
      componentStack: info.componentStack,
    });
  }

  private retry = () => {
    this.setState((s) => ({
      hasError: false,
      message: "",
      attempt: s.attempt + 1,
    }));
  };

  render() {
    if (!this.state.hasError) {
      // The attempt counter is the key: a retry must remount the subtree so the
      // failed hook state is rebuilt instead of re-throwing from the old one.
      return <div key={this.state.attempt}>{this.props.children}</div>;
    }

    return (
      <div className="mt-2 rounded-lg border border-destructive/30 bg-card/60 p-4">
        <div className="flex items-start gap-3">
          <div className="rounded-md bg-destructive/10 p-2 text-destructive">
            <AlertTriangle className="h-4 w-4" />
          </div>
          <div className="min-w-0 flex-1">
            <h4 className="font-display text-sm font-semibold">
              {translate("view_error_boundary.group_title")}
            </h4>
            <p className="mt-1 text-xs text-muted-foreground">
              {translate("view_error_boundary.group_hint")}
            </p>
            {this.state.message && (
              <pre className="mt-2 max-h-24 overflow-auto rounded-md border border-border bg-background/80 p-2 text-[11px] text-muted-foreground">
                {this.state.message}
              </pre>
            )}
            <Button className="mt-3" size="sm" variant="outline" onClick={this.retry}>
              <RotateCcw className="h-3.5 w-3.5" />
              <span className="ml-1.5">
                {translate("view_error_boundary.group_retry")}
              </span>
            </Button>
          </div>
        </div>
      </div>
    );
  }
}
