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

/*
 * The change a coding call made, and the wall of steps around it.
 *
 * Two complaints from 2026-08-27, judged against Claude Code and Codex: an
 * edit arrived as its raw arguments, so the one thing worth reading — what
 * actually changed — was the one thing you could not read; and a finished
 * turn left its whole run of calls lying in the transcript, which the
 * maintainer called "far too much stuff".
 */
describe("a coding call shows its diff", () => {
  const EDIT = {
    turn_id: "d1",
    call_id: "e1",
    name: "Edit",
    input: {
      file_path: "src/app.ts",
      old_string: "const port = 47821;",
      new_string: "const port = 47921;",
    },
  };

  function drawEdit(output = "The file src/app.ts has been updated successfully.") {
    draw([
      ev("user_message", { text: "change the port" }),
      ev("turn_started", { turn_id: "d1", ...TURN }),
      ev("tool_call", EDIT),
      ev("tool_result", { turn_id: "d1", call_id: "e1", output, is_error: false }),
      ev("turn_finished", { turn_id: "d1", status: "done", duration_ms: 900, usage: {} }),
    ]);
  }

  it("wears the size of the change on the row, before anything is opened", () => {
    drawEdit();
    const stat = screen.getByTestId("agent-diff-stat");
    expect(stat.dataset.added).toBe("1");
    expect(stat.dataset.removed).toBe("1");
  });

  it("paints the removed line and the added one when the row is opened", () => {
    drawEdit();
    fireEvent.click(screen.getByTestId("agent-tool").querySelector("button")!);
    const diff = screen.getByTestId("agent-tool-diff");
    const removed = diff.querySelector("[data-diff='del']");
    const added = diff.querySelector("[data-diff='add']");
    expect(removed?.textContent).toContain("47821");
    expect(added?.textContent).toContain("47921");
    // The colours live on a class each, so both themes get their own pair.
    expect(removed?.className).toContain("diff-line-del");
    expect(added?.className).toContain("diff-line-add");
  });

  it("does not repeat the edit's receipt under the diff it already showed", () => {
    drawEdit();
    fireEvent.click(screen.getByTestId("agent-tool").querySelector("button")!);
    expect(screen.getByTestId("agent-tool-diff")).toBeTruthy();
    expect(screen.queryByText(/has been updated successfully/)).toBeNull();
  });

  it("still shows the output when the edit FAILED — that is the whole point", () => {
    draw([
      ev("turn_started", { turn_id: "d1", ...TURN }),
      ev("tool_call", EDIT),
      ev("tool_result", {
        turn_id: "d1",
        call_id: "e1",
        output: "String to replace not found in file.",
        is_error: true,
      }),
      ev("turn_finished", { turn_id: "d1", status: "done", duration_ms: 900, usage: {} }),
    ]);
    fireEvent.click(screen.getByTestId("agent-tool").querySelector("button")!);
    expect(screen.getByText(/String to replace not found/)).toBeTruthy();
  });

  it("leaves a call that edits nothing exactly as it was", () => {
    draw([
      ev("turn_started", { turn_id: "d2", ...TURN }),
      ev("tool_call", { turn_id: "d2", call_id: "b1", name: "Bash", input: { command: "ls" } }),
      ev("tool_result", { turn_id: "d2", call_id: "b1", output: "a.ts", is_error: false }),
      ev("turn_finished", { turn_id: "d2", status: "done", duration_ms: 200, usage: {} }),
    ]);
    expect(screen.queryByTestId("agent-diff-stat")).toBeNull();
    fireEvent.click(screen.getByTestId("agent-tool").querySelector("button")!);
    expect(screen.queryByTestId("agent-tool-diff")).toBeNull();
    expect(screen.getByText("a.ts")).toBeTruthy();
  });
});

describe("a run of steps puts itself away", () => {
  /** `count` calls in a row, optionally one of them failing. */
  function run(count: number, opts: { live?: boolean; failAt?: number } = {}) {
    const events = [
      ev("user_message", { text: "look into it" }),
      ev("turn_started", { turn_id: "g1", ...TURN }),
    ];
    for (let i = 0; i < count; i++) {
      events.push(
        ev("tool_call", {
          turn_id: "g1",
          call_id: `c${i}`,
          name: "Grep",
          input: { pattern: `p${i}` },
        }),
      );
      events.push(
        ev("tool_result", {
          turn_id: "g1",
          call_id: `c${i}`,
          output: "hit",
          is_error: opts.failAt === i,
        }),
      );
    }
    if (!opts.live) {
      events.push(
        ev("turn_finished", { turn_id: "g1", status: "done", duration_ms: 5000, usage: {} }),
      );
    }
    draw(events);
  }

  it("folds a finished run to one line, and opens it again on a click", () => {
    run(6);
    const group = screen.getByTestId("agent-tool-group");
    expect(group.dataset.state).toBe("folded");
    expect(screen.queryAllByTestId("agent-tool")).toHaveLength(0);

    fireEvent.click(screen.getByTestId("agent-tool-group-toggle"));
    expect(screen.getByTestId("agent-tool-group").dataset.state).toBe("open");
    expect(screen.queryAllByTestId("agent-tool")).toHaveLength(6);
  });

  it("leaves a short run alone: three rows are not a wall", () => {
    run(3);
    expect(screen.getByTestId("agent-tool-group").dataset.state).toBe("plain");
    expect(screen.queryByTestId("agent-tool-group-toggle")).toBeNull();
    expect(screen.queryAllByTestId("agent-tool")).toHaveLength(3);
  });

  it("shows every step while the turn is still working", () => {
    run(6, { live: true });
    expect(screen.getByTestId("agent-tool-group").dataset.state).toBe("plain");
    expect(screen.queryAllByTestId("agent-tool")).toHaveLength(6);
  });

  it("folds AROUND a step that failed, keeping the failure itself in view", () => {
    // The first pass held the whole run open on any failure, which left a
    // group of thirty-five calls lying open because one of them missed (live
    // check, 2026-08-27). The failure is the thing worth seeing — not the
    // thirty-four calls that went fine around it.
    run(6, { failAt: 2 });
    expect(screen.getByTestId("agent-tool-group").dataset.state).toBe("folded");
    const rows = screen.queryAllByTestId("agent-tool");
    expect(rows).toHaveLength(1);
    expect(rows[0].dataset.state).toBe("error");
  });
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
  });
});
