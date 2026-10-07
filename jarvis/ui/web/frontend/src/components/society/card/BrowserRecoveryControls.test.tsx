import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import { BrowserRecoveryControls } from "./BrowserRecoveryControls";

vi.mock("@/i18n", () => ({ useT: () => (key: string) => key.split(".").at(-1) }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("browser recovery", () => {
  test("restarts over HTTP even when reload and the live stream are unavailable", async () => {
    let finish!: (value: Response) => void;
    const fetcher = vi.fn(() => new Promise<Response>((resolve) => { finish = resolve; }));
    vi.stubGlobal("fetch", fetcher);
    render(<BrowserRecoveryControls agentId="agent/name" canReload={false} canRestart reload={vi.fn()} />);
    expect((screen.getByRole("button", { name: "reload" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "restart" }));
    expect(fetcher).toHaveBeenCalledWith("/api/society/agents/agent%2Fname/browser/restart",
      expect.objectContaining({ method: "POST" }));
    const pending = screen.getByRole("button", { name: "restarting" }) as HTMLButtonElement;
    expect(pending.disabled).toBe(true);
    fireEvent.click(pending);
    expect(fetcher).toHaveBeenCalledTimes(1);
    finish(new Response('{"restarted":true}', { status: 200 }));
    await waitFor(() => expect(screen.getByRole("button", { name: "restart" })).toBeTruthy());
  });

  test("failed restart shows a safe error and permits an explicit retry", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(new Response("private error", { status: 503 }))
      .mockResolvedValueOnce(new Response('{"restarted":true}'));
    vi.stubGlobal("fetch", fetcher);
    render(<BrowserRecoveryControls agentId="lead" canReload canRestart reload={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "restart" }));
    expect((await screen.findByRole("alert")).textContent).toBe("restart_failed");
    fireEvent.click(screen.getByRole("button", { name: "restart" }));
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  test("external Chrome offers page reload without a process restart", () => {
    const reload = vi.fn();
    render(<BrowserRecoveryControls agentId="lead" canReload canRestart={false} reload={reload} />);
    expect(screen.queryByRole("button", { name: "restart" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "reload" }));
    expect(reload).toHaveBeenCalledTimes(1);
  });
});
