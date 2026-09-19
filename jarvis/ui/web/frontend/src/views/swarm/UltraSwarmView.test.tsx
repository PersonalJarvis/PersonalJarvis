import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { UltraSwarmView } from "./UltraSwarmView";
import { preparationFixture, snapshotFixture, SwarmSocketFake, unavailableTeamFixture } from "@/components/swarm/testFixtures";
import type { TeamCreate, TeamUnavailable, WorldSnapshot } from "@/components/swarm/types";

vi.mock("@/i18n", () => ({ useUiLanguage: () => "en" }));
const snapshots = new Map<string, WorldSnapshot>();
const unavailableTeams = new Map<string, TeamUnavailable>();
const mutations: { path: string; body: Record<string, unknown> }[] = [];
let created: TeamCreate | undefined;
beforeEach(() => {
  window.history.replaceState({}, "", "/?view=ultra-swarm&swarm_team=alpha");
  snapshots.clear(); snapshots.set("alpha", snapshotFixture("alpha")); snapshots.set("beta", snapshotFixture("beta"));
  unavailableTeams.clear();
  created = undefined; mutations.length = 0; SwarmSocketFake.all = [];
  vi.stubGlobal("WebSocket", SwarmSocketFake);
  vi.stubGlobal("WebGLRenderingContext", undefined);
  vi.stubGlobal("fetch", async (path: string, init?: RequestInit) => {
    const url = new URL(String(path), "http://localhost");
    const send = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
    if (url.pathname.endsWith("/capabilities")) return send({ local: true, sandbox: { available: true }, providers: [{ available: true }], distributed: { available: false } });
    if (url.pathname === "/api/swarm/requests") return send([]);
    if (url.pathname === "/api/swarm/restores") return send({ backups: [], pending_restores: [] });
    if (url.pathname === "/api/swarm/preparations") {
      created = JSON.parse(String(init?.body)) as TeamCreate;
      const preparation = preparationFixture("gamma");
      preparation.team = { ...preparation.team, name: created.name, goal: created.goal, limits: created.limits, policy: created.policy };
      snapshots.set("gamma", { ...snapshotFixture("gamma"), team: preparation.team });
      return send(preparation);
    }
    if (url.pathname === "/api/swarm/teams") {
      if (init?.method === "POST") {
        created = JSON.parse(String(init.body)) as TeamCreate;
        const next = snapshotFixture("gamma"); next.team.name = created.name; next.team.goal = created.goal; next.team.state = "created"; snapshots.set("gamma", next);
        return send(next.team);
      }
      return send([...Array.from(snapshots.values(), snapshot => snapshot.team), ...unavailableTeams.values()]);
    }
    const parts = url.pathname.split("/"); const id = parts[4]; const action = parts[5];
    if (action === "storage") return send({ backups: [], pending_restores: [] });
    if (unavailableTeams.has(id)) {
      if (action === "recover" && init?.method === "POST") {
        unavailableTeams.delete(id); snapshots.set(id, snapshotFixture(id));
      } else return send({ detail: unavailableTeams.get(id)!.error }, 503);
    }
    const snapshot = snapshots.get(id);
    if (!snapshot) return send({ detail: "Unknown team" }, 404);
    if (!action) return send(snapshot.team);
    if (action === "preparation") return send({ ...preparationFixture(id), team: snapshot.team });
    if (init?.method === "POST") {
      const body = init.body ? JSON.parse(String(init.body)) as Record<string, unknown> : {};
      mutations.push({ path: url.pathname, body });
      if (action === "backup") return send({ backup_id: "backup-a", created_at: 1, size_bytes: "512", download_url: `/api/swarm/teams/${id}/backups/backup-a` });
      const states = { start: "running", pause: "paused", resume: "running", stop: "canceled", cancel: "canceled", archive: "archived" } as const;
      if (action in states) { snapshot.team.state = states[action as keyof typeof states]; snapshot.team.version += 1; }
      return send(snapshot.team);
    }
    if (action === "world") {
      if (url.searchParams.get("group")) return send({ ...snapshot, aggregated: false, groups: [] });
      return send(snapshot);
    }
    if (action === "agents") return send(parts[6] === "record" ? snapshot.agents.find(agent => agent.id === parts[7]) : snapshot.agents);
    if (action === "tasks") return send([]);
    return send([]);
  });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("Swarm product controls without WebGL", () => {
  it("keeps a corrupt team visible and recoverable without displaying another team's world", async () => {
    snapshots.delete("beta"); unavailableTeams.set("beta", unavailableTeamFixture());
    render(<UltraSwarmView />);
    await screen.findAllByText("alpha-lead");
    const sidebar = screen.getByRole("complementary", { name: "Teams" });
    fireEvent.change(within(sidebar).getByLabelText("Filter by status"), { target: { value: "active" } });
    const unavailable = within(sidebar).getByRole("button", { name: "Team beta Unavailable" });
    expect(unavailable.querySelector("[data-state]")).toBeNull();
    fireEvent.click(unavailable);
    const workspace = screen.getByRole("main");
    await within(workspace).findByText(/Current status and world data are unknown/);
    await within(workspace).findByRole("alert");
    expect(within(workspace).getByRole("heading", { name: "Team beta" })).toBeTruthy();
    expect(within(workspace).queryByRole("region", { name: "Live teamwork" })).toBeNull();
    expect(within(workspace).queryByRole("button", { name: "Pause" })).toBeNull();
    expect(screen.queryAllByText("alpha-lead")).toHaveLength(0);
    expect(within(workspace).getByText("Storage & recovery", { selector: "summary" }).parentElement?.hasAttribute("open")).toBe(true);
    fireEvent.click(within(workspace).getByRole("button", { name: "Recover storage" }));
    await screen.findAllByText("beta-lead");
    expect(mutations.at(-1)?.path).toBe("/api/swarm/teams/beta/recover");
    expect(screen.queryByText(/Current status and world data are unknown/)).toBeNull();
    expect(within(sidebar).getByRole("button", { name: "Team beta Running" })).toBeTruthy();
  });
  it("opens evidence and publication inspectors from the accessible map's exact counts", async () => {
    snapshots.get("alpha")!.counts = { agents: "1", tasks: "0", artifacts: "9007199254740993", publications: "2" };
    render(<UltraSwarmView />);
    const memory = await screen.findByRole("group", { name: "Team memory" });
    fireEvent.click(within(memory).getByRole("button", { name: `Evidence & artifacts: ${BigInt("9007199254740993").toLocaleString()}` }));
    const inspector = screen.getByRole("region", { name: "Inspect" });
    expect(within(inspector).getByRole("button", { name: "Evidence & artifacts" }).getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(within(memory).getByRole("button", { name: "Published results: 2" }));
    expect(within(inspector).getByRole("button", { name: "Published results" }).getAttribute("aria-pressed")).toBe("true");
  });
  it("restores the selected team, displays exact budget and controls pause/resume/stop/archive", async () => {
    render(<UltraSwarmView />);
    await screen.findByRole("button", { name: "Pause" });
    expect(await screen.findByRole("region", { name: "Live teamwork" })).toBeTruthy();
    expect(document.querySelector("canvas")).toBeNull();
    expect(screen.getByText(`${BigInt("10000000000").toLocaleString()} / ${BigInt("100000000000").toLocaleString()}`)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Pause" }));
    await screen.findByRole("button", { name: "Resume" });
    expect(mutations[0]).toEqual({ path: "/api/swarm/teams/alpha/pause", body: { expected_version: 1, expected_storage_generation: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Resume" }));
    await screen.findByRole("button", { name: "Pause" });
    fireEvent.click(screen.getByRole("button", { name: "Stop" }));
    await screen.findByRole("button", { name: "Archive" });
    fireEvent.click(screen.getByRole("button", { name: "Archive" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Archive" })).toBeNull());
    expect(screen.getByText("Completed team view · no live workers")).toBeTruthy();
  });
  it("switches team URL and roster without leaking the prior team state", async () => {
    render(<UltraSwarmView />);
    await screen.findAllByText("alpha-lead");
    const teams = screen.getByRole("complementary", { name: "Teams" });
    fireEvent.click(within(teams).getByRole("button", { name: /Team beta/ }));
    await screen.findAllByText("beta-lead");
    expect(screen.queryAllByText("alpha-lead")).toHaveLength(0);
    expect(window.location.search).toContain("swarm_team=beta");
    expect(screen.getByRole("link", { name: "Open team separately" }).getAttribute("href")).toContain("swarm_team=beta");
    expect(SwarmSocketFake.all.filter(socket => socket.url.pathname.includes("alpha")).every(socket => socket.closed)).toBe(true);
  });
  it("creates a team with an exact 10-billion-token budget and explicit offline policy", async () => {
    render(<UltraSwarmView />);
    fireEvent.click(screen.getByRole("button", { name: "New team" }));
    const form = screen.getByRole("form", { name: "New team" });
    fireEvent.click(within(form).getByText("Options · budget and access", { selector: "summary" }));
    fireEvent.change(within(form).getByLabelText("Team name"), { target: { value: "Three reviewers" } });
    fireEvent.change(within(form).getByLabelText("What should this team accomplish?"), { target: { value: "Review an offline dataset" } });
    fireEvent.change(within(form).getByLabelText("What must the final result demonstrate?"), { target: { value: "Verified totals" } });
    fireEvent.change(within(form).getByLabelText("Token budget"), { target: { value: "10000000000" } });
    fireEvent.change(within(form).getByLabelText("Spend limit (USD, optional)"), { target: { value: "12.123456" } });
    fireEvent.click(within(form).getByLabelText("Internet access"));
    fireEvent.click(within(form).getByRole("button", { name: "Clarify the goal" }));
    await screen.findByRole("region", { name: "Prepare your swarm" });
    expect(created?.limits.token_budget).toBe("10000000000");
    expect(created?.limits.monetary_limit_microusd).toBe("12123456");
    expect(created?.policy.internet).toBe(false);
    expect(created?.acceptance).toBe("Verified totals");
    expect(created?.tasks).toEqual([]);
    expect(window.location.search).toContain("swarm_team=gamma");
    expect(mutations.some(item => item.path.endsWith("/start") || item.path.endsWith("/launch"))).toBe(false);
    expect(screen.queryByRole("button", { name: "Start" })).toBeNull();
  });
  it("drills down a bounded aggregate group and keeps storage recovery available", async () => {
    const snapshot = snapshots.get("alpha")!;
    snapshot.aggregated = true; snapshot.has_more = true;
    snapshot.groups = [{ id: "research", title: "Research", agents: "1000000", counts: { running: "1000" }, level: 2 }];
    render(<UltraSwarmView />);
    fireEvent.click(await screen.findByRole("button", { name: /Research/ }));
    await screen.findByRole("button", { name: "All groups" });
    await screen.findAllByText("alpha-lead");
    fireEvent.click(screen.getByText("Storage & recovery", { selector: "summary" }));
    fireEvent.click(screen.getByRole("button", { name: "Create backup" }));
    await screen.findByText("Backup created");
    fireEvent.click(screen.getByRole("button", { name: "Recover storage" }));
    await screen.findByText("Storage recovery completed");
    expect(mutations.map(item => item.path)).toEqual(["/api/swarm/teams/alpha/backup", "/api/swarm/teams/alpha/recover"]);
  });
});
