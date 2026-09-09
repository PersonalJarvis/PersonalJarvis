import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { AgentTimeline } from "./AgentTimeline";
import { EMPTY_TIMELINE, reduceEvents } from "./reduce";
import type { AgentChatEvent } from "@/lib/agentChatApi";
let seq = 0;
function ev(kind: string, payload: Record<string, unknown>, tsMs = 1000): AgentChatEvent {
 return { seq: ++seq, ts_ms: tsMs, kind, payload } as AgentChatEvent;
}
function draw(events: AgentChatEvent[]) {
 return render(<AgentTimeline items={reduceEvents(EMPTY_TIMELINE, events).items} assistantName="Jarvis" providerLabel={id => id} onDecide={() => undefined} />);
}
afterEach(() => { cleanup(); seq = 0; });
it("replays persisted events through the new shared trace", () => {
 draw([ev("turn_started", { turn_id: "t1" }), ev("tool_call", { turn_id: "t1", call_id: "c1", name: "list_dir", input: { path: "src" } }), ev("tool_result", { turn_id: "t1", call_id: "c1", output: "index.ts", duration_ms: 800 }), ev("turn_finished", { turn_id: "t1", status: "done" })]);
 expect(screen.getByTestId("work-trace")).toBeTruthy();
 expect(screen.getByRole("button", { name: /List files/ })).toBeTruthy();
});
describe("the person's turn with files", () => {
  const SHOT = {
    name: "shot.png",
    kind: "image",
    described_by: "none",
    url: "/api/agentic-ide/workspaces/ide_1/file?path=.jarvis%2Fdrops%2Fshot.png",
  };

  it("draws an image that can be fetched as the picture itself", () => {
    // The complaint (maintainer, 2026-08-27): a screenshot dropped on a pane
    // showed up in the person's turn as "### shot.png - '.jarvis/drops/…'".
    // With a url, the turn shows the person's sentence and the picture.
    draw([ev("user_message", { text: "## Task\nlook", typed: "look at this", attachments: [SHOT] })]);
    const turn = screen.getByTestId("agent-message-user");
    expect(turn.textContent).toContain("look at this");
    expect(turn.textContent).not.toContain("## Task");
    const image = within(turn).getByTestId("agent-message-image") as HTMLImageElement;
    expect(image.getAttribute("src")).toBe(SHOT.url);
    expect(image.getAttribute("alt")).toBe("shot.png");
  });

  it("keeps the chip for a file with nothing to draw", () => {
    draw([
      ev("user_message", {
        text: "read these",
        attachments: [
          { name: "notes.md", kind: "text", described_by: "extraction" },
          { name: "front-page.png", kind: "image", described_by: "vision" },
        ],
      }),
    ]);
    const turn = screen.getByTestId("agent-message-user");
    expect(within(turn).queryByTestId("agent-message-image")).toBeNull();
    expect(turn.textContent).toContain("notes.md");
    expect(turn.textContent).toContain("front-page.png");
    // Fill bubble, not inverted cream (`bg-foreground/70` on near-black).
    expect(turn.querySelector(".jarvis-user-bubble")).not.toBeNull();
  });
});
