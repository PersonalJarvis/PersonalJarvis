import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { AntigravitySubscriptionCard } from "./AntigravitySubscriptionCard";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

test("the connected Google subscription is visible without a switchable account directory", async () => {
  const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({
    installed: true, connected: true, mode: "oauth-personal", message: "Connected via Google subscription",
  }) });
  vi.stubGlobal("fetch", fetch);
  const view = render(<AntigravitySubscriptionCard refresh={0} />);
  expect(await screen.findByText("Connected via Google subscription")).toBeTruthy();
  expect(screen.getByRole("region", { name: "Antigravity" })).toBeTruthy();
  expect(screen.queryByRole("progressbar")).toBeNull();
  view.rerender(<AntigravitySubscriptionCard refresh={1} />);
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
});

test("an API-key connection is not presented as a Google subscription", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({
    installed: true, connected: true, mode: "api_key", message: "API key configured",
  }) }));
  render(<AntigravitySubscriptionCard refresh={0} />);
  expect(await screen.findByText("not signed in")).toBeTruthy();
  expect(screen.queryByText("API key configured")).toBeNull();
});
