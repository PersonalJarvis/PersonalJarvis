import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SpecialistAssignments, activeSourceProfiles } from "./SpecialistAssignments";
import { agentFixture, teamFixture } from "./testFixtures";
import type { AgentRecord } from "./types";

vi.mock("@/i18n", () => ({ useUiLanguage: () => "en" }));
const source = { agent_id: "researcher", name: "Researcher", title: "Evidence review", state: "active", focus: ["Public evidence"], workspace_dir: "/private", history: ["Never copy"], grants: ["all"] };
const roster = { agents: [source, { ...source, agent_id: "paused", name: "Paused source", state: "paused" }, { ...source, agent_id: "archived", name: "Archived source", state: "archived" }], total: 3 };
const members: AgentRecord[] = [];
const requests: { path: string; method: string; body?: Record<string, unknown> }[] = [];
let rosterDown = false;
beforeEach(() => {
  requests.length = 0; members.length = 0; rosterDown = false;
  vi.stubGlobal("fetch", async (path: string, init?: RequestInit) => {
    const url = new URL(path, "http://localhost");
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    requests.push({ path: url.pathname, method, body });
    const send = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
    if (url.pathname === "/api/society/agents") return rosterDown ? send({ detail: "Ordinary roster is offline" }, 503) : send(roster);
    if (method === "POST") {
      const member: AgentRecord = { ...agentFixture("alpha", "team-local-worker"), name: "Researcher", role: "worker", state: "idle", source_agent_id: body.source_agent_id };
      members.push(member); return send(member);
    }
    if (method === "DELETE") { members[0].state = "stopped"; return send(members[0]); }
    return send([agentFixture(), ...members]);
  });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("explicit team-scoped specialist assignments", () => {
  it("loads on demand and only projects active source identity fields", async () => {
    render(<SpecialistAssignments team={teamFixture()} onChanged={() => {}} />);
    expect(requests).toHaveLength(0);
    fireEvent.click(screen.getByText("Specialist assignments", { selector: "summary" }));
    await screen.findByRole("option", { name: "Researcher · Evidence review" });
    expect(screen.queryByRole("option", { name: /Paused source/ })).toBeNull();
    expect(screen.queryByRole("option", { name: /Archived source/ })).toBeNull();
    expect(activeSourceProfiles(roster)).toEqual([{ id: "researcher", name: "Researcher", title: "Evidence review", focus: ["Public evidence"] }]);
    expect(requests.every(item => item.method === "GET")).toBe(true);
  });
  it("transfers only explicit input and revokes the team identity without mutating its source", async () => {
    const changed = vi.fn();
    render(<SpecialistAssignments team={{ ...teamFixture(), storage_generation: "restored-generation" }} onChanged={changed} />);
    fireEvent.click(screen.getByText("Specialist assignments", { selector: "summary" }));
    await screen.findByRole("option", { name: "Researcher · Evidence review" });
    const form = screen.getByRole("form", { name: "Assign to this team" });
    fireEvent.change(within(form).getByLabelText("Choose an active specialist"), { target: { value: "researcher" } });
    fireEvent.change(within(form).getByLabelText("Input authorized for this team"), { target: { value: "Exact approved input\n  with whitespace" } });
    fireEvent.click(within(form).getByRole("button", { name: "Assign to this team" }));
    await screen.findByText("Specialist assigned with the approved input.");
    const assignment = requests.find(item => item.method === "POST")!;
    expect(assignment.path).toBe("/api/swarm/teams/alpha/specialists");
    expect(assignment.body).toEqual({ source_agent_id: "researcher", authorized_input: "Exact approved input\n  with whitespace", request_key: expect.any(String), expected_version: 1, expected_storage_generation: "restored-generation" });
    const assigned = await screen.findByRole("article", { name: "Researcher" });
    fireEvent.click(within(assigned).getByRole("button", { name: "Remove from this team" }));
    await screen.findByText("Team membership revoked. Personal records are unchanged.");
    expect(requests.filter(item => item.method === "DELETE")).toEqual([{ path: "/api/swarm/teams/alpha/specialists/team-local-worker", method: "DELETE", body: undefined }]);
    expect(requests.filter(item => item.path.startsWith("/api/society")).every(item => item.method === "GET")).toBe(true);
    expect(changed).toHaveBeenCalledTimes(2);
  });
  it("keeps existing assignments revocable when the ordinary roster is unavailable", async () => {
    rosterDown = true;
    members.push({ ...agentFixture("alpha", "existing"), name: "Researcher", role: "worker", source_agent_id: "researcher" });
    render(<SpecialistAssignments team={teamFixture()} onChanged={() => {}} />);
    fireEvent.click(screen.getByText("Specialist assignments", { selector: "summary" }));
    await screen.findByRole("alert");
    fireEvent.click(await screen.findByRole("button", { name: "Remove from this team" }));
    await waitFor(() => expect(requests.some(item => item.method === "DELETE")).toBe(true));
    expect(screen.queryByRole("option", { name: "Researcher · Evidence review" })).toBeNull();
  });
});
