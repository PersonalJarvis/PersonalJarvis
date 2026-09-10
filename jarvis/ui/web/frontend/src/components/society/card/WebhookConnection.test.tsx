import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import { WebhookConnection } from "./WebhookConnection";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("WebhookConnection", () => {
  test("only retrieves credentials on request and displays the token masked", async () => {
    const fetcher = vi.fn(async (_path: string, _options?: RequestInit) => new Response(JSON.stringify({ path: "/api/tasks/hooks/sample", token: "sample-generated-token" }), { status: 200 }));
    vi.stubGlobal("fetch", fetcher);
    render(<WebhookConnection taskId="sample" />);
    const connect = await screen.findByText("Connect webhook");
    expect(fetcher).not.toHaveBeenCalled();
    fireEvent.click(connect);
    const token = await screen.findByDisplayValue("sample-generated-token");
    expect(token.getAttribute("type")).toBe("password");
    expect(fetcher.mock.calls[0][0]).toBe("/api/tasks/sample/webhook-connection");
    expect(screen.getByDisplayValue(`${window.location.origin}/api/tasks/hooks/sample`)).toBeTruthy();
  });

  test("rotation requires the UI confirmation and uses the protected rotation endpoint", async () => {
    const fetcher = vi.fn(async (_path: string, options?: RequestInit) => new Response(JSON.stringify({
      path: "/api/tasks/hooks/sample", token: options?.method === "POST" ? "new-sample-token" : "old-sample-token",
    }), { status: 200 }));
    vi.stubGlobal("fetch", fetcher);
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<WebhookConnection taskId="sample" />);
    fireEvent.click(await screen.findByText("Connect webhook"));
    await screen.findByDisplayValue("old-sample-token");
    fireEvent.click(screen.getByText("Rotate token"));
    expect(fetcher).toHaveBeenCalledTimes(1);
    confirm.mockReturnValue(true);
    fireEvent.click(screen.getByText("Rotate token"));
    await screen.findByDisplayValue("new-sample-token");
    expect(fetcher.mock.calls[1][0]).toBe("/api/tasks/sample/webhook-connection/rotate");
    expect(fetcher.mock.calls[1][1]?.method).toBe("POST");
  });

  test("credential-store failure stays actionable without inventing a connection", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "Credential store unavailable" }), { status: 503 })));
    render(<WebhookConnection taskId="sample" />);
    fireEvent.click(await screen.findByText("Connect webhook"));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("Credential store unavailable"));
    expect(screen.queryByText("Rotate token")).toBeNull();
  });
});
