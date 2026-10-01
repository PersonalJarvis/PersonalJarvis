/**
 * `transferTerminal` against a backend that knows the route and one that
 * predates it. The second is the normal state right after an update: the
 * bundle reloads on its own, the Python backend only on the next restart, so
 * the pane menu already offers a move the server cannot do yet.
 */
import { afterEach, expect, it, vi } from "vitest";
import { fetchWorkspaceLayout, transferTerminal } from "./agenticIdeApi";

function answer(status: number, body: unknown) {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(body), { status })));
}

afterEach(() => { vi.unstubAllGlobals(); });

it("posts both workspaces and returns the moved pane", async () => {
  answer(200, { terminal: { name: "T3" }, source_workspace_id: "w1", target_workspace_id: "w2", state: {} });

  const result = await transferTerminal("pane:h1", "w1", "w2");

  const [url, init] = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
  expect(url).toBe("/api/agentic-ide/terminals/pane%3Ah1/transfer");
  expect(JSON.parse(init.body)).toEqual({ workspace_id: "w1", target_workspace_id: "w2" });
  expect(result.terminal.name).toBe("T3");
});

it("passes on the server's own reason", async () => {
  answer(409, { detail: "Blog already has the maximum of 12 terminals." });
  await expect(transferTerminal("T1", "w1", "w2")).rejects.toThrow("Blog already has the maximum of 12 terminals.");
});

it("asks for a restart when the backend predates the route", async () => {
  answer(404, { detail: "Not Found" });
  await expect(transferTerminal("T1", "w1", "w2")).rejects.toThrow("restart the app");
});

it("names the chosen place in the target grid", async () => {
  answer(200, { terminal: { name: "T2" }, state: {} });

  await transferTerminal("T1", "w1", "w2", { anchor: "pane:b1", side: "below" });

  const [, init] = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
  expect(JSON.parse(init.body)).toEqual({ workspace_id: "w1", target_workspace_id: "w2", anchor: "pane:b1", side: "below" });
});

it("reads another workspace's shape from its own route", async () => {
  answer(200, { id: "w2", name: "Blog", layout: null, terminals: [], max_terminals: 16 });

  const view = await fetchWorkspaceLayout("w2");

  expect((fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0][0]).toBe("/api/agentic-ide/workspaces/w2/layout");
  expect(view.name).toBe("Blog");
});
