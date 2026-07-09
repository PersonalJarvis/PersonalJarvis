import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { RiskGate } from "./RiskGate";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

it("declining posts the quit request and shows the goodbye screen", async () => {
  const calls: Array<[string, RequestInit | undefined]> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      calls.push([url, init]);
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ ok: true, quitting: true }),
      });
    }),
  );
  const onAccept = vi.fn();
  render(<RiskGate onAccept={onAccept} />);

  fireEvent.click(screen.getByRole("button", { name: /decline/i }));

  await waitFor(() =>
    expect(
      calls.some(
        ([url, init]) =>
          url === "/api/onboarding/decline-terms" && init?.method === "POST",
      ),
    ).toBe(true),
  );
  expect(screen.getByText(/shutting down/i)).toBeDefined();
  expect(onAccept).not.toHaveBeenCalled();
});

it("shows the goodbye screen even when the quit request fails", async () => {
  // A warming/erroring backend must not trap the user on the gate: the
  // goodbye state renders either way and the user just closes the window.
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("net")));
  render(<RiskGate onAccept={() => undefined} />);

  fireEvent.click(screen.getByRole("button", { name: /decline/i }));

  await waitFor(() => expect(screen.getByText(/shutting down/i)).toBeDefined());
});

it("declining needs no checkbox tick; accepting still does", () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve({}) }),
  );
  render(<RiskGate onAccept={() => undefined} />);

  const decline = screen.getByRole("button", { name: /decline/i }) as HTMLButtonElement;
  const proceed = screen.getByRole("button", { name: /continue/i }) as HTMLButtonElement;
  expect(decline.disabled).toBe(false);
  expect(proceed.disabled).toBe(true);
});
