import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { AgentRoutineDetail } from "./AgentRoutineDetail";
import type { TaskDetail } from "@/views/automations/automationsModel";
import en from "@/i18n/locales/society/en.json";

vi.mock("@/components/agentchat/AgentTimeline", () => ({ AgentTimeline: () => null }));

vi.mock("@/i18n", () => ({
  useLocaleChunk: () => {},
  useT: () => (key: string) => key.split(".").reduce<unknown>((value, part) => (value as Record<string, unknown>)?.[part], en) ?? key,
}));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function setup() {
  const trigger = { type: "calendar", local_time: "08:00", timezone: "Europe/Berlin" };
  const task = {
    id: "r1",
    title: "[agent:Mail] Inbox",
    trigger_type: "calendar",
    trigger,
    state: "scheduled",
    spec: {
      action: {
        kind: "agent",
        prompt: "Identity\nRoutine:\nRead mail.",
        provider: "claude-api",
        model: "opus",
        effort: "",
        account_id: "",
      },
    },
    steps: [],
  } as unknown as TaskDetail;
  const fetcher = vi.fn(
    async (_url: string, _init?: RequestInit) =>
      ({ ok: true, status: 200, json: async () => task }) as Response,
  );
  vi.stubGlobal("fetch", fetcher);
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <AgentRoutineDetail
        routine={{ id: "r1", title: "[agent:Mail] Inbox", prompt: "Read mail.", trigger, schedule: "", state: "scheduled", dueMs: null, lastRunMs: null }}
        agentId="mail"
        onClose={() => {}}
      />
    </QueryClientProvider>,
  );
  return { fetcher };
}

test("the pinned seat is shown and can follow the agent again", async () => {
  const { fetcher } = setup();
  await waitFor(() => expect((screen.getByLabelText("Name") as HTMLInputElement).disabled).toBe(false));
  // The pin is visible without any catalog fetch.
  expect(screen.getByText("claude-api · opus")).toBeTruthy();
  expect(fetcher.mock.calls.some(([url]) => String(url).includes("agent-chat"))).toBe(false);
  // The explicit control clears the pin without depending on catalog readiness.
  fireEvent.click(screen.getByText("Change"));
  fireEvent.click(screen.getByRole("button", { name: "Follow agent" }));
  expect(screen.getByText("Follow agent", { selector: "p" })).toBeTruthy();
  fireEvent.click(screen.getAllByText("Save")[0]);
  await waitFor(() =>
    expect(
      fetcher.mock.calls.some(
        ([url, init]) =>
          String(url).includes("/routines/") &&
          init?.method === "PATCH" &&
          JSON.parse(String(init?.body))?.provider === "",
      ),
    ).toBe(true),
  );
  const patch = fetcher.mock.calls.find(
    ([url, init]) => String(url).includes("/routines/") && init?.method === "PATCH",
  );
  expect(JSON.parse(String(patch?.[1]?.body))).toMatchObject({
    title: "Inbox",
    prompt: "Read mail.",
    provider: "",
    model: "",
    effort: "",
    account_id: "",
  });
});
