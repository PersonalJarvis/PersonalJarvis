import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  AgentTimeline,
  arrangeThinking,
  thoughtGist,
  thoughtNeedsFold,
} from "@/components/agentchat/AgentTimeline";
import { EMPTY_TIMELINE, reduceEvents, type TurnBlock } from "@/components/agentchat/reduce";
import type { AgentChatEvent } from "@/lib/agentChatApi";

/**
 * The scratchpad — how a turn's thinking is shown.
 *
 * The complaint this file guards (maintainer, 2026-08-25): the model's
 * intermediate steps had stopped showing. Every stretch of thought was
 * merged into one folded "Thought for Ns" row at the top of the turn, and
 * the running one drew nothing at all, so the reasoning was neither
 * watchable while it happened nor readable where it happened.
 */

let seq = 0;
function ev(kind: string, payload: Record<string, unknown>, tsMs = 1_000): AgentChatEvent {
  seq += 1;
  return { seq, ts_ms: tsMs, kind, payload } as AgentChatEvent;
}

const TURN = { provider: "openai-codex", model: "gpt-5.4", effort: "high", runner: "codex-cli" };

function draw(events: AgentChatEvent[]) {
  const { items } = reduceEvents(EMPTY_TIMELINE, events);
  return render(
    <AgentTimeline
      items={items}
      assistantName="Jarvis"
      providerLabel={(id) => id}
      onDecide={() => undefined}
    />,
  );
}

const LONG =
  "First I need to find where the port is configured. The launcher reads it from the " +
  "instance config, so the dev instance and the live one cannot collide.\n\n" +
  "Then I will check whether anything else already listens there.";

afterEach(() => {
  cleanup();
  seq = 0;
});

describe("arrangeThinking", () => {
  const spoken = (id: string, text: string, live = false): TurnBlock => ({
    kind: "reasoning",
    id,
    text,
    durationMs: live ? null : 1000,
    live,
    startedMs: 0,
  });
  const silent = (id: string, durationMs: number, live = false): TurnBlock => ({
    kind: "reasoning",
    id,
    text: "",
    durationMs: live ? null : durationMs,
    live,
    startedMs: 0,
  });
  const tool: TurnBlock = {
    kind: "tool",
    callId: "c1",
    name: "Bash",
    input: {},
    output: "ok",
    isError: false,
    durationMs: 10,
    approval: null,
    startedMs: 0,
  };

  it("keeps a thought with words where it happened, live or finished", () => {
    const out = arrangeThinking([spoken("a", "Port first."), tool, spoken("b", "Now the log.", true)]);
    expect(out.map((b) => (b.kind === "reasoning" ? b.id : b.kind))).toEqual(["a", "tool", "b"]);
  });

  it("folds the wordless thoughts into one timing row and drops a running one", () => {
    const out = arrangeThinking([silent("a", 1000), tool, silent("b", 4000), silent("c", 0, true)]);
    expect(out).toHaveLength(2);
    const [row] = out;
    expect(row.kind).toBe("reasoning");
    if (row.kind === "reasoning") expect(row.durationMs).toBe(5000);
  });
});

describe("thought helpers", () => {
  it("folds only what a preview cannot hold", () => {
    expect(thoughtNeedsFold("Looking in the wiki for the holiday dates.")).toBe(false);
    expect(thoughtNeedsFold(LONG)).toBe(true);
    expect(thoughtNeedsFold("x".repeat(201))).toBe(true);
  });

  it("flattens Markdown into one readable line", () => {
    expect(thoughtGist("**Checking** the `config`\n\n- first\n- second")).toBe(
      "Checking the config - first - second",
    );
  });
});

describe("the scratchpad", () => {
  it("streams the thought as it is written, under the turn's one live line", () => {
    vi.useFakeTimers();
    try {
      const started = Date.now();
      draw([
        ev("user_message", { text: "look into it" }, started),
        ev("turn_started", { turn_id: "t1", ...TURN }, started),
        ev("reasoning_started", { turn_id: "t1", message_id: "r1" }, started),
        ev("usage_delta", { turn_id: "t1", usage: { input_tokens: 12, output_tokens: 300 } }, started),
        ev("reasoning_delta", { turn_id: "t1", text: "The port is read from " }, started),
        ev("reasoning_delta", { turn_id: "t1", text: "the instance config." }, started),
      ]);

      const pad = screen.getByTestId("agent-reasoning");
      expect(pad.getAttribute("data-state")).toBe("live");
      // The words are on screen while they arrive.
      expect(pad.textContent).toContain("The port is read from the instance config.");
      // …and the live line is the scratchpad's own header: the core, the
      // running time, the tokens so far — once, not once here and once below.
      const live = screen.getAllByTestId("agent-turn-live");
      expect(live).toHaveLength(1);
      expect(pad.contains(live[0])).toBe(true);
      expect(within(live[0]).getByTestId("live-core")).toBeTruthy();
      expect(live[0].textContent).toMatch(/300/);
      act(() => {
        vi.advanceTimersByTime(8_000);
      });
      expect(live[0].textContent).toMatch(/8s/);
    } finally {
      vi.useRealTimers();
    }
  });

  it("reads every stretch of thinking where it happened, open while the turn still works", () => {
    draw([
      ev("user_message", { text: "look into it" }),
      ev("turn_started", { turn_id: "t2", ...TURN }),
      ev("reasoning", { turn_id: "t2", text: LONG, duration_ms: 3000 }),
      ev("tool_call", { turn_id: "t2", call_id: "c1", name: "Bash", input: { command: "ss -ltnp" } }),
      ev("tool_result", { turn_id: "t2", call_id: "c1", output: "ok", is_error: false }),
      ev("reasoning", { turn_id: "t2", text: "It is listening.", duration_ms: 1000 }),
    ]);

    const rows = screen.getAllByTestId("agent-reasoning");
    expect(rows).toHaveLength(2);
    // Thought, command, thought — in that order, not one merged row on top.
    const order = Array.from(
      document.querySelectorAll("[data-testid='agent-reasoning'],[data-testid='agent-tool']"),
    ).map((el) => el.getAttribute("data-testid"));
    expect(order).toEqual(["agent-reasoning", "agent-tool", "agent-reasoning"]);

    // A long thought is open in full while the turn runs; the short one is
    // whole either way, with nothing left to unfold.
    expect(rows[0].getAttribute("data-state")).toBe("open");
    expect(rows[0].textContent).toContain("3s");
    expect(rows[0].textContent).toContain("already listens there");
    expect(rows[1].getAttribute("data-state")).toBe("whole");
    expect(rows[1].textContent).toContain("It is listening.");
    expect(within(rows[1]).getByRole("button").hasAttribute("disabled")).toBe(true);

    // The turn's own live line sits below, because no thought is streaming.
    expect(screen.getAllByTestId("agent-turn-live")).toHaveLength(1);
    expect(rows[0].contains(screen.getByTestId("agent-turn-live"))).toBe(false);
  });

  it("folds a long thought to a two-line preview once the turn is done, and opens on a click", () => {
    draw([
      ev("user_message", { text: "look into it" }),
      ev("turn_started", { turn_id: "t3", ...TURN }),
      ev("reasoning", { turn_id: "t3", text: LONG, duration_ms: 3000 }),
      ev("assistant_text", { turn_id: "t3", message_id: "m1", text: "Running." }),
      ev("turn_finished", { turn_id: "t3", status: "done", duration_ms: 4000, usage: {} }),
    ]);

    const pad = screen.getByTestId("agent-reasoning");
    expect(pad.getAttribute("data-state")).toBe("folded");
    // The preview still shows the thought's first words without a click.
    const preview = within(pad).getByTestId("agent-reasoning-preview");
    expect(preview.textContent).toContain("First I need to find where the port is configured.");
    expect(preview.className).toContain("line-clamp-2");

    fireEvent.click(within(pad).getByRole("button"));
    expect(pad.getAttribute("data-state")).toBe("open");
    expect(pad.textContent).toContain("already listens there");
    fireEvent.click(within(pad).getByRole("button"));
    expect(pad.getAttribute("data-state")).toBe("folded");
  });

  it("reduces wordless thinking to its time and never opens it", () => {
    draw([
      ev("turn_started", { turn_id: "t4", ...TURN }),
      ev("reasoning", { turn_id: "t4", text: "", duration_ms: 2000 }),
      ev("tool_call", { turn_id: "t4", call_id: "c1", name: "Read", input: { path: "a.py" } }),
      ev("tool_result", { turn_id: "t4", call_id: "c1", output: "ok", is_error: false }),
      ev("reasoning", { turn_id: "t4", text: "", duration_ms: 6000 }),
      ev("assistant_text", { turn_id: "t4", message_id: "m1", text: "Done." }),
      ev("turn_finished", { turn_id: "t4", status: "done", duration_ms: 9000, usage: {} }),
    ]);

    const rows = screen.getAllByTestId("agent-reasoning");
    expect(rows).toHaveLength(1);
    expect(rows[0].getAttribute("data-state")).toBe("silent");
    expect(rows[0].textContent).toMatch(/8s/);
    expect(within(rows[0]).getByRole("button").hasAttribute("disabled")).toBe(true);
  });

  it("draws nothing for a thought that has started but has no words yet", () => {
    draw([
      ev("turn_started", { turn_id: "t5", ...TURN }),
      ev("reasoning_started", { turn_id: "t5", message_id: "r1" }),
    ]);
    expect(screen.queryByTestId("agent-reasoning")).toBeNull();
    // The live line below is what says the turn is thinking.
    expect(screen.getAllByTestId("agent-turn-live")).toHaveLength(1);
  });
});
