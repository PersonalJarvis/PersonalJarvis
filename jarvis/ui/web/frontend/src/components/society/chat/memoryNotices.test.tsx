import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { WorkTrace } from "@/components/agentchat/WorkTrace";
import type { NoticeItem, TimelineItem, TurnBlock, TurnItem } from "@/components/agentchat/reduce";
import { foldMemoryNotices } from "./memoryNotices";

const turn = (id: string): TurnItem => ({ type: "turn", id, blocks: [], status: "done", startedMs: 1, durationMs: 1 } as unknown as TurnItem);
const notice = (id: string, kind = "memory_updated"): NoticeItem => ({
  type: "notice", id, kind, text: "Memory updated", agentName: "Scout", agentId: "scout", status: "done", tsMs: 2, resolved: "", data: {},
});
const user = (id: string): TimelineItem => ({ type: "user", id, text: "hi", attachments: [], tsMs: 1 } as unknown as TimelineItem);
afterEach(cleanup);

describe("memory receipts", () => {
  it("move into the nearest earlier turn and never stay below it", () => {
    const items: TimelineItem[] = [user("u1"), turn("t1"), notice("m1"), user("u2"), notice("m2"), turn("t2"), notice("m3"), notice("r", "society_result")];
    const folded = foldMemoryNotices(items);
    expect(folded.items.map((item) => item.id)).toEqual(["u1", "t1", "u2", "t2", "r"]);
    expect(folded.memoryByTurn.get("t1")?.map((n) => n.id)).toEqual(["m1", "m2"]);
    expect(folded.memoryByTurn.get("t2")?.map((n) => n.id)).toEqual(["m3"]);
  });

  it("keeps a receipt with no earlier turn where it is", () => {
    const items: TimelineItem[] = [notice("m0"), user("u1")];
    expect(foldMemoryNotices(items).items).toEqual(items);
    expect(foldMemoryNotices([notice("m0"), turn("t1")]).items.map((item) => item.id)).toEqual(["m0", "t1"]);
  });

  it("draws trace extras above the reply, finished or still streaming", () => {
    const thought: TurnBlock = { kind: "reasoning", id: "think", text: "Plan.", live: false, durationMs: 1000, startedMs: 1 };
    const answer: TurnBlock = { kind: "text", id: "answer", text: "Final reply." };
    const extras = [{ key: "m1", node: <span>MEMORY-STEP</span> }];
    const order = () => {
      const html = document.body.innerHTML;
      return [html.indexOf("Plan."), html.indexOf("MEMORY-STEP"), html.indexOf("Final reply.")];
    };
    const { rerender } = render(<WorkTrace conversation blocks={[thought, answer]} status="done" startedMs={1} durationMs={2000} extras={extras} />);
    expect(screen.getByText("MEMORY-STEP")).toBeTruthy();
    let [plan, memory, reply] = order();
    expect(memory).toBeGreaterThan(-1);
    expect(memory).toBeLessThan(reply);
    rerender(<WorkTrace conversation blocks={[{ ...thought, live: true }, answer]} status="running" startedMs={1} durationMs={null} extras={extras} />);
    [plan, memory, reply] = order();
    expect(memory).toBeGreaterThan(-1);
    expect(memory).toBeLessThan(reply);
    expect(plan).toBeLessThan(memory);
  });
});
