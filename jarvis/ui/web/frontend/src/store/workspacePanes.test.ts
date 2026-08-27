/**
 * The session list's one store: a poll for the list, and the socket's word
 * for what each session is DOING — patched in place the moment it arrives,
 * so a finished session does not keep its spinner until the next poll.
 */
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { WorkspacePaneRow } from "@/lib/agenticIdeApi";
import {
  PANE_ACTIVITY_EVENT,
  readPaneActivityChange,
  resetWorkspacePanesPoll,
  useWorkspacePanes,
  useWorkspacePanesStore,
} from "@/store/workspacePanes";

function pane(name: string, workspaceId: string, overrides: Partial<WorkspacePaneRow> = {}) {
  return {
    workspace_id: workspaceId,
    workspace_name: "Personal Jarvis",
    folder: "C:\\Users\\dev\\Personal Jarvis",
    workspace_active: true,
    key: name.toLowerCase(),
    history_id: `${name}@${workspaceId}`,
    name,
    agent: "claude",
    display_name: "Claude Code",
    accepts_prompts: true,
    status: "live" as const,
    exit_code: null,
    activity: "working" as const,
    activity_since: 1000,
    worked: true,
    started_at: 1,
    last_output_at: 2,
    last_prompt: "",
    last_prompt_at: null,
    recap: "Refactor the router",
    has_resume: false,
    readable: true,
    account: null,
    account_label: null,
    archived: false,
    ...overrides,
  };
}

const finished = (name: string, workspace = "w1") => ({
  session_id: workspace,
  key: name.toLowerCase(),
  name,
  status: "live",
  activity: "waiting" as const,
  activity_since: 1042,
  worked: true,
});

describe("the pane store follows the socket's word", () => {
  beforeEach(() => {
    resetWorkspacePanesPoll();
    useWorkspacePanesStore.setState({
      panes: [pane("T1", "w1"), pane("T2", "w1"), pane("T1", "w2")],
      activeId: "w1",
      loaded: true,
      load: async () => {},
    });
  });
  afterEach(() => resetWorkspacePanesPoll());

  it("patches exactly the row the change names — by workspace AND call-sign", () => {
    useWorkspacePanesStore.getState().applyActivity(finished("T1", "w2"));

    const rows = useWorkspacePanesStore.getState().panes;
    expect(rows.map((row) => `${row.workspace_id}/${row.name}:${row.activity}`)).toEqual([
      "w1/T1:working",
      "w1/T2:working",
      "w2/T1:waiting",
    ]);
    const patched = rows[2];
    expect(patched.activity_since).toBe(1042);
    expect(patched.worked).toBe(true);
    // Everything the event does not carry is kept, not blanked.
    expect(patched.recap).toBe("Refactor the router");
    expect(patched.display_name).toBe("Claude Code");
  });

  it("leaves a pane it has never listed for the next poll", () => {
    const before = useWorkspacePanesStore.getState().panes;
    useWorkspacePanesStore.getState().applyActivity(finished("T9"));
    expect(useWorkspacePanesStore.getState().panes).toBe(before);
  });

  it("keeps the list's identity when the event repeats what the poll said", () => {
    useWorkspacePanesStore.getState().applyActivity(finished("T1"));
    const after = useWorkspacePanesStore.getState().panes;
    useWorkspacePanesStore.getState().applyActivity(finished("T1"));
    expect(useWorkspacePanesStore.getState().panes).toBe(after);
  });

  it("takes a process status only from the four the row can hold", () => {
    useWorkspacePanesStore
      .getState()
      .applyActivity({ ...finished("T2"), status: "exited", activity: "exited" });
    expect(useWorkspacePanesStore.getState().panes[1].status).toBe("exited");

    useWorkspacePanesStore
      .getState()
      .applyActivity({ ...finished("T2"), status: "bogus", activity: "waiting" });
    expect(useWorkspacePanesStore.getState().panes[1].status).toBe("exited");
  });

  it("listens on the window for as long as somebody reads the list", () => {
    const { unmount } = renderHook(() => useWorkspacePanes());

    act(() => {
      window.dispatchEvent(new CustomEvent(PANE_ACTIVITY_EVENT, { detail: finished("T1") }));
    });
    expect(useWorkspacePanesStore.getState().panes[0].activity).toBe("waiting");

    unmount();
    act(() => {
      window.dispatchEvent(
        new CustomEvent(PANE_ACTIVITY_EVENT, {
          detail: { ...finished("T2"), activity: "asking" },
        }),
      );
    });
    // Nobody is looking: the event is dropped, exactly like the poll stops.
    expect(useWorkspacePanesStore.getState().panes[1].activity).toBe("working");
  });

  it("ignores a detail that is not a change", () => {
    expect(readPaneActivityChange(null)).toBeNull();
    expect(readPaneActivityChange("T1")).toBeNull();
    expect(readPaneActivityChange({ key: "t1" })).toBeNull();
    expect(readPaneActivityChange({ session_id: "w1", key: "t1" })).toEqual({
      session_id: "w1",
      key: "t1",
      name: "",
      status: "",
      activity: "",
      activity_since: 0,
      worked: false,
    });
  });
});
