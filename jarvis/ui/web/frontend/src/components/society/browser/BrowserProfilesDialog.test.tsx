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

function mount(agentId?: string, connectChrome = false) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><BrowserProfilesDialog agentId={agentId} connectChrome={connectChrome} onClose={() => {}} /></QueryClientProvider>);
  return client;
}

async function ready(agentId?: string) {
  const client = mount(agentId);
  await screen.findByRole("heading", { name: "Profiles" });
  return client;
}

async function openCard(name: RegExp) {
  fireEvent.click(await screen.findByRole("button", { name }));
  return screen.getByLabelText("Profile name");
}

function mutations() { return calls.filter((call) => call.method !== "GET"); }

function sharedDefault() {
  snapshot.profiles.unshift({ id: "shared", name: "Shared browser", kind: "managed", connected: false,
    allowed_domains: [], agent_ids: ["writer"], is_default: true });
  snapshot.default_profile_id = "shared";
  snapshot.bindings.writer = { mode: "inherit", profile_id: null, effective_profile_id: "shared" };
}

describe("browser profile sharing", () => {
  test("Google recovery offers Chrome setup when none exists without creating or assigning anything", async () => {
    snapshot.profiles = [];
    snapshot.bindings.scout = { mode: "own", profile_id: null, effective_profile_id: null };
    mount("scout", true);
    const create = await screen.findByRole("button", { name: "Create profile" });
    expect(screen.getByRole("note").textContent).toContain("Sign in yourself in regular Chrome");
    expect((screen.getByRole("radio", { name: /Your regular Chrome/ }) as HTMLInputElement).checked).toBe(true);
    expect(mutations()).toEqual([]);
    fireEvent.click(within(create.closest("form")!).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("button", { name: "Create profile" })).toBeNull();
    expect(mutations()).toEqual([]);
  });

  test("Google recovery opens an existing Chrome profile instead of another managed profile", async () => {
    snapshot.profiles.unshift({ id: "isolated", name: "Managed browser", kind: "managed", connected: false,
      allowed_domains: [], agent_ids: [], is_default: false });
    mount("scout", true);
    expect(await screen.findByDisplayValue("Personal Chrome")).toBeTruthy();
    expect(screen.queryByDisplayValue("Managed browser")).toBeNull();
    expect(screen.queryByRole("button", { name: "Create profile" })).toBeNull();
    expect(mutations()).toEqual([]);
  });

  test("a removed assigned profile asks for a choice until the user picks a replacement", async () => {
    snapshot.profiles = [];
    mount("scout");
    expect(await screen.findByText(/profile this agent used was removed/)).toBeTruthy();
    const group = screen.getByRole("radiogroup", { name: "Which browser does Scout use?" });
    expect(within(group).getAllByRole("radio").some((radio) => (radio as HTMLInputElement).checked)).toBe(false);
    expect(mutations()).toEqual([]);
    fireEvent.click(within(group).getByRole("radio", { name: /Own private profile/ }));
    await waitFor(() => expect(mutations()[0]?.body).toEqual({ mode: "own", profile_id: null }));
  });

  test("a missing default is not offered as the shared browser", async () => {
    snapshot.profiles = [];
    snapshot.default_profile_id = "revoked";
    snapshot.bindings.scout = { mode: "inherit", profile_id: null, effective_profile_id: "revoked" };
    mount("scout");
    expect(await screen.findByText(/profile this agent used was removed/)).toBeTruthy();
    const shared = screen.getByRole("radio", { name: /Shared browser/ }) as HTMLInputElement;
    expect(shared.checked).toBe(false);
    expect(shared.disabled).toBe(true);
    expect(mutations()).toEqual([]);
  });

  test("opening the manager preserves assignments and reports a disconnected Chrome", async () => {
    await ready("scout");
    expect(mutations()).toEqual([]);
    expect((screen.getByRole("radio", { name: /Personal Chrome/ }) as HTMLInputElement).checked).toBe(true);
    expect(screen.getByText("Not connected")).toBeTruthy();
    expect(screen.getByText("Used by Scout")).toBeTruthy();
    expect(screen.getByText(/A website can still log you out/)).toBeTruthy();
  });

  test("the shared browser is offered once and its card cannot reset other choices", async () => {
    sharedDefault();
    await ready("writer");
    const group = screen.getByRole("radiogroup", { name: "Which browser does Writer use?" });
    expect(within(group).getAllByRole("radio").map((radio) => radio.closest("label")!.textContent)).toEqual([
      expect.stringContaining("Shared browser — the same logins"),
      expect.stringContaining("Own private profile"),
      expect.stringContaining("Personal Chrome"),
    ]);
    await openCard(/Shared browser.*Default/);
    expect(screen.getByText(/New agents get it automatically/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Use for all agents" })).toBeNull();
    expect(screen.queryByRole("checkbox")).toBeNull();
    expect(mutations()).toEqual([]);
  });

  test("using a profile for all agents asks first, then includes future agents", async () => {
    const listener = vi.fn();
    window.addEventListener(BROWSER_PROFILE_CHANGED_EVENT, listener);
    const client = await ready();
    await openCard(/Personal Chrome/);
    fireEvent.click(screen.getByRole("button", { name: "Use for all agents" }));
    expect(mutations()).toEqual([]);
    expect(screen.getByText(/Every agent switches to Personal Chrome/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Switch all agents" }));
    await waitFor(() => expect(mutations()).toEqual([{ path: "/api/society/browser/profiles/personal/sharing", method: "PUT", body: { scope: "all", agent_ids: [] } }]));
    await waitFor(() => expect(client.getQueryData<BrowserProfilesSnapshot>(BROWSER_PROFILES_QUERY)?.default_profile_id).toBe("personal"));
    expect(listener).toHaveBeenCalledOnce();
    expect((listener.mock.calls[0][0] as CustomEvent).detail.agentIds).toEqual(["writer"]);
    window.removeEventListener(BROWSER_PROFILE_CHANGED_EVENT, listener);
  });

  test("choosing agents saves exactly the checked agents and only after a change", async () => {
    await ready();
    await openCard(/Personal Chrome/);
    const save = screen.getByRole("button", { name: "Save agents" }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    fireEvent.click(screen.getByRole("checkbox", { name: "Scout" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Writer" }));
    expect(screen.getByText(/Agents you untick stop browsing/)).toBeTruthy();
    fireEvent.click(save);
    await waitFor(() => expect(mutations()[0]?.body).toEqual({ scope: "selected", agent_ids: ["writer"] }));
  });

  test("an agent switches to its own profile with one click", async () => {
    await ready("scout");
    fireEvent.click(screen.getByRole("radio", { name: /Own private profile/ }));
    await waitFor(() => expect(mutations()[0]).toEqual({ path: "/api/society/agents/scout/browser/profile", method: "PUT", body: { mode: "own", profile_id: null } }));
    await waitFor(() => expect((screen.getByRole("radio", { name: /Own private profile/ }) as HTMLInputElement).checked).toBe(true));
  });

  test("Chrome profile creation requires explicit domains and never assigns agents automatically", async () => {
    await ready();
    fireEvent.click(screen.getByRole("button", { name: "Add profile" }));
    const form = screen.getByRole("button", { name: "Create profile" }).closest("form")!;
    expect((within(form).getByRole("radio", { name: /Extra Jarvis browser/ }) as HTMLInputElement).checked).toBe(true);
    fireEvent.click(within(form).getByRole("radio", { name: /Your regular Chrome/ }));
    fireEvent.change(within(form).getByLabelText("Profile name"), { target: { value: "Work Chrome" } });
    expect((within(form).getByRole("button", { name: "Create profile" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(within(form).getByLabelText("Allowed websites"), { target: { value: "x.com, example.com" } });
    fireEvent.click(within(form).getByRole("button", { name: "Create profile" }));
    await waitFor(() => expect(mutations()).toEqual([{ path: "/api/society/browser/profiles", method: "POST", body: { name: "Work Chrome", kind: "chrome", allowed_domains: ["x.com", "example.com"] } }]));
    expect(await screen.findByDisplayValue("Work Chrome")).toBeTruthy();
  });

  test("domain edits are saved explicitly and only after a change", async () => {
    await ready();
    await openCard(/Personal Chrome/);
    const save = screen.getByRole("button", { name: "Save changes" }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Allowed websites"), { target: { value: "x.com, EXAMPLE.COM, x.com" } });
    fireEvent.click(save);
    await waitFor(() => expect(mutations()[0]?.body).toEqual({ name: "Personal Chrome", allowed_domains: ["x.com", "example.com"] }));
  });

  test("pairing is explicit and does not claim a Chrome Web Store installation", async () => {
    await ready();
    await openCard(/Personal Chrome/);
    expect(screen.getByText(/installed manually until Chrome Web Store/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Download extension" }).getAttribute("href")).toBe("/api/society/browser/extension.zip");
    expect(screen.queryByText("private-code")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Generate pairing code" }));
    expect(await screen.findByText("private-code")).toBeTruthy();
    expect(screen.getByText("http://127.0.0.1:8765")).toBeTruthy();
    expect(screen.getByText(/Valid for 2 minutes/)).toBeTruthy();
  });

  test("busy assignment preserves the old profile and gives a recoverable error", async () => {
    failingStatus = 409;
    await ready();
    await openCard(/Personal Chrome/);
    fireEvent.click(screen.getByRole("button", { name: "Use for all agents" }));
    fireEvent.click(screen.getByRole("button", { name: "Switch all agents" }));
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", expect.stringContaining("browser is in use"));
    expect(snapshot.default_profile_id).toBeNull();
    expect(screen.queryByText("private provider error")).toBeNull();
  });

  test("removal states that saved logins are retained before disconnecting", async () => {
    await ready();
    await openCard(/Personal Chrome/);
    fireEvent.click(screen.getByRole("button", { name: "Remove profile" }));
    expect(mutations()).toEqual([]);
    expect(screen.getByText(/Saved logins on this computer are not deleted/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(mutations()[0]?.method).toBe("DELETE"));
  });

  test("removing the shared profile warns that agents stop browsing", async () => {
    sharedDefault();
    await ready();
    await openCard(/Shared browser.*Default/);
    fireEvent.click(screen.getByRole("button", { name: "Remove profile" }));
    expect(screen.getByText(/This is the shared profile/)).toBeTruthy();
    expect(mutations()).toEqual([]);
  });
});
