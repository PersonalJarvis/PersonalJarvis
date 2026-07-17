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

/** URL-aware fetch stub: the component now issues TWO mount-time GETs (the
 * key itself + the browser-lock state), so ordered `mockResolvedValueOnce`
 * chains would race. `extra` may claim a call (e.g. the rotate POST) first. */
function stubKeyFetch(
  extra?: (url: string, opts?: RequestInit) => unknown,
  browserLockEnabled = false,
) {
  const mock = vi.fn().mockImplementation(async (url: string, opts?: RequestInit) => {
    const custom = extra?.(url, opts);
    if (custom) return custom;
    if (url === "/api/settings/browser-login") {
      return { ok: true, json: async () => ({ enabled: browserLockEnabled }) };
    }
    return { ok: true, json: async () => ({ key: KEY, masked: MASKED }) };
  });
  vi.stubGlobal("fetch", mock);
  return mock;
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
    const fetchMock = stubKeyFetch((_url, opts) =>
      opts?.method === "POST"
        ? {
            ok: true,
            json: async () => ({ ok: true, key: "jctl_rotated9999", masked: "jctl_…9999" }),
          }
        : undefined,
    );
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
    const fetchMock = stubKeyFetch((url, opts) =>
      url === "/api/control/api-key" && opts?.method === "PUT"
        ? { ok: true, json: async () => ({ ok: true, masked: "…tery" }) }
        : undefined,
    );
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

  it("turning the browser lock ON asks for confirmation before the PUT", async () => {
    const fetchMock = stubKeyFetch((url, opts) =>
      url === "/api/settings/browser-login" && opts?.method === "PUT"
        ? {
            ok: true,
            json: async () => ({
              ok: true,
              enabled: true,
              persisted: true,
              applied_live: true,
              session_minted: true,
            }),
          }
        : undefined,
    );
    render(<JarvisApiGroup />);
    await waitFor(() => screen.getByText(MASKED));

    fireEvent.click(screen.getByRole("switch"));
    // The dialog must appear WITHOUT any PUT having fired yet.
    expect(
      screen.getByRole("heading", { name: /require the control key in the browser\?/i }),
    ).toBeTruthy();
    expect(fetchMock.mock.calls.find(([, opts]) => opts?.method === "PUT")).toBeUndefined();

    fireEvent.click(screen.getByRole("button", { name: /require the key/i }));
    await waitFor(() => {
      const put = fetchMock.mock.calls.find(([, opts]) => opts?.method === "PUT");
      expect(put).toBeTruthy();
      expect(put?.[0]).toBe("/api/settings/browser-login");
      expect(JSON.parse(put?.[1]?.body as string)).toMatchObject({ enabled: true });
    });
  });

  it("turning the browser lock OFF PUTs immediately, no dialog", async () => {
    const fetchMock = stubKeyFetch(
      (url, opts) =>
        url === "/api/settings/browser-login" && opts?.method === "PUT"
          ? {
              ok: true,
              json: async () => ({
                ok: true,
                enabled: false,
                persisted: true,
                applied_live: true,
                session_minted: false,
              }),
            }
          : undefined,
      true,
    );
    render(<JarvisApiGroup />);
    await waitFor(() => screen.getByText(MASKED));
    await waitFor(() =>
      expect(screen.getByRole("switch").getAttribute("aria-checked")).toBe("true"),
    );

    fireEvent.click(screen.getByRole("switch"));
    await waitFor(() => {
      const put = fetchMock.mock.calls.find(([, opts]) => opts?.method === "PUT");
      expect(put).toBeTruthy();
      expect(put?.[0]).toBe("/api/settings/browser-login");
      expect(JSON.parse(put?.[1]?.body as string)).toMatchObject({ enabled: false });
    });
    expect(
      screen.queryByRole("heading", { name: /require the control key in the browser\?/i }),
    ).toBeNull();
  });
});
