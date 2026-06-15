/**
 * RerunButton: the single-click "Continue" / "Restart" control on terminal
 * mission cards in the Outputs view.
 *
 * Contract:
 * - Constructive action → fires on ONE click (unlike the destructive
 *   hold-to-abort button), POSTing `{confirmed:false}` to
 *   /api/missions/{id}/rerun.
 * - A destructive stored prompt comes back 409 `requires_confirm`; the button
 *   flips to a "Confirm re-run" state and the next click re-sends
 *   `{confirmed:true}` — never a native confirm() dialog (those freeze the
 *   desktop webview).
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { RerunButton } from "@/components/RerunButton";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function renderBtn(props: React.ComponentProps<typeof RerunButton>) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={client}>
      <RerunButton {...props} />
    </QueryClientProvider>,
  );
}

function okResponse(body: unknown) {
  return { ok: true, status: 200, json: async () => body };
}

describe("RerunButton", () => {
  it("shows the Continue label for a cancelled mission", () => {
    renderBtn({ missionId: "m1", action: "continue" });
    expect(screen.getByRole("button", { name: "Continue" })).toBeTruthy();
  });

  it("shows the Restart label for a failed mission", () => {
    renderBtn({ missionId: "m1", action: "restart" });
    expect(screen.getByRole("button", { name: "Restart" })).toBeTruthy();
  });

  it("POSTs to /rerun with confirmed:false on a single click", async () => {
    const fetchMock = vi.fn(async () =>
      okResponse({
        ok: true,
        parent_mission_id: "m1",
        mission_id: "new-id",
        action: "continue",
        started: true,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const onStarted = vi.fn();

    renderBtn({ missionId: "m1", action: "continue", onStarted });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [url, opts] = fetchMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(String(url)).toContain("/api/missions/m1/rerun");
    expect(opts.method).toBe("POST");
    expect(JSON.parse(String(opts.body))).toEqual({ confirmed: false });
    await waitFor(() => expect(onStarted).toHaveBeenCalledWith("new-id"));
  });

  it("asks for confirmation on a destructive 409, then re-sends confirmed:true", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: false,
        status: 409,
        json: async () => ({
          requires_confirm: true,
          warning: "Destructive mission detected.",
        }),
      })
      .mockResolvedValueOnce(
        okResponse({
          ok: true,
          parent_mission_id: "m1",
          mission_id: "new-id",
          action: "restart",
          started: false,
        }),
      );
    vi.stubGlobal("fetch", fetchMock);

    renderBtn({ missionId: "m1", action: "restart" });
    fireEvent.click(screen.getByRole("button", { name: "Restart" }));

    // First click → 409 → button flips to the confirm state.
    const confirmBtn = await screen.findByRole("button", {
      name: "Confirm re-run",
    });
    fireEvent.click(confirmBtn);

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const secondCall = fetchMock.mock.calls[1] as unknown as [
      string,
      RequestInit,
    ];
    expect(JSON.parse(String(secondCall[1].body))).toEqual({
      confirmed: true,
    });
  });
});
