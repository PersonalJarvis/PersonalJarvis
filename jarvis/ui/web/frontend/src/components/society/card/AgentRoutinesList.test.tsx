/**
 * The per-agent routines list: titles drop the scheduler prefix, + posts
 * through the society route, and a new row appears after save.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { AgentRoutinesList } from "@/components/society/card/AgentRoutinesList";

function mount(agentId = "mailbox") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity, refetchInterval: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <AgentRoutinesList agentId={agentId} />
    </QueryClientProvider>,
  );
}

function json(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response;
}

let routines: { id: string; title: string; state: string; trigger: unknown }[];
let fetchMock: ReturnType<typeof vi.fn>;

describe("AgentRoutinesList", () => {
  beforeEach(() => {
    routines = [
      {
        id: "r1",
        title: "[agent:Mailbox] Morning inbox",
        state: "scheduled",
        trigger: { kind: "every", interval_seconds: 3_600 },
      },
    ];
    fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      const path = String(url);
      const method = init?.method ?? "GET";
      if (path.includes("/routines") && method === "POST") {
        const body = JSON.parse(String(init?.body ?? "{}")) as { title?: string; prompt?: string };
        routines = [
          ...routines,
          {
            id: "r2",
            title: `[agent:Mailbox] ${body.title ?? "new"}`,
            state: "scheduled",
            trigger: { kind: "every", interval_seconds: 18_000 },
          },
        ];
        return json({ id: "r2", title: `[agent:Mailbox] ${body.title ?? "new"}` });
      }
      if (path.includes("/routines")) return json({ routines });
      return json({});
    });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  test("strips the [agent:Name] prefix the scheduler stores", async () => {
    mount();
    await waitFor(() => expect(screen.getByTestId("agent-routines").textContent).toContain("Morning inbox"));
    expect(screen.getByTestId("agent-routines").textContent).not.toContain("[agent:Mailbox]");
  });

  test("adding a routine posts to this agent and shows the new row", async () => {
    mount();
    fireEvent.click(await screen.findByTestId("agent-routines-add"));
    fireEvent.change(screen.getByPlaceholderText("Title"), { target: { value: "X marketing" } });
    fireEvent.change(screen.getByPlaceholderText("What to do"), { target: { value: "Draft the post." } });
    fireEvent.click(screen.getByText("Add"));

    await waitFor(() => {
      const post = fetchMock.mock.calls.find(
        (c) => String(c[0]).includes("/api/society/agents/mailbox/routines") && (c[1] as RequestInit | undefined)?.method === "POST",
      );
      expect(post).toBeTruthy();
      const body = JSON.parse(String(post?.[1]?.body));
      expect(body.title).toBe("X marketing");
      expect(body.prompt).toBe("Draft the post.");
      expect(body.schedule.kind).toBe("every");
    });

    await waitFor(() => expect(screen.getByTestId("agent-routines").textContent).toContain("X marketing"));
  });
});
