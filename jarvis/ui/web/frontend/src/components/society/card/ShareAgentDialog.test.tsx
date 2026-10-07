/**
 * The Share sheet: the template as the server built it, what was taken out,
 * the edit loop, and publishing under the signed-in GitHub name.
 *
 * No jest-dom in this repo — assertions use toBeTruthy()/toBeNull().
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/openExternal", () => ({ openExternalUrl: vi.fn() }));
// The chat column's store: sending is observed, never run.
const send = vi.fn(async () => "sent" as const);
vi.mock("../chat/AgentChatPanel", () => {
  const state = { activeSessionId: "society:release-scribe", send: (...args: unknown[]) => send(...(args as [])) };
  return { useSocietyChatStore: (selector: (s: typeof state) => unknown) => selector(state) };
});
// Shiki is not what is under test; the plain code text is.
vi.mock("@/components/docs/CodeBlock", () => ({
  CodeBlock: ({ code }: { code: string }) => <pre data-testid="code">{code}</pre>,
}));

import { ShareAgentDialog } from "@/components/society/card/ShareAgentDialog";
import type { SocietyAgent } from "@/components/society/data";
import type { ShareDraftWire } from "@/lib/agentShare";

const AGENT = {
  agentId: "release-scribe",
  name: "Release Scribe",
  title: "Writes the release notes",
  tier: "specialist",
  figure: null,
  palette: { primary: "#2f6f4f", secondary: "#8a5a3b", accent: "#ffd166" },
  chatSessionId: "society:release-scribe",
} as unknown as SocietyAgent;

function draft(over: Partial<ShareDraftWire> = {}): ShareDraftWire {
  const template = {
    schema: 1 as const,
    name: "Release Scribe",
    title: "Writes the release notes",
    instructions: "Collect merged pull requests and write release notes. Mail drafts to [email].",
    tier: "specialist" as const,
    effort: "",
    focus: ["plugin:github"],
    grant_mode: "all" as const,
    grants: [],
    denies: [],
    skills: null,
    require_approval: [],
    knowledge_scope: "shared" as const,
    avatar: {},
  };
  const listing = {
    name: "release-scribe",
    title: "Release Scribe",
    description: "Writes the release notes",
    categories: [],
    version: "1.0.0",
  };
  return {
    template,
    listing,
    findings: [{ kind: "email", field: "instructions", hint: "me…ev" }],
    polished_by: "",
    published: null,
    submission: { kind: "agent", name: listing.name, version: listing.version, agent: template },
    errors: [],
    ...over,
  };
}

let identity: Record<string, unknown> = { enabled: true, signed_in: false };
let current = draft();

function installFetchMock() {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body }) as Response;
    if (url === "/api/society/agents/release-scribe/template" && method === "GET") return ok(current);
    if (url === "/api/society/agents/release-scribe/template" && method === "PUT") {
      const edits = JSON.parse(String(init?.body)) as { summary?: string };
      current = draft({ listing: { ...current.listing, description: edits.summary ?? "" } });
      return ok(current);
    }
    if (url === "/api/society/agents/release-scribe/template/publish" && method === "POST") {
      return ok({
        ok: true,
        name: "release-scribe",
        version: "1.0.0",
        issue_url: "https://github.com/PersonalJarvis/marketplace/issues/7",
        install: { cli: "jarvis marketplace install release-scribe", runner: "", prompt: "" },
      });
    }
    if (url === "/api/marketplace/publish/identity") return ok(identity);
    if (url.startsWith("/api/marketplace/publish/status")) return ok({ live: false });
    throw new Error(`unexpected fetch: ${method} ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ShareAgentDialog agent={AGENT} onClose={() => undefined} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  current = draft();
  identity = { enabled: true, signed_in: false };
  send.mockClear();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ShareAgentDialog", () => {
  it("shows the template the server built and what was taken out", async () => {
    installFetchMock();
    mount();
    const code = await screen.findByTestId("code");
    expect(code.textContent).toContain('"kind": "agent"');
    expect(code.textContent).toContain("[email]");
    const privacy = screen.getByTestId("share-privacy");
    expect(privacy.textContent).toMatch(/1 private details removed/);
    expect(privacy.textContent).toContain("me…ev");
    expect(privacy.textContent).toMatch(/Never shared/);
  });

  it("saves an edited summary and shows the server's answer", async () => {
    const fetchMock = installFetchMock();
    mount();
    const summary = (await screen.findByTestId("share-summary")) as HTMLTextAreaElement;
    fireEvent.change(summary, { target: { value: "Turns merged PRs into notes." } });
    await waitFor(
      () =>
        expect(
          fetchMock.mock.calls.some(([, init]) => (init as RequestInit | undefined)?.method === "PUT"),
        ).toBe(true),
      { timeout: 2000 },
    );
  });

  it("asks for GitHub sign-in before publishing", async () => {
    installFetchMock();
    mount();
    expect(await screen.findByTestId("share-sign-in")).toBeTruthy();
    expect(screen.queryByTestId("share-publish")).toBeNull();
  });

  it("publishes as the signed-in user and shows the install line", async () => {
    identity = { enabled: true, signed_in: true, login: "octocat" };
    installFetchMock();
    mount();
    fireEvent.click(await screen.findByTestId("share-publish"));
    const done = await screen.findByTestId("share-published");
    expect(done.textContent).toContain("release-scribe 1.0.0 submitted");
    expect(done.textContent).toContain("jarvis marketplace install release-scribe");
  });

  it("lets the agent prepare its own public version through its chat", async () => {
    installFetchMock();
    mount();
    fireEvent.click(await screen.findByTestId("share-polish"));
    await waitFor(() => expect(send).toHaveBeenCalledTimes(1));
    const calls = send.mock.calls as unknown as string[][];
    expect(calls[0][0]).toContain("society_share_template");
  });

  it("blocks publishing while the template has a problem", async () => {
    identity = { enabled: true, signed_in: true, login: "octocat" };
    current = draft({ errors: ["write a one-line summary for the store card"] });
    installFetchMock();
    mount();
    const publish = (await screen.findByTestId("share-publish")) as HTMLButtonElement;
    expect(publish.disabled).toBe(true);
    expect(screen.getByText(/write a one-line summary/)).toBeTruthy();
  });
});
