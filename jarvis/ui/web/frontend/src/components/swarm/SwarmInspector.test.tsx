import { useState } from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SwarmInspector } from "./SwarmInspector";
import { snapshotFixture } from "./testFixtures";
import type { RecordKind, TaskRecord, WorldSnapshot } from "./types";

vi.mock("@/i18n", () => ({ useUiLanguage: () => "en" }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
function Inspector({ kind = "tasks", selected = "late-task", snapshot = snapshotFixture() }: { kind?: RecordKind; selected?: string; snapshot?: WorldSnapshot }) {
  const [selection, setSelection] = useState({ kind, selected });
  return <SwarmInspector snapshot={snapshot} {...selection} onSelect={(nextKind, id) => setSelection({ kind: nextKind, selected: id })} awake />;
}
describe("Swarm evidence and relationship inspector", () => {
  it("filters records with the themed status menu and restores all records", async () => {
    vi.stubGlobal("fetch", async () => new Response(JSON.stringify([
      { id: "working", team_id: "alpha", title: "Current calculation", state: "running" },
      { id: "done", team_id: "alpha", title: "Verified calculation", state: "succeeded" },
    ])));
    render(<Inspector selected="" />);
    await screen.findByRole("button", { name: "Current calculation Running" });
    const filter = screen.getByRole("combobox", { name: "Filter by status" });
    fireEvent.click(filter);
    fireEvent.click(screen.getByRole("option", { name: "Succeeded" }));
    expect(screen.queryByRole("button", { name: "Current calculation Running" })).toBeNull();
    expect(screen.getByRole("button", { name: "Verified calculation Succeeded" })).toBeTruthy();
    fireEvent.click(filter);
    fireEvent.click(screen.getByRole("option", { name: "All" }));
    expect(screen.getByRole("button", { name: "Current calculation Running" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Verified calculation Succeeded" })).toBeTruthy();
  });

  it("loads the complete result and all evidence instead of the truncated live projection", async () => {
    const snapshot = snapshotFixture();
    const result = "Verified source-backed result. ".repeat(20);
    const evidence = ["proof-1", "proof-2", "proof-3", "proof-4", "proof-5"];
    const task: TaskRecord = { id: "late-task", team_id: "alpha", title: "Complete result", description: "Original goal", acceptance: "Original criteria", dependencies: [], domain: "research", milestone: "delivery", difficulty: 3, priority: 5, verification: "review", verification_script: "", required_tools: [], independent_verification: true, state: "running", owner_id: "alpha-lead", attempt_count: 1, fence: 1, version: 1, result, evidence, reason: "", created_at: 1, updated_at: 1 };
    snapshot.tasks = [{ ...task, result: result.slice(0, 200), evidence: evidence.slice(0, 3) }];
    let fullTask = task;
    let detailReads = 0;
    vi.stubGlobal("fetch", async (path: string) => {
      if (path.includes("/record/")) { detailReads += 1; return new Response(JSON.stringify(fullTask)); }
      return new Response(JSON.stringify([fullTask]));
    });
    const { rerender } = render(<Inspector snapshot={snapshot} />);
    await screen.findByText(result.trim());
    expect(screen.getByRole("button", { name: "proof-5" })).toBeTruthy();
    fullTask = { ...task, version: 2, result: "Updated complete result. ".repeat(20), evidence: [...evidence, "proof-6"] };
    const next = { ...snapshot, tasks: [{ ...fullTask, result: fullTask.result.slice(0, 200), evidence: evidence.slice(0, 3) }] };
    rerender(<Inspector snapshot={next} />);
    await screen.findByText(fullTask.result.trim());
    expect(screen.getByRole("button", { name: "proof-6" })).toBeTruthy();
    expect(detailReads).toBe(2);
  });
  it("follows dependencies beyond the current bounded page using scoped record lookup", async () => {
    const paths: string[] = [];
    vi.stubGlobal("fetch", async (path: string) => {
      paths.push(path);
      const id = path.split("/").at(-1);
      const body = path.includes("/record/") ? { team_id: "alpha", id, title: id === "late-task" ? "Late milestone" : "Original research", dependencies: id === "late-task" ? ["first-task"] : [], evidence: [] } : [];
      return new Response(JSON.stringify(body));
    });
    render(<Inspector />);
    await screen.findByText("Late milestone");
    fireEvent.click(screen.getByRole("button", { name: "first-task" }));
    await screen.findByText("Original research");
    expect(paths).toContain("/api/swarm/teams/alpha/tasks/record/first-task");
  });
  it("bounds artifact preview, renders content as text, and publishes once with retry identity", async () => {
    const published: Record<string, unknown>[] = []; let canceled = false;
    vi.stubGlobal("fetch", async (path: string, init?: RequestInit) => {
      if (path.endsWith("/publish")) {
        published.push(JSON.parse(String(init?.body)));
        return new Response(JSON.stringify({ id: "published-a" }));
      }
      if (path.endsWith("/artifacts/a")) {
        return new Response(new ReadableStream({
          start(controller) { controller.enqueue(new TextEncoder().encode("<script>unsafe()</script>" + "e".repeat(200000))); },
          cancel() { canceled = true; },
        }));
      }
      return new Response(JSON.stringify([{ team_id: "alpha", id: "a", title: "Verified artifact", task_id: "task-a" }]));
    });
    render(<Inspector kind="artifacts" selected="a" />);
    await screen.findByRole("button", { name: "Open" });
    fireEvent.click(screen.getByRole("button", { name: "Open" }));
    await screen.findByText(/Preview limited to 100 KB/);
    expect(canceled).toBe(true);
    expect(document.querySelector("script")).toBeNull();
    expect(document.querySelector("pre")!.textContent!.length).toBeLessThan(100100);
    fireEvent.click(screen.getByRole("button", { name: "Publish selected artifact" }));
    await screen.findByText("Artifact published");
    fireEvent.click(screen.getByRole("button", { name: "Publish selected artifact" }));
    await waitFor(() => expect(published).toHaveLength(2));
    expect(published[0]).toEqual(published[1]);
    expect(published[0]).toMatchObject({ artifact_id: "a", destination: "artifact", expected_version: 1 });
  });
});
