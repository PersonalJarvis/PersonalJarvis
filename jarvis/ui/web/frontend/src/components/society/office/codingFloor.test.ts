/**
 * The coding floor's roster: IDE panes projected into office figures — which
 * panes count, what they are called, where they stand, how they look, and
 * that a status change does not rebuild the figures that did not change.
 */
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { WorkspacePaneRow } from "@/lib/agenticIdeApi";
import { resetWorkspacePanesPoll, useWorkspacePanesStore } from "@/store/workspacePanes";

import {
  isPaneAgentId, paneAgentId, paneFigure, paneLabel, paneOccupants, panePlateName, paneRunState, plateTitle, useCodingFloorOccupants,
} from "./codingFloor";
import { allDesks, buildOfficeLayout } from "./officeLayout";
import { toyLookFor } from "./toyFigureModel";

function pane(name: string, workspaceId: string, overrides: Partial<WorkspacePaneRow> = {}): WorkspacePaneRow {
  return {
    workspace_id: workspaceId,
    workspace_name: workspaceId === "w1" ? "Personal Jarvis" : "Website",
    folder: "/home/dev/project",
    workspace_active: true,
    key: name,
    history_id: `${name}@${workspaceId}`,
    name,
    agent: "claude",
    display_name: "Claude Code",
    accepts_prompts: true,
    status: "live",
    exit_code: null,
    activity: "working",
    activity_since: 1000,
    worked: true,
    started_at: 100,
    last_output_at: 2,
    last_prompt: "Fix the router",
    last_prompt_at: 90,
    recap: "Refactor the router",
    has_resume: false,
    readable: true,
    account: null,
    account_label: null,
    archived: false,
    ...overrides,
  };
}

describe("coding floor roster", () => {
  it("names a pane's figure by workspace and lifetime id, never colliding with society ids", () => {
    const id = paneAgentId(pane("T1", "w1"));
    expect(id).toBe("pane:w1:T1@w1");
    expect(isPaneAgentId(id)).toBe(true);
    expect(isPaneAgentId("research-scout")).toBe(false);
    expect(paneAgentId({ workspace_id: "w1", history_id: "", key: "T3" })).toBe("pane:w1:T3");
  });

  it("keeps coding panes only, ordered by workspace, then start time, then call-sign", () => {
    const rows = [
      pane("T3", "w1", { started_at: 50 }),
      pane("sh", "w1", { agent: "shell", display_name: "Shell" }),
      pane("T1", "w2"),
      pane("T2", "w1", { archived: true }),
      pane("T10", "w1", { started_at: 100 }),
      pane("T9", "w1", { started_at: 100 }),
    ];
    expect(paneOccupants(rows).map((o) => `${o.pane.workspace_name}/${o.pane.name}`))
      .toEqual(["Personal Jarvis/T3", "Personal Jarvis/T9", "Personal Jarvis/T10", "Website/T1"]);
  });

  it("labels the plate with the pane's topic, and keeps the CLI and call-sign as the title", () => {
    const [occupant] = paneOccupants([pane("T2", "w1", { display_name: "Gemini CLI", agent: "gemini" })]);
    expect(occupant.agent.name).toBe("Refactor the router");
    expect(occupant.agent.title).toBe("Gemini · T2");
    expect(paneLabel({ display_name: "", agent: "codex", name: "", key: "T4" })).toBe("Codex · T4");
    expect(panePlateName(pane("T3", "w1", { recap: "", last_prompt: "Fix the login test" }))).toBe("Fix the login test");
    expect(panePlateName(pane("T3", "w1", { recap: "", last_prompt: "" }))).toBe("Claude · T3");
    expect(occupant.agent.providerLabel).toBe("Personal Jarvis");
    expect(occupant.agent.tier).toBe("specialist");
    expect(occupant.agent.chatSessionId).toBeNull();
    expect(occupant.agent.createdMs).toBe(100_000);
  });

  it("splits a recap into subject and result for the two-line plate", () => {
    expect(plateTitle("Office map camera — controls fix")).toEqual({ subject: "Office map camera", result: "controls fix" });
    expect(plateTitle("Terminal tabs – rename without restart")).toEqual({ subject: "Terminal tabs", result: "rename without restart" });
    expect(plateTitle("Login test - flaky on CI")).toEqual({ subject: "Login test", result: "flaky on CI" });
    expect(plateTitle("Fix the login test")).toEqual({ subject: "Fix the login test", result: "" });
    expect(plateTitle("self-hosted runner")).toEqual({ subject: "self-hosted runner", result: "" });
    expect(plateTitle(" — dangling")).toEqual({ subject: "— dangling", result: "" });
  });

  it("gives same-named workspaces their own departments", () => {
    const occupants = paneOccupants([pane("T1", "a", { workspace_name: "App" }), pane("T1", "b", { workspace_name: "App" })]);
    expect(occupants.map((o) => o.agent.providerLabel)).toEqual(["App", "App 2"]);
  });

  it("maps the pane's state onto where its figure stands", () => {
    expect(paneRunState(pane("T1", "w1", { activity: "working" }))).toBe("working");
    expect(paneRunState(pane("T1", "w1", { activity: "starting" }))).toBe("working");
    expect(paneRunState(pane("T1", "w1", { status: "pending", activity: "" }))).toBe("working");
    expect(paneRunState(pane("T1", "w1", { activity: "asking" }))).toBe("waiting");
    // An errored pane stands at its desk too; the occupant keeps the real word.
    const [failed] = paneOccupants([pane("T1", "w1", { activity: "failed" })]);
    expect(failed.agent.state).toBe("waiting");
    expect(failed.dot).toBe("error");
    expect(failed.stateKey).toBe("failed");
    expect(paneRunState(pane("T1", "w1", { status: "error", activity: "" }))).toBe("waiting");
    expect(paneRunState(pane("T1", "w1", { activity: "waiting" }))).toBe("idle");
    expect(paneRunState(pane("T1", "w1", { status: "exited", activity: "exited" }))).toBe("idle");
  });

  it("dresses each pane randomly but stably", () => {
    expect(paneFigure("abc")).toEqual(paneFigure("abc"));
    const looks = Array.from({ length: 24 }, (_, i) => toyLookFor(paneFigure(`h${i}`), `pane:w:h${i}`));
    const distinct = (key: keyof (typeof looks)[number]) => new Set(looks.map((l) => l[key])).size;
    expect(distinct("skin")).toBeGreaterThan(2);
    expect(distinct("hair")).toBeGreaterThan(2);
    expect(distinct("hairStyle")).toBeGreaterThan(2);
    expect(distinct("shirt")).toBeGreaterThan(3);
    expect(distinct("pants")).toBeGreaterThan(1);
    expect(distinct("outfit")).toBeGreaterThan(2);
  });

  it("seats every occupant at a desk of its workspace's department on the coding floor", () => {
    const rows = [pane("T1", "w1"), pane("T2", "w1", { activity: "asking" }), pane("T1", "w2", { activity: "" })];
    const occupants = paneOccupants(rows);
    const layout = buildOfficeLayout(occupants.map((o) => o.agent), { variant: "coding" });
    const seated = new Map(allDesks(layout).filter((d) => d.agentId).map((d) => [d.agentId!, d.id]));
    expect(seated.size).toBe(3);
    for (const o of occupants) {
      const dept = layout.departments.find((d) => d.desks.some((desk) => desk.agentId === o.agent.agentId))!;
      expect(dept.label).toBe(o.agent.providerLabel);
    }
  });
});

describe("useCodingFloorOccupants", () => {
  beforeEach(() => {
    resetWorkspacePanesPoll();
    useWorkspacePanesStore.setState({ panes: [pane("T1", "w1"), pane("T2", "w1")], activeId: "w1", loaded: true, load: async () => {} });
  });
  afterEach(() => resetWorkspacePanesPoll());

  it("keeps unchanged occupants' identity when another pane changes state", () => {
    const { result } = renderHook(() => useCodingFloorOccupants(true));
    const before = result.current;
    expect(before.loaded).toBe(true);
    expect(before.occupants).toHaveLength(2);

    // A fresh poll with new row objects but the same content, output ticking: nothing changes.
    act(() => {
      useWorkspacePanesStore.setState({ panes: [pane("T1", "w1", { last_output_at: 9 }), pane("T2", "w1", { last_output_at: 9 })] });
    });
    expect(result.current).toBe(before);

    act(() => {
      useWorkspacePanesStore.setState({ panes: [pane("T1", "w1"), pane("T2", "w1", { activity: "asking" })] });
    });
    const after = result.current;
    expect(after).not.toBe(before);
    expect(after.occupants[0]).toBe(before.occupants[0]);
    expect(after.occupants[1]).not.toBe(before.occupants[1]);
    expect(after.occupants[1].agent.state).toBe("waiting");
    expect(after.byAgentId.get(paneAgentId(pane("T2", "w1")))).toBe(after.occupants[1]);
  });

  it("starts no poll while disabled", () => {
    let loads = 0;
    useWorkspacePanesStore.setState({ load: async () => { loads += 1; } });
    const { rerender, unmount } = renderHook(({ on }) => useCodingFloorOccupants(on), { initialProps: { on: false } });
    expect(loads).toBe(0);
    rerender({ on: true });
    expect(loads).toBe(1);
    unmount();
  });
});
