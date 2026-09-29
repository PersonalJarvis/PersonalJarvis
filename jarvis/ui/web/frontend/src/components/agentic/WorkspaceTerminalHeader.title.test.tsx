import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { TerminalRecap } from "@/lib/agenticIdeApi";
import { useEventStore } from "@/store/events";
import { usePaneRecapsStore } from "@/store/paneRecaps";
import { useWorkspacePanesStore } from "@/store/workspacePanes";
import { WorkspaceTerminalHeader } from "./WorkspaceTerminalHeader";

const BASE = { name: "T3", workspaceId: "w1", agent: "codex", displayName: "Codex", appearance: "dark" as const, status: "live" as const };

beforeEach(() => {
  // Off the IDE section, so the shared recap poll never fetches in a test.
  useEventStore.setState({ activeSection: "chats" });
  usePaneRecapsStore.setState({ workspaceId: null, byName: {} });
  useWorkspacePanesStore.setState({ panes: [] });
});
afterEach(cleanup);

describe("pane header title", () => {
  it("shows the pane's short goal instead of its call-sign", () => {
    usePaneRecapsStore.setState({
      workspaceId: "w1",
      byName: { T3: { recap: "Release pipeline — green CI", source: "model" } as TerminalRecap },
    });
    render(<WorkspaceTerminalHeader {...BASE} />);
    const title = screen.getByTestId("pane-title-T3");
    expect(title.textContent).toBe("Release pipeline — green CI");
    expect(title.getAttribute("title")).toContain("T3");
  });

  it("keeps the call-sign while nothing names the work yet", () => {
    render(<WorkspaceTerminalHeader {...BASE} />);
    expect(screen.getByTestId("pane-title-T3").textContent).toBe("T3");
  });
});
