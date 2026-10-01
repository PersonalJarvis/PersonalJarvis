import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SwarmRequests, type SwarmProposal } from "./SwarmRequests";
import { preparationFixture, teamFixture } from "./testFixtures";
import type { TeamCreate } from "./types";

vi.mock("@/i18n", () => ({ useUiLanguage: () => "en" }));
const proposals: SwarmProposal[] = [];
const writes: { path: string; body: unknown }[] = [];
let interruptApproval = false;
const send = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
function savedSpec(): TeamCreate {
  const team = teamFixture();
  return { name: "Already approved name", goal: "Approved goal", acceptance: "Approved check", mode: "local", request_key: "proposal:request-a", limits: { ...team.limits, token_budget: "9007199254740993", runtime_seconds: 121 }, policy: { ...team.policy, internet: false, tools: ["run_javascript"] }, tasks: [] };
}
beforeEach(() => {
  writes.length = 0; interruptApproval = false;
  proposals.splice(0, proposals.length, { id: "request-a", source_agent_id: "researcher", source_name: "Researcher", created_at: 1, state: "pending", team_id: null, brief: { name: "Proposed research", goal: "Check this table", acceptance: "Verify totals", authorized_input: "Only this explicit input\n  retain whitespace" } });
  vi.stubGlobal("fetch", async (path: string, init?: RequestInit) => {
    const url = new URL(path, "http://localhost");
    if (!init?.method || init.method === "GET") return send(proposals);
    const body = init.body ? JSON.parse(String(init.body)) : undefined;
    writes.push({ path: url.pathname, body });
    if (url.pathname.endsWith("/preparation")) return send(preparationFixture("created-team"));
    if (url.pathname.endsWith("/approve")) {
      if (interruptApproval) {
        interruptApproval = false; proposals[0].state = "approving"; proposals[0].approval_spec = body;
        return send({ detail: "Temporary service outage" }, 503);
      }
      proposals[0].state = "approved"; proposals[0].team_id = "created-team";
      return send({ ...teamFixture("created-team"), state: "created" });
    }
    proposals[0].state = "rejected";
    return send(proposals[0]);
  });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("owner review of ordinary specialist proposals", () => {
  it("prefills the brief but requires explicit budget approval and never starts work", async () => {
    const created = vi.fn();
    render(<SwarmRequests capability={null} onCreated={created} onSelect={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Review proposal" }));
    expect(writes).toHaveLength(0);
    const form = screen.getByRole("form", { name: "Approve proposal" });
    expect((within(form).getByLabelText("Team name") as HTMLInputElement).value).toBe("Proposed research");
    expect((within(form).getByLabelText("What should this team accomplish?") as HTMLTextAreaElement).value).toBe("Check this table");
    expect(screen.getAllByText(/Only this explicit input/).at(-1)?.textContent).toBe(proposals[0].brief.authorized_input);
    fireEvent.change(within(form).getByLabelText("Token budget"), { target: { value: "10000000000" } });
    fireEvent.change(within(form).getByLabelText("Spend limit (USD, optional)"), { target: { value: "12.123456" } });
    fireEvent.click(within(form).getByLabelText("Internet access"));
    fireEvent.click(within(form).getByRole("button", { name: "Approve and create team" }));
    await waitFor(() => expect(created).toHaveBeenCalledOnce());
    const spec = writes[0].body as TeamCreate;
    expect(writes.map(write => write.path)).toEqual(["/api/swarm/requests/request-a/approve", "/api/swarm/teams/created-team/preparation"]);
    expect(spec.limits.token_budget).toBe("10000000000");
    expect(spec.limits.monetary_limit_microusd).toBe("12123456");
    expect(spec.policy.internet).toBe(false);
    expect(spec.tasks).toEqual([]);
    expect(spec.preparation_required).toBe(true);
    expect(created.mock.calls[0][0].state).toBe("created");
  });
  it("rejects a proposal without creating a team", async () => {
    const created = vi.fn();
    render(<SwarmRequests capability={null} onCreated={created} onSelect={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Reject proposal" }));
    await screen.findByText("Proposal rejected.");
    expect(writes).toEqual([{ path: "/api/swarm/requests/request-a/reject", body: undefined }]);
    expect(created).not.toHaveBeenCalled();
  });
  it("resumes an interrupted approval with every saved field unchanged", async () => {
    const spec = savedSpec(); const before = JSON.stringify(spec);
    proposals[0].state = "approving"; proposals[0].approval_spec = spec;
    const created = vi.fn();
    render(<SwarmRequests capability={null} onCreated={created} onSelect={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Resume approval" }));
    await waitFor(() => expect(created).toHaveBeenCalledOnce());
    expect(JSON.stringify(writes[0].body)).toBe(before);
    expect(screen.queryByRole("form", { name: "Approve proposal" })).toBeNull();
  });
  it("locks a partially approved form to the durable recovery spec after a failure", async () => {
    interruptApproval = true;
    const created = vi.fn();
    render(<SwarmRequests capability={null} onCreated={created} onSelect={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Review proposal" }));
    fireEvent.click(screen.getByRole("button", { name: "Approve and create team" }));
    fireEvent.click(await screen.findByRole("button", { name: "Resume approval" }));
    await waitFor(() => expect(created).toHaveBeenCalledOnce());
    expect(writes[1].body).toEqual(writes[0].body);
    expect(screen.queryByRole("form", { name: "Approve proposal" })).toBeNull();
  });
});
