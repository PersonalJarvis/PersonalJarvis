import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CreateTeamForm } from "./CreateTeamForm";
import { preparationFixture, teamFixture } from "./testFixtures";
import type { TeamCreate } from "./types";

vi.mock("@/i18n", () => ({ useUiLanguage: () => "en" }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("goal-only Swarm entry", () => {
  it("retains execution mode in form submissions and prevents selecting unavailable distributed capacity", async () => {
    const submit = vi.fn(async (_spec: TeamCreate) => teamFixture());
    const { rerender } = render(<CreateTeamForm capability={null} onCreated={() => {}} onSubmitTeam={submit} />);
    fireEvent.change(screen.getByLabelText("What should this team accomplish?"), { target: { value: "Review a public dataset." } });
    fireEvent.click(screen.getByText("Options · budget and access", { selector: "summary" }));
    const mode = screen.getByRole("combobox", { name: "Execution mode" });
    expect(mode.getAttribute("data-value")).toBe("auto");
    fireEvent.keyDown(mode, { key: "ArrowDown" });
    const options = screen.getByRole("listbox", { name: "Execution mode" });
    const distributed = screen.getByRole("option", { name: "Distributed" });
    expect(distributed.getAttribute("aria-disabled")).toBe("true");
    fireEvent.click(distributed);
    expect(mode.getAttribute("data-value")).toBe("auto");
    fireEvent.keyDown(options, { key: "End" });
    fireEvent.keyDown(options, { key: "Enter" });
    expect(mode.getAttribute("data-value")).toBe("local");
    expect(document.activeElement).toBe(mode);
    fireEvent.click(screen.getByRole("button", { name: "Clarify the goal" }));
    await waitFor(() => expect(submit).toHaveBeenCalledOnce());
    expect(submit.mock.calls[0][0]).toMatchObject({ mode: "local", limits: { concurrency: 32 } });

    rerender(<CreateTeamForm capability={{ distributed: { available: true } }} onCreated={() => {}} onSubmitTeam={submit} />);
    fireEvent.click(mode);
    fireEvent.click(screen.getByRole("option", { name: "Distributed" }));
    fireEvent.click(screen.getByRole("button", { name: "Clarify the goal" }));
    await waitFor(() => expect(submit).toHaveBeenCalledTimes(2));
    expect(submit.mock.calls[1][0]).toMatchObject({ mode: "distributed", limits: { concurrency: 1000 } });
    expect(submit.mock.calls[1][0].request_key).not.toBe(submit.mock.calls[0][0].request_key);
  });

  it("prepares a natural goal without starting execution and retains its identity on retry", async () => {
    const onCreated = vi.fn();
    const requests: { path: string; body: Record<string, unknown> }[] = [];
    vi.stubGlobal("fetch", async (path: string, init: RequestInit) => {
      requests.push({ path, body: JSON.parse(String(init.body)) });
      if (requests.length === 1) throw new TypeError("Connection interrupted during clarification");
      return new Response(JSON.stringify(preparationFixture("new-goal")));
    });
    const { rerender } = render(<CreateTeamForm capability={null} onCreated={onCreated} onClose={() => {}} />);
    fireEvent.change(screen.getByLabelText("What should this team accomplish?"), { target: { value: "Compare three approaches and save a verified report." } });
    expect(screen.getByText("Options · budget and access").parentElement?.hasAttribute("open")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Clarify the goal" }));
    await screen.findByRole("alert");
    const spec = requests[0].body as unknown as TeamCreate;
    expect(spec.name).toBe(spec.goal);
    expect(spec.acceptance).toBe("");
    expect(spec.tasks).toEqual([]);
    expect(spec.preparation_required).toBe(true);
    expect(spec.limits.worker_limit).toBe("1000");
    expect(spec.limits.concurrency).toBe(32);
    expect(spec.limits.monetary_limit_microusd).toBeNull();
    rerender(<CreateTeamForm capability={{ distributed: { available: true } }} onCreated={onCreated} onClose={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Clarify the goal" }));
    await waitFor(() => expect(onCreated).toHaveBeenCalledOnce());
    expect(requests.map(row => row.path)).toEqual(["/api/swarm/preparations", "/api/swarm/preparations"]);
    expect(requests[1].body).toEqual(requests[0].body);
    expect(requests[1].body.mode).toBe("local");
    expect(onCreated.mock.calls[0][0].state).toBe("created");
  });

  it("imports a dropped prompt and keeps it when a later file is invalid", async () => {
    const submit = vi.fn(async () => teamFixture());
    render(<CreateTeamForm capability={null} onCreated={() => {}} onClose={() => {}} onSubmitTeam={submit} />);
    const form = screen.getByRole("form", { name: "New team" });
    const goal = screen.getByLabelText("What should this team accomplish?") as HTMLTextAreaElement;
    fireEvent.drop(form, { dataTransfer: { files: [new File(["Investigate the problem.\nSave reproducible evidence."], "goal.md", { type: "text/markdown" })] } });
    await screen.findByText("Prompt loaded: goal.md");
    expect(goal.value).toBe("Investigate the problem.\nSave reproducible evidence.");
    fireEvent.change(screen.getByLabelText("Choose or drop a prompt file (.txt or .md)"), { target: { files: [new File(["bad\u0000payload"], "invalid.txt")] } });
    await screen.findByRole("alert");
    expect(goal.value).toBe("Investigate the problem.\nSave reproducible evidence.");
    expect(submit).not.toHaveBeenCalled();
  });
});
