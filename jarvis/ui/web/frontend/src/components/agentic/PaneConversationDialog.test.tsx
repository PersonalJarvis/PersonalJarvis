import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PaneConversationDialog } from "./PaneConversationDialog";
import type { ConversationResponse } from "@/lib/agenticIdeApi";

const CONVERSATION: ConversationResponse = {
  terminal: "T7",
  agent: "claude",
  readable: true,
  available: true,
  turns: [
    { role: "user", text: "fix the scrollbar please", steps: [] },
    {
      role: "assistant",
      text: "Rebuilding it now.",
      steps: [
        { tool: "Read", target: "PaneScrollRail.tsx", detail: "{}" },
        { tool: "Edit", target: "PaneScrollRail.tsx", detail: "{}" },
      ],
    },
  ],
};

function stubFetch(payload: unknown, ok = true): ReturnType<typeof vi.fn> {
  const stub = vi.fn().mockResolvedValue({
    ok,
    status: ok ? 200 : 500,
    json: () => Promise.resolve(payload),
  });
  vi.stubGlobal("fetch", stub);
  return stub;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("PaneConversationDialog", () => {
  it("renders the recorded turns with a scrollable history surface", async () => {
    const fetchStub = stubFetch(CONVERSATION);
    render(
      <PaneConversationDialog terminal="T7" open onOpenChange={() => {}} />,
    );

    await waitFor(() =>
      expect(screen.getByText("fix the scrollbar please")).toBeTruthy(),
    );
    expect(screen.getByText("Rebuilding it now.")).toBeTruthy();
    // The tool work is summarised, not dumped: count plus tool names.
    expect(screen.getByText(/Read, Edit/)).toBeTruthy();
    // The scroller is a real overflow container — ITS scrollbar is the honest
    // "where am I", which the live TUI pane can never give.
    const scroller = screen.getByTestId("pane-conversation-scroller");
    expect(scroller.className).toContain("overflow-y-auto");
    expect(fetchStub).toHaveBeenCalledWith(
      "/api/agentic-ide/terminals/T7/conversation",
      expect.anything(),
    );
  });

  it("says honestly when this CLI keeps no readable record", async () => {
    stubFetch({
      terminal: "T2",
      agent: "shell",
      readable: false,
      available: false,
      turns: [],
    });
    render(
      <PaneConversationDialog terminal="T2" open onOpenChange={() => {}} />,
    );

    await waitFor(() =>
      expect(screen.getByText("No history yet")).toBeTruthy(),
    );
  });

  it("loads nothing while closed", () => {
    const fetchStub = stubFetch(CONVERSATION);
    render(
      <PaneConversationDialog
        terminal="T7"
        open={false}
        onOpenChange={() => {}}
      />,
    );
    expect(fetchStub).not.toHaveBeenCalled();
  });
});
