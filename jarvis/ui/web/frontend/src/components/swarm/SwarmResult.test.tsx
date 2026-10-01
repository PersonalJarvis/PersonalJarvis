import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SwarmResult } from "./SwarmResult";
import { teamFixture } from "./testFixtures";
import type { ScopedRecord, TeamRecord } from "./types";

vi.mock("@/i18n", () => ({ useUiLanguage: () => "en" }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const deliveryId = "__swarm_delivery";
const send = (body: unknown) => new Response(JSON.stringify(body));
function completed(id = "alpha"): TeamRecord {
  return { ...teamFixture(id), state: "succeeded", storage_generation: "generation-a", checkpoint: { autonomy: { decision: "deliver" } } };
}
function delivery(teamId = "alpha", evidence = ["report", "numbers", "proof"]): ScopedRecord {
  return { id: deliveryId, team_id: teamId, state: "succeeded", version: 2, result: "The count is **4**, the sum is **40**, and the mean is **10**.", evidence };
}
function artifact(id: string, origin: string, name: string, mediaType = "application/json", taskId = "calculate"): ScopedRecord {
  return { id, team_id: "alpha", name, task_id: taskId, media_type: mediaType, provenance: { origin } };
}
function fixtureFetch(team: TeamRecord, task: ScopedRecord, artifacts: ScopedRecord[], content = "{\"count\":4,\"sum\":40,\"mean\":10}") {
  const calls: string[] = [];
  vi.stubGlobal("fetch", async (path: string, init?: RequestInit) => {
    expect(init?.method ?? "GET").toBe("GET"); calls.push(path);
    if (path.endsWith(`/tasks/record/${deliveryId}`)) return send(task);
    if (path.includes("/artifacts?")) return send(artifacts);
    if (path.endsWith("/tasks?limit=50&offset=0")) return send([]);
    if (path.includes("/artifacts/")) return new Response(content);
    return send(team);
  });
  return calls;
}

describe("the user's completed Swarm result", () => {
  it("shows the full accepted delivery, readable Markdown and actual files without fetching file contents", async () => {
    const team = completed(); const task = delivery();
    task.result = `${"Accepted detail. ".repeat(30)}\n\nThe final total is **40**.\n\n![External picture](https://untrusted.test/track.png)`;
    const calls = fixtureFetch(team, task, [
      artifact("report", "worker-output", "result.txt", "text/plain", deliveryId),
      artifact("numbers", "worker-authored", "statistics.data"),
      artifact("proof", "runtime-receipt", "verification.json"),
      artifact("unaccepted", "worker-authored", "unaccepted.json"),
    ]);
    const onInspect = vi.fn();
    render(<SwarmResult team={team} awake onInspect={onInspect} />);
    await screen.findByText("statistics.data");
    expect(screen.getByText("40", { selector: "strong" })).toBeTruthy();
    expect(screen.getByText(/Accepted detail\./).textContent!.length).toBeGreaterThan(200);
    expect(screen.queryByText("verification.json")).toBeNull();
    expect(screen.queryByText("unaccepted.json")).toBeNull();
    expect(screen.queryByRole("img")).toBeNull();
    expect(calls.some(path => /\/artifacts\//.test(path))).toBe(false);
    expect(screen.getByRole("link", { name: "Download" }).getAttribute("href")).toBe("/api/swarm/teams/alpha/artifacts/numbers");
    expect(screen.getByRole("link", { name: "Download result summary" }).getAttribute("download")).toBe("result.txt");
    fireEvent.click(screen.getByRole("button", { name: "Inspect completed work" }));
    expect(onInspect).toHaveBeenLastCalledWith("tasks", deliveryId);
    fireEvent.click(screen.getByRole("button", { name: "See all files and evidence" }));
    expect(onInspect).toHaveBeenLastCalledWith("artifacts", "");
  });

  it("retains a successful delivery in archive and previews a file only on request", async () => {
    const team = { ...completed(), state: "archived" as const };
    fixtureFetch(team, delivery("alpha", ["numbers"]), [artifact("numbers", "worker-authored", "statistics.json")]);
    render(<SwarmResult team={team} awake onInspect={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Preview" }));
    await screen.findByText('{"count":4,"sum":40,"mean":10}');
    fireEvent.click(screen.getByRole("button", { name: "Close preview" }));
    expect(screen.queryByText('{"count":4,"sum":40,"mean":10}')).toBeNull();
  });

  it("bounds previews by bytes and keeps nontext files downloadable", async () => {
    const team = completed();
    const calls = fixtureFetch(team, delivery("alpha", ["text", "binary"]), [
      artifact("text", "worker-authored", "large.txt", "text/plain"),
      artifact("binary", "worker-authored", "output.custom", "application/octet-stream"),
    ], "x".repeat(100010));
    const { container } = render(<SwarmResult team={team} awake onInspect={() => {}} />);
    await screen.findByText("output.custom");
    expect(screen.getAllByRole("button", { name: "Preview" })).toHaveLength(1);
    expect(screen.getAllByRole("link", { name: "Download" })).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: "Preview" }));
    await waitFor(() => expect(container.querySelector("pre")?.textContent?.length).toBe(100000));
    expect(calls.filter(path => /\/artifacts\//.test(path))).toHaveLength(1);
  });

  it("does not load or claim a result while work is still running", () => {
    const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
    const { container } = render(<SwarmResult team={teamFixture()} awake onInspect={() => {}} />);
    expect(container.textContent).toBe(""); expect(fetch).not.toHaveBeenCalled();
  });

  it("handles legacy runs and archived failures without inventing a final delivery", async () => {
    const team = { ...completed(), checkpoint: {} };
    const calls = fixtureFetch(team, delivery(), []);
    const { rerender } = render(<SwarmResult team={team} awake onInspect={() => {}} />);
    await screen.findByText(/no saved, verified final delivery/);
    expect(calls.some(path => path.includes("/record/"))).toBe(false);
    const archived = { ...completed(), state: "archived" as const, version: 2 };
    fixtureFetch(archived, { ...delivery(), state: "canceled" }, []);
    rerender(<SwarmResult team={archived} awake onInspect={() => {}} />);
    await screen.findByText(/no saved, verified final delivery/);
    expect(screen.queryByText(/Final delivery completed/)).toBeNull();
  });

  it("hides old content synchronously on team and storage-generation changes and ignores a late reply", async () => {
    const team = completed();
    fixtureFetch(team, delivery(), [artifact("numbers", "worker-authored", "statistics.json")]);
    const { rerender } = render(<SwarmResult team={team} awake onInspect={() => {}} />);
    await screen.findByText("statistics.json");
    let release: (response: Response) => void = () => {};
    let oldSignal: AbortSignal | undefined;
    const restored = { ...team, storage_generation: "generation-b" };
    vi.stubGlobal("fetch", async (_path: string, init?: RequestInit) => {
      oldSignal = init?.signal as AbortSignal;
      return new Promise<Response>(resolve => { release = resolve; });
    });
    rerender(<SwarmResult team={restored} awake onInspect={() => {}} />);
    expect(screen.queryByText("statistics.json")).toBeNull();
    const beta = completed("beta");
    fixtureFetch(beta, { ...delivery("beta", []), result: "Beta result" }, []);
    rerender(<SwarmResult team={beta} awake onInspect={() => {}} />);
    await screen.findByText("Beta result");
    expect(oldSignal?.aborted).toBe(true);
    await act(async () => { release(send(restored)); });
    expect(screen.queryByText("statistics.json")).toBeNull();
    expect(screen.getByText("Beta result")).toBeTruthy();
  });

  it("rejects mismatched full records and generation changes during a read", async () => {
    const team = completed();
    fixtureFetch(team, delivery("foreign"), []);
    const { rerender } = render(<SwarmResult team={team} awake onInspect={() => {}} />);
    await screen.findByRole("alert");
    expect(screen.queryByText(/Final delivery completed/)).toBeNull();
    let teamReads = 0;
    const newer = { ...team, version: 2 };
    vi.stubGlobal("fetch", async (path: string) => {
      if (path.includes("/record/")) return send(delivery("alpha", []));
      teamReads += 1;
      return send({ ...newer, storage_generation: teamReads > 1 ? "restored" : newer.storage_generation });
    });
    rerender(<SwarmResult team={newer} awake onInspect={() => {}} />);
    await screen.findByText(/saved run changed/);
    expect(screen.queryByText(/Final delivery completed/)).toBeNull();
  });

  it("caps metadata discovery and exposes the complete inspector when the accepted file is beyond it", async () => {
    const team = completed(); const offsets: number[] = [];
    vi.stubGlobal("fetch", async (path: string) => {
      if (path.includes("/record/")) return send(delivery("alpha", ["later-file"]));
      if (path.includes("/artifacts?")) {
        const offset = Number(new URL(path, "http://localhost").searchParams.get("offset")); offsets.push(offset);
        return send(Array.from({ length: 50 }, (_, index) => artifact(`proof-${offset + index}`, "runtime-receipt", "execution.json")));
      }
      return send(team);
    });
    render(<SwarmResult team={team} awake onInspect={() => {}} />);
    await screen.findByText(/limited selection of the accepted files/);
    expect(offsets).toEqual([0, 50, 100, 150]);
    expect(screen.getByRole("button", { name: "See all files and evidence" })).toBeTruthy();
  });
});
