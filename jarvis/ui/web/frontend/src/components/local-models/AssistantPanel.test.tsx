import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/i18n", () => ({
  useT: () => (key: string) => key,
  fill: (template: string, vars: Record<string, string | number>) =>
    `${template}${Object.values(vars).join("|")}`,
}));

vi.mock("@/store/events", () => ({
  useEventStore: (selector: (s: { assistantName: string }) => unknown) =>
    selector({ assistantName: "Jarvis" }),
}));

// The timeline is covered by its own tests; here only what the panel feeds it matters.
vi.mock("@/components/agentchat/AgentTimeline", () => ({
  AgentTimeline: ({ items }: { items: { type: string }[] }) => (
    <div data-testid="timeline-stub" data-count={items.length} />
  ),
}));

import { AssistantPanel } from "./AssistantPanel";
import { useLocalModelsAssistantStore } from "./assistantStore";
import { EMPTY_TIMELINE } from "@/components/agentchat/reduce";

const BASE = "/api/providers/ollama/local-models/assistant";

interface Call {
  url: string;
  method: string;
  body: Record<string, unknown> | null;
}

/** jsdom's WebSocket would try to reach a real host; a silent stand-in is enough here. */
class FakeSocket {
  onopen: (() => void) | null = null;
  onmessage: ((msg: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  close() {}
}

function stubFetch(routes: Record<string, (init?: RequestInit) => Response>): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      const body = typeof init?.body === "string" ? (JSON.parse(init.body) as Record<string, unknown>) : null;
      calls.push({ url, method, body });
      const key = `${method} ${url}`;
      const handler = routes[key] ?? routes[url];
      return handler ? handler(init) : new Response(null, { status: 404 });
    }),
  );
  return calls;
}

const reply = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });

const PROPOSAL_TEXT = `Here is the plan.

\`\`\`jarvis-proposal
{"version":1,"steps":[
  {"id":"s1","kind":"pull","model":"qwen3.5:8b","proven":true,"label":"Download Qwen"},
  {"id":"s2","kind":"set_role","role":"chat","model":"qwen3.5:8b","proven":true,"label":"Use it for Chat"}
],"notes":[]}
\`\`\``;

function finishedTurnWithProposal() {
  return {
    ...EMPTY_TIMELINE,
    lastSeq: 3,
    items: [
      { type: "user" as const, id: "u-1", text: "Help me set up", attachments: [], tsMs: 1 },
      {
        type: "turn" as const,
        id: "t1",
        provider: "openrouter",
        model: "m",
        effort: "",
        runner: "api",
        status: "done" as const,
        blocks: [{ kind: "text" as const, id: "live", text: PROPOSAL_TEXT }],
        startedMs: 1,
        durationMs: 10,
        usage: null,
        liveUsage: null,
        costUsd: null,
        error: null,
      },
    ],
  };
}

/** The rail reads the roles and the server through React Query. */
function renderPanel(props: Record<string, unknown> = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <AssistantPanel
        providerId="ollama"
        serverLabel="Ollama"
        request={null}
        onOpenApiKeys={() => {}}
        {...props}
      />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.stubGlobal("WebSocket", FakeSocket);
  useLocalModelsAssistantStore.setState({
    activeSessionId: null,
    activeSession: null,
    timeline: EMPTY_TIMELINE,
    sessions: [],
    busy: false,
    lastError: null,
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("AssistantPanel", () => {
  it("renders the 409 sentence with a way to the API Keys section", async () => {
    const sentence = "Connect the Jarvis Agents tier first — no usable credential.";
    stubFetch({
      [`GET ${BASE}/session`]: () => reply({ session_id: null, provider: "", model: "", ready: false, reason: "no_credential" }),
      [`GET ${BASE}/health`]: () => reply({}, 404),
      [`POST ${BASE}/run`]: () => reply({ detail: sentence }, 409),
    });
    const onOpenApiKeys = vi.fn();
    renderPanel({ request: { mode: "setup", token: 1 }, onOpenApiKeys });

    await waitFor(() => expect(screen.getByTestId("assistant-blocked")).toBeDefined());
    expect(screen.getByTestId("assistant-blocked").textContent).toContain(sentence);
    fireEvent.click(screen.getByRole("button", { name: "local_models.assistant.open_api_keys" }));
    expect(onOpenApiKeys).toHaveBeenCalledTimes(1);
    // The composer stays locked: there is no session to talk to.
    expect((screen.getByTestId("assistant-composer") as HTMLTextAreaElement).disabled).toBe(true);
  });

  it("shows the monitor's last check and a Fix that starts diagnose mode", async () => {
    const calls = stubFetch({
      [`GET ${BASE}/session`]: () => reply({ session_id: null, provider: "", model: "", ready: true, reason: "" }),
      [`GET ${BASE}/health`]: () =>
        reply({ status: "error", reason: "chat role: model not installed", since: 1_700_000_000, last_ok: null, checked_at: 1_700_000_000 }),
      [`POST ${BASE}/run`]: () => reply({ session_id: "s-1", turn_id: "t-1", surface: "local-models" }),
    });
    renderPanel();

    await waitFor(() => expect(screen.getByTestId("assistant-health")).toBeDefined());
    expect(screen.getByTestId("assistant-health").textContent).toContain("chat role: model not installed");

    fireEvent.click(screen.getByTestId("assistant-health-fix"));
    await waitFor(() =>
      expect(calls.some((c) => c.method === "POST" && c.url === `${BASE}/run` && c.body?.mode === "diagnose")).toBe(true),
    );
    await waitFor(() => expect(useLocalModelsAssistantStore.getState().activeSessionId).toBe("s-1"));
  });

  it("says so when the backend predates the assistant", async () => {
    stubFetch({});
    renderPanel();
    await waitFor(() => expect(screen.getByTestId("assistant-backend-missing")).toBeDefined());
  });

  it("confirms the proposal once and answers the matching approval cards by itself", async () => {
    const calls = stubFetch({
      // The store already holds the open session (set below); the route
      // answering null keeps the panel from re-opening (and resetting) it.
      [`GET ${BASE}/session`]: () => reply({ session_id: null, provider: "openrouter", model: "m", ready: true, reason: "" }),
      [`GET ${BASE}/health`]: () => reply({}, 404),
      "POST /api/agent-chat/sessions/s-1/messages": () => reply({ turn_id: "t2" }),
      "POST /api/agent-chat/sessions/s-1/approvals/a-1": () => reply({}),
      "POST /api/agent-chat/sessions/s-1/approvals/a-2": () => reply({}),
      "GET /api/agent-chat/sessions?limit=500&surface=local-models": () => reply({ sessions: [] }),
    });
    useLocalModelsAssistantStore.setState({ activeSessionId: "s-1", timeline: finishedTurnWithProposal() });
    renderPanel();

    await waitFor(() => expect(screen.getByTestId("setup-proposal")).toBeDefined());
    // The turn names who wrote it — read from the turn, not from config.
    expect(screen.getByTestId("assistant-turn-origin").textContent).toContain("\u00b7 m");
    fireEvent.click(screen.getByTestId("proposal-confirm"));
    await waitFor(() =>
      expect(calls.some((c) => c.url.endsWith("/messages") && String(c.body?.text).startsWith("Execute steps: s1, s2 ("))).toBe(true),
    );
    await waitFor(() => expect(screen.getByTestId("proposal-confirmed")).toBeDefined());

    // The runner asks about the confirmed pull: answered without a click.
    const ingest = useLocalModelsAssistantStore.getState().ingest;
    act(() => {
      ingest({ seq: 4, ts_ms: 20, kind: "turn_started", payload: { turn_id: "t2", provider: "openrouter", model: "m" } });
      ingest({
        seq: 5,
        ts_ms: 21,
        kind: "approval_required",
        payload: { turn_id: "t2", approval_id: "a-1", call_id: "c-1", name: "lm_pull", input: { model: "qwen3.5:8b" }, summary: "pull" },
      });
    });
    await waitFor(() =>
      expect(calls.some((c) => c.url.endsWith("/approvals/a-1") && c.body?.decision === "allow")).toBe(true),
    );

    // A question outside the plan stays a question.
    act(() => {
      ingest({
        seq: 6,
        ts_ms: 22,
        kind: "approval_required",
        payload: { turn_id: "t2", approval_id: "a-2", call_id: "c-2", name: "lm_pull", input: { model: "llama4:70b" }, summary: "pull" },
      });
    });
    await new Promise((r) => setTimeout(r, 20));
    expect(calls.some((c) => c.url.endsWith("/approvals/a-2"))).toBe(false);
  });
  it("names the brain that answers and marks it as the cloud one", async () => {
    stubFetch({
      [`GET ${BASE}/session`]: () =>
        reply({ session_id: null, provider: "gemini", model: "gemini-3-pro", ready: true, reason: "" }),
      [`GET ${BASE}/health`]: () => reply({}, 404),
    });
    renderPanel();

    await waitFor(() =>
      expect(screen.getByTestId("assistant-origin-model").textContent).toBe("gemini-3-pro"),
    );
    expect(screen.getByTestId("assistant-origin").textContent).toContain("Gemini");
    // Ready: the explaining sentence, not a warning.
    expect(screen.queryByTestId("assistant-origin-blocked")).toBeNull();
    expect(screen.getByTestId("assistant-origin").textContent).toContain(
      "local_models.assistant.origin_cloud",
    );
    // The same answer travels to a narrow window as one chip.
    expect(screen.getByTestId("assistant-origin-chip").textContent).toContain("gemini-3-pro");
  });

  it("says BEFORE the first click when no Tool Model can run the helper", async () => {
    const why = "Pick a Tool Model with an API key first — the helper runs on it.";
    stubFetch({
      [`GET ${BASE}/session`]: () =>
        reply({ session_id: null, provider: "gemini", model: "", ready: false, reason: why }),
      [`GET ${BASE}/health`]: () => reply({}, 404),
    });
    const onOpenApiKeys = vi.fn();
    renderPanel({ onOpenApiKeys });

    await waitFor(() =>
      expect(screen.getByTestId("assistant-origin-blocked").textContent).toBe(why),
    );
    // No model of its own: the pair falls back to the provider's default.
    expect(screen.getByTestId("assistant-origin-model").textContent).toBe(
      "local_models.assistant.origin_default_model",
    );
    fireEvent.click(screen.getByTestId("assistant-origin-change"));
    expect(onOpenApiKeys).toHaveBeenCalledTimes(1);
  });
});
