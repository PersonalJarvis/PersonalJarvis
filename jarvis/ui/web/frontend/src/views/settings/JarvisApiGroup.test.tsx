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

describe("JarvisApiGroup", () => {
  it("renders the 'Assistant API' heading and the masked key (full key hidden)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => ({ key: KEY, masked: MASKED }) }),
    );
    render(<JarvisApiGroup />);

    expect(screen.getByText("Assistant API")).toBeTruthy();
    await waitFor(() => expect(screen.getByText(MASKED)).toBeTruthy());
    // The clear key must NOT be on screen until the user reveals it.
    expect(screen.queryByText(KEY)).toBeNull();
  });

  it("reveals the full key when Show is clicked", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => ({ key: KEY, masked: MASKED }) }),
    );
    render(<JarvisApiGroup />);
    await waitFor(() => screen.getByText(MASKED));

    fireEvent.click(screen.getByRole("button", { name: /show/i }));
    await waitFor(() => expect(screen.getByText(KEY)).toBeTruthy());
  });

  it("copies the clear key via robustCopy", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => ({ key: KEY, masked: MASKED }) }),
    );
    render(<JarvisApiGroup />);
    await waitFor(() => screen.getByText(MASKED));

    fireEvent.click(screen.getByRole("button", { name: /copy/i }));
    await waitFor(() => expect(robustCopy).toHaveBeenCalledWith(KEY));
  });

  it("rotates the key with confirm=true when Regenerate is clicked", async () => {
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

    fireEvent.click(screen.getByRole("button", { name: /regenerate/i }));

    await waitFor(() => {
      const post = fetchMock.mock.calls.find(([, opts]) => opts?.method === "POST");
      expect(post).toBeTruthy();
      expect(post?.[0]).toBe("/api/control/api-key/rotate");
      expect(JSON.parse(post?.[1]?.body as string)).toMatchObject({ confirm: true });
    });
  });
});
