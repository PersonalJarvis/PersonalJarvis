import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import en from "@/i18n/locales/society/en.json";
import type { BrowserProfilesSnapshot } from "@/lib/browserProfiles";
import { BROWSER_PROFILE_CHANGED_EVENT, BROWSER_PROFILES_QUERY } from "@/lib/browserProfiles";
import BrowserProfilesDialog from "./BrowserProfilesDialog";

vi.mock("@/i18n", () => ({
  useLocaleChunk: () => true,
  useT: () => (key: string) => en.society.browser_profiles[key.replace("society.browser_profiles.", "") as keyof typeof en.society.browser_profiles] ?? key,
}));

let snapshot: BrowserProfilesSnapshot;
let calls: Array<{ path: string; method: string; body: Record<string, unknown> }>;
let failingStatus: number | null;

beforeEach(() => {
  snapshot = {
    profiles: [{ id: "personal", name: "Personal Chrome", kind: "chrome", connected: false,
      allowed_domains: ["x.com"], agent_ids: ["scout"], is_default: false }],
    default_profile_id: null,
    bindings: { scout: { mode: "profile", profile_id: "personal", effective_profile_id: "personal" },
      writer: { mode: "own", profile_id: null, effective_profile_id: null } },
    agents: [{ agent_id: "scout", name: "Scout" }, { agent_id: "writer", name: "Writer" }],
  };
  calls = [];
  failingStatus = null;
  vi.stubGlobal("fetch", vi.fn(async (path: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : {};
    calls.push({ path, method, body });
    if (method !== "GET" && failingStatus) return new Response("private provider error", { status: failingStatus });
    if (path.endsWith("/pair")) return Response.json({ pairing_code: "private-code", server_url: "http://127.0.0.1:8765", expires_in: 120 });
    if (method === "POST") {
      const profile = { id: "new", ...body, connected: false, agent_ids: [], is_default: false };
      snapshot = { ...snapshot, profiles: [...snapshot.profiles, profile] } as BrowserProfilesSnapshot;
      return Response.json(profile);
    }
    if (path.endsWith("/sharing")) {
      const all = body.scope === "all";
      const ids = all ? snapshot.agents.map((agent) => agent.agent_id) : body.agent_ids as string[];
      snapshot = {
        ...snapshot,
        default_profile_id: all ? "personal" : null,
        profiles: snapshot.profiles.map((profile) => profile.id === "personal" ? { ...profile, is_default: all, agent_ids: ids } : profile),
        bindings: Object.fromEntries(snapshot.agents.map(({ agent_id }) => [agent_id, {
          mode: all ? "inherit" : ids.includes(agent_id) ? "profile" : "own",
          profile_id: !all && ids.includes(agent_id) ? "personal" : null,
          effective_profile_id: ids.includes(agent_id) ? "personal" : null,
        }])),
      } as BrowserProfilesSnapshot;
    } else if (path.endsWith("/browser/profile")) {
      const id = path.split("/")[4];
      snapshot = { ...snapshot, bindings: { ...snapshot.bindings, [id]: {
        mode: body.mode, profile_id: body.profile_id,
        effective_profile_id: body.mode === "own" ? null : body.mode === "inherit" ? snapshot.default_profile_id : body.profile_id,
      } } } as BrowserProfilesSnapshot;
    } else if (method === "PATCH") {
      snapshot = { ...snapshot, profiles: snapshot.profiles.map((profile) => profile.id === "personal" ? { ...profile, ...body } : profile) } as BrowserProfilesSnapshot;
    } else if (method === "DELETE") {
      snapshot = { ...snapshot, profiles: [], bindings: {} };
    }
    return Response.json(snapshot);
  }));
});

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function mount(agentId?: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><BrowserProfilesDialog agentId={agentId} onClose={() => {}} /></QueryClientProvider>);
  return client;
}

async function ready(agentId?: string) {
  const client = mount(agentId);
  await screen.findByLabelText("Profile name");
  return client;
}

function mutations() { return calls.filter((call) => call.method !== "GET"); }

describe("browser profile sharing", () => {
  test("a removed assigned profile remains visibly disconnected until an explicit replacement is saved", async () => {
    snapshot.profiles = [];
    mount("scout");
    expect(await screen.findByText("Currently uses: Profile disconnected — choose a profile")).toBeTruthy();
    expect(screen.getByRole("combobox", { name: "Agent browser profile" }).textContent).toContain("Profile disconnected");
    expect(screen.queryByText("Currently uses: Own separate profile")).toBeNull();
    expect(mutations()).toEqual([]);
    fireEvent.click(screen.getByRole("combobox", { name: "Agent browser profile" }));
    fireEvent.click(await screen.findByRole("option", { name: "Own separate profile" }));
    expect(mutations()).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: "Save agent choice" }));
    await waitFor(() => expect(mutations()[0]?.body).toEqual({ mode: "own", profile_id: null }));
  });

  test("a missing default is not presented as an agent's own profile", async () => {
    snapshot.profiles = [];
    snapshot.default_profile_id = "revoked";
    snapshot.bindings.scout = { mode: "inherit", profile_id: null, effective_profile_id: "revoked" };
    mount("scout");
    expect(await screen.findByText("Currently uses: Profile disconnected — choose a profile")).toBeTruthy();
    expect(screen.getByRole("combobox", { name: "Agent browser profile" }).textContent).toContain("Use the default · Profile disconnected");
    expect(mutations()).toEqual([]);
  });

  test("opening the manager preserves existing assignments and reports disconnected Chrome", async () => {
    await ready("scout");
    expect(mutations()).toEqual([]);
    expect(screen.getByText(/Chrome disconnected/)).toBeTruthy();
    expect(screen.getByText(/Website.*expire a login/)).toBeTruthy();
    expect(screen.getByText("Currently uses: Personal Chrome")).toBeTruthy();
  });

  test("all agents explicitly includes future agents and updates current assignments", async () => {
    const listener = vi.fn();
    window.addEventListener(BROWSER_PROFILE_CHANGED_EVENT, listener);
    const client = await ready();
    fireEvent.click(screen.getByRole("radio", { name: "All agents (including future agents)" }));
    fireEvent.click(screen.getByRole("button", { name: "Save sharing" }));
    await waitFor(() => expect(mutations()).toEqual([{ path: "/api/society/browser/profiles/personal/sharing", method: "PUT", body: { scope: "all", agent_ids: [] } }]));
    await waitFor(() => expect(client.getQueryData<BrowserProfilesSnapshot>(BROWSER_PROFILES_QUERY)?.default_profile_id).toBe("personal"));
    expect(listener).toHaveBeenCalledOnce();
    expect((listener.mock.calls[0][0] as CustomEvent).detail.agentIds).toEqual(["writer"]);
    window.removeEventListener(BROWSER_PROFILE_CHANGED_EVENT, listener);
  });

  test("selected agents saves exactly the checked agents", async () => {
    await ready();
    fireEvent.click(screen.getByRole("checkbox", { name: "Scout" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Writer" }));
    fireEvent.click(screen.getByRole("button", { name: "Save sharing" }));
    await waitFor(() => expect(mutations()[0]?.body).toEqual({ scope: "selected", agent_ids: ["writer"] }));
  });

  test("an individual can opt out of the default with its own profile", async () => {
    await ready("scout");
    fireEvent.click(screen.getByRole("combobox", { name: "Agent browser profile" }));
    fireEvent.click(await screen.findByRole("option", { name: "Own separate profile" }));
    expect(mutations()).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: "Save agent choice" }));
    await waitFor(() => expect(mutations()[0]).toEqual({ path: "/api/society/agents/scout/browser/profile", method: "PUT", body: { mode: "own", profile_id: null } }));
    expect(await screen.findByText("Currently uses: Own separate profile")).toBeTruthy();
  });

  test("Chrome profile creation requires explicit domains and never assigns agents automatically", async () => {
    await ready();
    fireEvent.click(screen.getByRole("button", { name: "Add profile" }));
    const form = screen.getByRole("button", { name: "Create profile" }).closest("form")!;
    fireEvent.change(within(form).getByLabelText("Profile name"), { target: { value: "Work Chrome" } });
    expect((within(form).getByRole("button", { name: "Create profile" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(within(form).getByLabelText("Allowed websites"), { target: { value: "x.com, example.com" } });
    fireEvent.click(within(form).getByRole("button", { name: "Create profile" }));
    await waitFor(() => expect(mutations()).toEqual([{ path: "/api/society/browser/profiles", method: "POST", body: { name: "Work Chrome", kind: "chrome", allowed_domains: ["x.com", "example.com"] } }]));
    expect(await screen.findByDisplayValue("Work Chrome")).toBeTruthy();
  });

  test("domain edits are saved explicitly and notify the affected live browser", async () => {
    await ready();
    fireEvent.change(screen.getByLabelText("Allowed websites"), { target: { value: "x.com, EXAMPLE.COM, x.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Save profile details" }));
    await waitFor(() => expect(mutations()[0]?.body).toEqual({ name: "Personal Chrome", allowed_domains: ["x.com", "example.com"] }));
  });

  test("pairing is explicit and does not claim a Chrome Web Store installation", async () => {
    await ready();
    expect(screen.getByText(/installed manually until Chrome Web Store/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Download extension" }).getAttribute("href")).toBe("/api/society/browser/extension.zip");
    expect(screen.queryByText("private-code")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Generate pairing code" }));
    expect(await screen.findByText("private-code")).toBeTruthy();
    expect(screen.getByText("http://127.0.0.1:8765")).toBeTruthy();
    expect(screen.getByText(/Valid for 2 minutes/)).toBeTruthy();
    expect(screen.getByText(/Chrome disconnected/)).toBeTruthy();
  });

  test("busy assignment preserves the old profile and gives a recoverable error", async () => {
    failingStatus = 409;
    await ready();
    fireEvent.click(screen.getByRole("radio", { name: "All agents (including future agents)" }));
    fireEvent.click(screen.getByRole("button", { name: "Save sharing" }));
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", expect.stringContaining("browser is in use"));
    expect(snapshot.default_profile_id).toBeNull();
    expect(screen.queryByText("private provider error")).toBeNull();
  });

  test("removal states that website sessions are retained before disconnecting", async () => {
    await ready();
    fireEvent.click(screen.getByRole("button", { name: "Remove profile from Jarvis" }));
    expect(mutations()).toEqual([]);
    expect(screen.getByText(/website sessions are not deleted/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Remove from Jarvis" }));
    await waitFor(() => expect(mutations()[0]?.method).toBe("DELETE"));
  });
});
