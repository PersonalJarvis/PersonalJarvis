import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SwarmPreparation } from "./SwarmPreparation";
import { preparationFixture } from "./testFixtures";

vi.mock("@/i18n", () => ({ useUiLanguage: () => "en" }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("clarification and approved-plan launch", () => {
  it("retries a transient busy-draft read failure without relaunching inference", async () => {
    vi.useFakeTimers();
    let gets = 0;
    const view = { ...preparationFixture(), busy: true };
    vi.stubGlobal("fetch", async (_path: string, init?: RequestInit) => {
      expect(init?.method ?? "GET").toBe("GET");
      gets += 1;
      if (gets === 2) throw new TypeError("Transient connection loss");
      return new Response(JSON.stringify(gets === 1 ? view : preparationFixture("alpha", true)));
    });
    try {
      render(<SwarmPreparation team={view.team} awake onChanged={() => {}} />);
      await act(async () => { await vi.advanceTimersByTimeAsync(0); });
      expect(gets).toBe(1);
      await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
      expect(gets).toBe(2);
      expect(screen.getByRole("alert").textContent).toContain("Transient connection loss");
      await act(async () => { await vi.advanceTimersByTimeAsync(5100); });
      expect(gets).toBe(3);
      expect(screen.getByRole("button", { name: "Approve plan and start swarm" })).toBeTruthy();
      expect(screen.queryByRole("alert")).toBeNull();
    } finally { cleanup(); vi.useRealTimers(); }
  });
  it("asks questions, shows the saved plan, and sends only its approval references on launch", async () => {
    let view = preparationFixture();
    const writes: { path: string; body: Record<string, unknown> }[] = [];
    const onChanged = vi.fn();
    vi.stubGlobal("fetch", async (path: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        const body = JSON.parse(String(init.body)); writes.push({ path, body });
        view = path.endsWith("/answers") ? { ...preparationFixture("alpha", true), answers: body.answers }
          : { ...view, state: "launched", team: { ...view.team, state: "running" } };
      }
      return new Response(JSON.stringify(view));
    });
    render(<SwarmPreparation team={view.team} awake onChanged={onChanged} />);
    await screen.findByLabelText("Which deliverable should be saved?");
    expect(writes).toHaveLength(0);
    expect(screen.queryByRole("button", { name: "Approve plan and start swarm" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "JSON file" }));
    fireEvent.click(screen.getByRole("button", { name: "Create plan" }));
    await screen.findByText("Calculate, verify and save the result.");
    expect(screen.getByText("No external publishing")).toBeTruthy();
    expect(writes).toHaveLength(1);
    expect(writes[0].body).toMatchObject({ expected_revision: 1, expected_storage_generation: "", answers: { deliverable: "JSON file" } });
    fireEvent.click(screen.getByRole("button", { name: "Approve plan and start swarm" }));
    await screen.findByText("Plan approved. The swarm has started.");
    expect(writes[1]).toEqual({ path: "/api/swarm/teams/alpha/launch", body: { expected_revision: 2, expected_storage_generation: "", digest: "a".repeat(64), request_key: expect.any(String) } });
    expect(onChanged.mock.calls.at(-1)?.[0].state).toBe("running");
  });

  it("restores a ready draft without launching and hides approval while answers are edited", async () => {
    const view = preparationFixture("alpha", true);
    const writes: string[] = [];
    vi.stubGlobal("fetch", async (path: string, init?: RequestInit) => {
      if (init?.method === "POST") writes.push(path);
      return new Response(JSON.stringify(view));
    });
    render(<SwarmPreparation team={view.team} awake onChanged={() => {}} />);
    await screen.findByRole("button", { name: "Approve plan and start swarm" });
    expect(writes).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Change answers" }));
    fireEvent.change(screen.getByLabelText("Which deliverable should be saved?"), { target: { value: "A new report" } });
    expect(screen.queryByRole("button", { name: "Approve plan and start swarm" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Back to the plan" }));
    fireEvent.click(screen.getByRole("button", { name: "Change answers" }));
    expect((screen.getByLabelText("Which deliverable should be saved?") as HTMLTextAreaElement).value).toBe("JSON file");
    expect(writes).toHaveLength(0);
  });

  it("keeps the same launch identity after an uncertain response", async () => {
    const view = preparationFixture("alpha", true);
    const writes: Record<string, unknown>[] = [];
    vi.stubGlobal("fetch", async (_path: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        writes.push(JSON.parse(String(init.body)));
        if (writes.length === 1) throw new TypeError("Connection interrupted");
        return new Response(JSON.stringify({ ...view, state: "launched", team: { ...view.team, state: "running" } }));
      }
      return new Response(JSON.stringify(view));
    });
    render(<SwarmPreparation team={view.team} awake onChanged={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Approve plan and start swarm" }));
    await screen.findByRole("alert");
    fireEvent.click(screen.getByRole("button", { name: "Approve plan and start swarm" }));
    await screen.findByText("Plan approved. The swarm has started.");
    expect(writes[0]).toEqual(writes[1]);
  });

  it("does not replace a newer plan with an older overlapping GET", async () => {
    let gets = 0;
    let release: (value: Response) => void = () => {};
    const oldView = preparationFixture();
    vi.stubGlobal("fetch", async (_path: string, init?: RequestInit) => {
      if (init?.method === "POST") return new Response(JSON.stringify(preparationFixture("alpha", true)));
      gets += 1;
      if (gets === 2) return new Promise<Response>(resolve => { release = resolve; });
      return new Response(JSON.stringify(oldView));
    });
    render(<SwarmPreparation team={oldView.team} awake onChanged={() => {}} />);
    await screen.findByLabelText("Which deliverable should be saved?");
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(gets).toBe(2));
    fireEvent.click(screen.getByRole("button", { name: "JSON file" }));
    fireEvent.click(screen.getByRole("button", { name: "Create plan" }));
    await screen.findByRole("button", { name: "Approve plan and start swarm" });
    release(new Response(JSON.stringify(oldView)));
    await waitFor(() => expect(screen.getByRole("button", { name: "Approve plan and start swarm" })).toBeTruthy());
    expect(screen.queryByLabelText("Which deliverable should be saved?")).toBeNull();
  });

  it("rejects another team's plan instead of displaying or approving it", async () => {
    vi.stubGlobal("fetch", async () => new Response(JSON.stringify(preparationFixture("foreign", true))));
    render(<SwarmPreparation team={preparationFixture().team} awake onChanged={() => {}} />);
    await screen.findByRole("alert");
    expect(screen.queryByText("Calculate, verify and save the result.")).toBeNull();
    expect(screen.queryByRole("button", { name: "Approve plan and start swarm" })).toBeNull();
  });
});
