import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { JarvisApiGroup } from "./JarvisApiGroup";

const KEY = "jctl_secretvalue1234";
const MASKED = "jctl_…1234";

const robustCopy = vi.fn().mockResolvedValue(true);
vi.mock("@/lib/clipboard", () => ({
  robustCopy: (text: string) => robustCopy(text),
}));

afterEach(() => vi.restoreAllMocks());

function stubKeyFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({ ok: true, json: async () => ({ key: KEY, masked: MASKED }) }),
  );
}

describe("JarvisApiGroup", () => {
  it("renders the 'Control Key' heading and the masked key (full key hidden)", async () => {
    stubKeyFetch();
    render(<JarvisApiGroup />);

    expect(screen.getByRole("heading", { name: "Control Key" })).toBeTruthy();
    await waitFor(() => expect(screen.getByText(MASKED)).toBeTruthy());
    // The clear key must NOT be on screen until the user reveals it.
    expect(screen.queryByText(KEY)).toBeNull();
  });

  it("reveals the full key when Show is clicked", async () => {
    stubKeyFetch();
    render(<JarvisApiGroup />);
    await waitFor(() => screen.getByText(MASKED));

    fireEvent.click(screen.getByRole("button", { name: /show/i }));
    await waitFor(() => expect(screen.getByText(KEY)).toBeTruthy());
  });

  it("copies the clear key via robustCopy", async () => {
    stubKeyFetch();
    render(<JarvisApiGroup />);
    await waitFor(() => screen.getByText(MASKED));

    fireEvent.click(screen.getByRole("button", { name: /copy/i }));
    await waitFor(() => expect(robustCopy).toHaveBeenCalledWith(KEY));
  });

  it("rotates only after the confirmation dialog is accepted", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, json: async () => ({ key: KEY, masked: MASKED }) })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ ok: true, key: "jctl_rotated9999", masked: "jctl_…9999" }),
      });
    vi.stubGlobal("fetch", fetchMock);
    render(<JarvisApiGroup />);
    await waitFor(() => screen.getByText(MASKED));

    fireEvent.click(screen.getByRole("button", { name: /generate random key/i }));
    // Opening the dialog must NOT fire the request yet.
    expect(fetchMock.mock.calls.find(([, opts]) => opts?.method === "POST")).toBeUndefined();

    fireEvent.click(screen.getByRole("button", { name: /generate new key/i }));
    await waitFor(() => {
      const post = fetchMock.mock.calls.find(([, opts]) => opts?.method === "POST");
      expect(post).toBeTruthy();
      expect(post?.[0]).toBe("/api/control/api-key/rotate");
      expect(JSON.parse(post?.[1]?.body as string)).toMatchObject({ confirm: true });
    });
  });

  it("sets a user-chosen key via PUT after both entries match", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, json: async () => ({ key: KEY, masked: MASKED }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ ok: true, masked: "…tery" }) });
    vi.stubGlobal("fetch", fetchMock);
    render(<JarvisApiGroup />);
    await waitFor(() => screen.getByText(MASKED));

    fireEvent.click(screen.getByRole("button", { name: /choose my own key/i }));
    fireEvent.change(screen.getByLabelText("New Control Key"), {
      target: { value: "correct-horse-battery" },
    });
    fireEvent.change(screen.getByLabelText("Repeat new Control Key"), {
      target: { value: "correct-horse-battery" },
    });
    fireEvent.click(screen.getByRole("button", { name: /set this key/i }));

    await waitFor(() => {
      const put = fetchMock.mock.calls.find(([, opts]) => opts?.method === "PUT");
      expect(put).toBeTruthy();
      expect(put?.[0]).toBe("/api/control/api-key");
      expect(JSON.parse(put?.[1]?.body as string)).toMatchObject({
        value: "correct-horse-battery",
        confirm: true,
      });
    });
  });

  it("rejects a too-short custom key locally without any request", async () => {
    stubKeyFetch();
    render(<JarvisApiGroup />);
    await waitFor(() => screen.getByText(MASKED));
    const fetchMock = window.fetch as ReturnType<typeof vi.fn>;
    const callsBefore = fetchMock.mock.calls.length;

    fireEvent.click(screen.getByRole("button", { name: /choose my own key/i }));
    fireEvent.change(screen.getByLabelText("New Control Key"), {
      target: { value: "short" },
    });
    fireEvent.change(screen.getByLabelText("Repeat new Control Key"), {
      target: { value: "short" },
    });
    fireEvent.click(screen.getByRole("button", { name: /set this key/i }));

    await waitFor(() =>
      // Exact message: the form's hint text also mentions "At least 12 characters".
      expect(screen.getByText("At least 12 characters are required.")).toBeTruthy(),
    );
    expect(fetchMock.mock.calls.length).toBe(callsBefore);
  });
});
