import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

import { AssistantProfilePanel, chatFacts } from "@/components/home/AssistantProfilePanel";
import { timeStamps } from "@/components/agentchat/AgentTimeline";
import type { TimelineItem } from "@/components/agentchat/reduce";
import { useEventStore } from "@/store/events";

vi.mock("@/components/MascotGigi", () => ({ MascotGigi: () => <div data-testid="mascot" /> }));

const MIN = 60_000;

function user(id: string, tsMs: number, origin?: "control"): TimelineItem {
  return { type: "user", id, text: id, attachments: [], tsMs, ...(origin ? { origin } : {}) };
}

function turn(id: string, startedMs: number, tools: number): TimelineItem {
  return {
    type: "turn", id, provider: "p", model: "m", effort: "", runner: "api", status: "done",
    blocks: [
      { kind: "text", id: `${id}-t`, text: "hi" },
      ...Array.from({ length: tools }, (_, i) => ({
        kind: "tool" as const, callId: `${id}-${i}`, name: "x", input: {}, output: null, isError: false,
      })),
    ],
    startedMs, durationMs: 1, usage: null, liveUsage: null, costUsd: null, error: null,
  } as TimelineItem;
}

describe("timeStamps", () => {
  it("stamps the first message and every one after a 20-minute pause", () => {
    const t0 = new Date(2026, 9, 1, 9, 0).getTime();
    const items = [user("a", t0), turn("b", t0 + MIN, 0), user("c", t0 + 5 * MIN), user("d", t0 + 40 * MIN)];
    const stamps = timeStamps(items, new Date(2026, 9, 1, 12, 0));
    expect([...stamps.keys()]).toEqual(["a", "d"]);
  });

  it("leads with the date when the message is not from today", () => {
    const t0 = new Date(2026, 8, 24, 8, 13).getTime();
    const label = timeStamps([user("a", t0)], new Date(2026, 9, 1)).get("a") ?? "";
    expect(label).toContain(",");
  });
});

describe("chatFacts", () => {
  it("counts what the person wrote, the answers and the tool calls", () => {
    const facts = chatFacts([user("a", 10), turn("b", 20, 3), user("c", 30, "control"), turn("d", 40, 1)]);
    expect(facts).toEqual({ messages: 1, answers: 2, tools: 4, startedMs: 10 });
  });
});

describe("AssistantProfilePanel", () => {
  beforeEach(() => {
    useEventStore.setState({ assistantName: "George", connected: true, voiceState: "idle" });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (String(url).startsWith("/api/settings/agent-instructions")) {
          return new Response(
            JSON.stringify({ content: "x", exists: true, filename: "George.md", template: "", char_count: 1200 }),
            { status: 200 },
          );
        }
        if (String(url).startsWith("/api/wiki/health")) {
          return new Response(JSON.stringify({ ok: true, health: { vault_pages: 42, last_write: null } }), { status: 200 });
        }
        return new Response("{}", { status: 404 });
      }),
    );
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("shows the name, the soul file and the wiki page count from real reads", async () => {
    render(<AssistantProfilePanel onClose={() => {}} />);
    expect(screen.getByTestId("assistant-panel-name").textContent).toBe("George");
    await waitFor(() => expect(screen.getByTestId("assistant-tile-memory").textContent).toContain("42"));
    const soul = screen.getByTestId("assistant-tile-soul").textContent ?? "";
    expect(soul).toContain("George.md");
    expect(soul).toMatch(/1[.,]?200/);
  });

  it("never shows an invented number while the reads are in flight", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));
    render(<AssistantProfilePanel onClose={() => {}} />);
    expect(screen.getByTestId("assistant-tile-memory").textContent).not.toMatch(/\d/);
  });
});
