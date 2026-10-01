import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, test, vi } from "vitest";
import { loadLocaleChunk } from "@/i18n";
import type { SocietyAgent } from "../data";
import type { SocietyChatGroup } from "@/lib/societyChatGroups";
import { buildOfficeLayout } from "./officeLayout";
import { useOfficeStore } from "./officeStore";
import { TeamRoomPanel } from "./TeamRoomPanel";

const agents = [
  { agentId: "a1", name: "Ada Lovelace", title: "Research", state: "idle", palette: { primary: "#4f7cac" } },
  { agentId: "a2", name: "Bo", title: "Writer", state: "working", palette: { primary: "#c8553d" } },
  { agentId: "a3", name: "Cy", title: "", state: "idle", palette: { primary: "#5e9c76" } },
] as SocietyAgent[];
const layout = buildOfficeLayout(agents.map((a, i) => ({
  agentId: a.agentId, name: a.name, tier: "specialist" as const, providerLabel: "Codex", state: a.state, createdMs: i,
})));

beforeAll(async () => { await loadLocaleChunk("society"); });

let groups: SocietyChatGroup[];
const calls: { method: string; url: string; body: unknown }[] = [];

beforeEach(() => {
  groups = [{ group_id: "g1", name: "Launch crew", members: ["a1", "a2"], created_ms: 1, updated_ms: 1 }];
  calls.length = 0;
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : null;
    calls.push({ method, url, body });
    if (method === "POST") {
      const group = { group_id: "g2", name: body.name, members: body.members, created_ms: 2, updated_ms: 2 };
      groups = [...groups, group];
      return new Response(JSON.stringify({ group }));
    }
    if (method === "PATCH") {
      groups = groups.map((g) => (url.endsWith(g.group_id) ? { ...g, name: body.name, members: body.members } : g));
      return new Response(JSON.stringify({ group: groups.find((g) => url.endsWith(g.group_id)) }));
    }
    if (method === "DELETE") {
      groups = groups.filter((g) => !url.endsWith(g.group_id));
      return new Response("{}");
    }
    return new Response(JSON.stringify({ groups }));
  });
  useOfficeStore.setState({ meeting: null, summons: {}, teamDraft: [] });
});

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function mount(onOpenGroup = vi.fn()) {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <TeamRoomPanel agents={agents} layout={layout} sample={false} actions={{ onOpenAgent: vi.fn(), onOpenLedger: vi.fn(), onOpenGroup }} />
    </QueryClientProvider>,
  );
  return onOpenGroup;
}

describe("team room panel", () => {
  test("shows each team with its members and seats them at the table on Meet", async () => {
    mount();
    expect(await screen.findByText("Launch crew")).toBeTruthy();
    expect(screen.getByText(/2 members · 1 working/)).toBeTruthy();
    expect(screen.getByTitle(/Ada Lovelace/).textContent).toContain("AL");

    fireEvent.click(screen.getByRole("button", { name: "Meet with Launch crew at the table" }));
    const { meeting, summons } = useOfficeStore.getState();
    expect(meeting?.groupId).toBe("g1");
    expect(summons.a1.spotId).toMatch(/^meeting-/);
    expect(summons.a2.spotId).toMatch(/^meeting-/);
    // The running meeting shows up top, and ending it releases everyone.
    expect(await screen.findByText("Meeting: Launch crew")).toBeTruthy();
    expect(screen.getByText(/Working, so they stay at their desk: Bo/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "End meeting" }));
    expect(useOfficeStore.getState().meeting).toBeNull();
    expect(useOfficeStore.getState().summons).toEqual({});
  });

  test("renames a team and changes its members", async () => {
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    fireEvent.change(screen.getByDisplayValue("Launch crew"), { target: { value: "Crew two" } });
    fireEvent.click(screen.getAllByRole("checkbox", { name: /Cy/ })[0]);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ name: "Crew two", members: ["a1", "a2", "a3"] });
  });

  test("deletes a team only after a second, explicit click", async () => {
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete team" }));
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Really delete?" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.url.endsWith("/g1"))).toBe(true));
  });

  test("puts a new team together from the free agents and starts its meeting", async () => {
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "New team" }));
    fireEvent.click(screen.getByRole("button", { name: "All free agents" }));
    expect(screen.getByText("2 selected")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Create team and meet (2)" }));
    await waitFor(() => expect(useOfficeStore.getState().meeting?.groupId).toBe("g2"));
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({ name: "Ada Lovelace + Cy", members: ["a1", "a3"] });
    expect(useOfficeStore.getState().teamDraft).toEqual([]);
  });
});
