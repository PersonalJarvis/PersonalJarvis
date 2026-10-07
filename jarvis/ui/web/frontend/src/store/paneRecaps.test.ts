import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { TerminalRecap } from "@/lib/agenticIdeApi";
import { useEventStore } from "@/store/events";
import { useIdeProjectsStore } from "@/store/ideProjects";
import { useWorkspacePanesStore } from "@/store/workspacePanes";
import { paneTitleFrom, recapsFor, usePaneRecapsStore, usePaneTitle } from "./paneRecaps";

const row = { recap: "", last_prompt: "", display_name: "Claude Code", name: "T1" };

describe("paneTitleFrom", () => {
  it("prefers a title the model or the user wrote", () => {
    expect(paneTitleFrom({ recap: "Release pipeline — green CI", source: "model" }, row)).toBe("Release pipeline — green CI");
    expect(paneTitleFrom({ recap: "Demo branch", source: "user" }, row)).toBe("Demo branch");
  });

  it("ignores the screen-derived line and falls back to the pane's topic", () => {
    const asked = { ...row, last_prompt: "Fix the failing login test" };
    expect(paneTitleFrom({ recap: "Claude Code — running since 17:22", source: "heuristic" }, asked)).toBe(
      "Fix the failing login test",
    );
  });

  it("answers empty for a pane nobody asked anything, so the call-sign shows", () => {
    expect(paneTitleFrom(undefined, row)).toBe("");
    expect(paneTitleFrom(undefined, undefined)).toBe("");
  });
});

const titled = (recap: string) => ({ recap, source: "cli" }) as TerminalRecap;

describe("recapsFor", () => {
  it("keeps a workspace's titles after another one answered", () => {
    const state = {
      workspaceId: "w2",
      byName: { T1: titled("Second workspace") },
      byWorkspace: { w1: { T1: titled("First workspace") }, w2: { T1: titled("Second workspace") } },
    };
    expect(recapsFor(state, "w1")?.T1.recap).toBe("First workspace");
    expect(recapsFor(state, "w2")?.T1.recap).toBe("Second workspace");
    expect(recapsFor(state, "w3")).toBeUndefined();
  });
});

describe("usePaneTitle across a workspace switch", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    useEventStore.setState({ activeSection: "chats" });
    useIdeProjectsStore.setState({ activeWorkspaceId: null });
    usePaneRecapsStore.setState({ workspaceId: null, byName: {}, byWorkspace: {} });
  });

  it("fetches the new workspace's titles at once instead of on the next tick", async () => {
    const asked: string[] = [];
    vi.stubGlobal("fetch", async (url: string) => {
      asked.push(url);
      const id = new URL(url, "http://x").searchParams.get("workspace_id") ?? "w1";
      return new Response(
        JSON.stringify({ workspace_id: id, terminals: [{ name: "T1", recap: `Title in ${id}`, source: "cli" }] }),
      );
    });
    useWorkspacePanesStore.setState({ panes: [] });
    useIdeProjectsStore.setState({ activeWorkspaceId: "w1" });
    useEventStore.setState({ activeSection: "agentic-ide" });

    const { result, rerender } = renderHook(({ id }) => usePaneTitle(id, "T1"), { initialProps: { id: "w1" } });
    await waitFor(() => expect(result.current).toBe("Title in w1"));

    act(() => useIdeProjectsStore.setState({ activeWorkspaceId: "w2" }));
    rerender({ id: "w2" });
    await waitFor(() => expect(result.current).toBe("Title in w2"));
    expect(asked.some((url) => url.includes("workspace_id=w2"))).toBe(true);

    // Back to the first workspace: its titles are already held, before any answer.
    vi.stubGlobal("fetch", () => new Promise<Response>(() => {}));
    act(() => useIdeProjectsStore.setState({ activeWorkspaceId: "w1" }));
    rerender({ id: "w1" });
    expect(result.current).toBe("Title in w1");
  });
});
