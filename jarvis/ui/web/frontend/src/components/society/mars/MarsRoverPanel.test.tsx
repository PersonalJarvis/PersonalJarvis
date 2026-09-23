import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MarsRoverPanel } from "./MarsRoverPanel";
import { readRoverAttempt, saveRoverAttempt } from "./roverApi";
import { roverRide, roverSnapshot } from "./roverFixtures.test-support";

vi.mock("@/i18n", () => ({ useT: () => (key: string) => key }));
vi.mock("@tanstack/react-query", () => ({ useQueryClient: () => ({ invalidateQueries: async () => undefined }) }));
afterEach(() => { cleanup(); sessionStorage.clear(); vi.unstubAllGlobals(); });

describe("explicit server-authorized rover controls", () => {
  it("does not board from arrival or stale display state without an explicit action", async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify(roverRide)));
    vi.stubGlobal("fetch", fetcher);
    const view = render(<MarsRoverPanel agentId="agent-one" active snapshot={roverSnapshot()} online={false} />);
    const board = screen.getByRole("button", { name: "society.mars.rover_board" }) as HTMLButtonElement;
    expect(board.disabled).toBe(true);
    fireEvent.click(board);
    expect(fetcher).not.toHaveBeenCalled();
    view.rerender(<MarsRoverPanel agentId="agent-one" active snapshot={roverSnapshot()} online />);
    expect(fetcher).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "society.mars.rover_board" }));
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
    const [path] = fetcher.mock.calls[0] as unknown as [string, RequestInit];
    expect(path).toContain("/agents/agent-one/rides/ride-one/board");
    await waitFor(() => expect(readRoverAttempt()).toBeNull());
  });

  it("retains the exact unacknowledged action through unmount and explicit retry", async () => {
    const fetcher = vi.fn().mockRejectedValueOnce(new Error("lost connection"))
      .mockResolvedValue(new Response(JSON.stringify(roverRide)));
    vi.stubGlobal("fetch", fetcher);
    const first = render(<MarsRoverPanel agentId="agent-one" active snapshot={roverSnapshot()} online />);
    fireEvent.click(screen.getByRole("button", { name: "society.mars.rover_board" }));
    await screen.findByRole("alert");
    const pending = readRoverAttempt();
    expect(pending?.action).toBe("board");
    first.unmount();
    render(<MarsRoverPanel agentId="agent-one" active snapshot={roverSnapshot()} online />);
    expect(fetcher).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "society.mars.rover_retry" }));
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
    expect(fetcher.mock.calls[1]).toEqual(fetcher.mock.calls[0]);
    await waitFor(() => expect(readRoverAttempt()).toBeNull());
  });

  it("scopes a selected route to the attached agent and blocks inactive-agent controls", async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify(roverRide)));
    vi.stubGlobal("fetch", fetcher);
    const snapshot = roverSnapshot([{ ...roverRide, state: "boarded", attached: true }]);
    const view = render(<MarsRoverPanel agentId="agent-one" active={false} snapshot={snapshot} online />);
    const travel = screen.getByRole("button", { name: "society.mars.rover_travel" }) as HTMLButtonElement;
    expect(travel.disabled).toBe(true);
    view.rerender(<MarsRoverPanel agentId="agent-one" active snapshot={snapshot} online />);
    fireEvent.click(screen.getByRole("button", { name: "society.mars.rover_travel" }));
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
    const [path, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit];
    expect(path).toBe("/api/society/mars/agents/agent-one/rides/ride-one/travel");
    expect(JSON.parse(init.body as string).destination_dock_id).toBe("outpost-b");
    await waitFor(() => expect(readRoverAttempt()).toBeNull());
  });

  it("recovers an exact saved action after its actor is archived without granting new actions", async () => {
    const pending = { action: "exit" as const, agent_id: "archived-agent", ride_id: "old-ride", request_id: crypto.randomUUID() };
    saveRoverAttempt(pending);
    const fetcher = vi.fn(async () => new Response(JSON.stringify({ ...roverRide, state: "completed", attached: false })));
    vi.stubGlobal("fetch", fetcher);
    render(<MarsRoverPanel agentId="" active={false} snapshot={roverSnapshot([], [])} online />);
    const retry = screen.getByRole("button", { name: "society.mars.rover_retry" }) as HTMLButtonElement;
    expect(retry.disabled).toBe(false);
    fireEvent.click(retry);
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
    const [path, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit];
    expect(path).toBe("/api/society/mars/agents/archived-agent/rides/old-ride/exit");
    expect(JSON.parse(init.body as string)).toEqual({ request_id: pending.request_id });
    await waitFor(() => expect(readRoverAttempt()).toBeNull());
    expect((screen.getByRole("button", { name: "society.mars.rover_reserve" }) as HTMLButtonElement).disabled).toBe(true);
  });
});
