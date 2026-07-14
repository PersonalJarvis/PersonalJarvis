import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import {
  ToolApprovalPanel,
  useMissionToolApprovals,
} from "@/components/missions/ToolApprovalPanel";
import { useI18nStore } from "@/i18n";
import type { PendingMissionToolApproval } from "@/types/missions";

const MISSION_ID = "mission/one";

beforeEach(() => {
  useI18nStore.getState().setUi("en", { push: false });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function pendingApproval(
  patch: Partial<PendingMissionToolApproval> = {},
): PendingMissionToolApproval {
  return {
    trace_id: "11111111-2222-4333-8444-555555555555",
    mission_id: MISSION_ID,
    worker_id: "worker-1",
    tool_name: "gmail/send_message",
    risk_tier: "ask",
    reason: "risk_tier",
    args_preview: "{'to': 'person@example.test', 'api_key': '<redacted>'}",
    requested_at_ns: Date.now() * 1_000_000,
    expires_at_ns: (Date.now() + 30_000) * 1_000_000,
    ...patch,
  };
}

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response;
}

function Harness({ missionId = MISSION_ID }: { missionId?: string | null }) {
  const query = useMissionToolApprovals(missionId);
  return <ToolApprovalPanel missionId={missionId} query={query} />;
}

function renderPanel(missionId: string | null = MISSION_ID) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={client}>
      <Harness missionId={missionId} />
    </QueryClientProvider>,
  );
}

describe("ToolApprovalPanel", () => {
  it("requires a second explicit click before approving the paused call", async () => {
    const approval = pendingApproval();
    let active = true;
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") {
        active = false;
        return jsonResponse({
          ok: true,
          mission_id: MISSION_ID,
          trace_id: approval.trace_id,
          decision: "approved",
          tool_name: approval.tool_name,
        });
      }
      return jsonResponse({
        mission_id: MISSION_ID,
        approvals: active ? [approval] : [],
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    renderPanel();

    expect(await screen.findByText("gmail/send_message")).toBeTruthy();
    expect(screen.getByText("<redacted>", { exact: false })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Review approval" }));

    expect(fetchMock.mock.calls.filter((call) => call[1]?.method === "POST")).toHaveLength(0);
    expect(screen.getByRole("alert").textContent).toContain("Run this tool now?");

    fireEvent.click(screen.getByRole("button", { name: "Approve and run" }));

    await waitFor(() => {
      const postCalls = fetchMock.mock.calls.filter(
        (call) => call[1]?.method === "POST",
      );
      expect(postCalls).toHaveLength(1);
      expect(String(postCalls[0][0])).toBe(
        "/api/missions/mission%2Fone/tool-approvals/11111111-2222-4333-8444-555555555555/approve",
      );
    });
  });

  it("denies directly and sends an explicit denial reason", async () => {
    const approval = pendingApproval();
    let active = true;
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") {
        active = false;
        return jsonResponse({
          ok: true,
          mission_id: MISSION_ID,
          trace_id: approval.trace_id,
          decision: "denied",
          tool_name: approval.tool_name,
        });
      }
      return jsonResponse({
        mission_id: MISSION_ID,
        approvals: active ? [approval] : [],
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: "Deny" }));

    await waitFor(() => {
      const postCall = fetchMock.mock.calls.find(
        (call) => call[1]?.method === "POST",
      );
      expect(postCall).toBeTruthy();
      expect(String(postCall?.[0])).toContain("/deny");
      expect(JSON.parse(String(postCall?.[1]?.body))).toEqual({
        reason: "user_denied",
      });
    });
  });

  it("renders an expired request as non-decidable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({
          mission_id: MISSION_ID,
          approvals: [
            pendingApproval({ expires_at_ns: (Date.now() - 1_000) * 1_000_000 }),
          ],
        }),
      ),
    );

    renderPanel();

    expect(await screen.findByText("Expired")).toBeTruthy();
    expect(screen.getByText(/can no longer be decided/i)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Review approval" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Deny" })).toBeNull();
  });

  it("shows a recoverable server error and does not fetch without a mission", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse({ detail: "Approval coordinator is warming up" }, 503),
    );
    vi.stubGlobal("fetch", fetchMock);

    const first = renderPanel();
    expect(await screen.findByText("Approvals could not be loaded.")).toBeTruthy();
    expect(screen.getByText("Approval coordinator is warming up")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));

    first.unmount();
    fetchMock.mockClear();
    renderPanel(null);
    expect(
      screen.getByText("Select a mission to review supervisor tool requests."),
    ).toBeTruthy();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
