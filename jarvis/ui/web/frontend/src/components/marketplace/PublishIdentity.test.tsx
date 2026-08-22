/**
 * The shared publishing identity: the header chip, the sign-in dialog and
 * the device-code ticket.
 *
 * What is pinned here is the contract every publishing surface relies on:
 * signed-out shows one button, signed-in shows the handle and a menu with
 * sign-out, and the dialog shows the device code GitHub handed out — split
 * for reading, copyable, with the GitHub button next to it.
 *
 * No jest-dom in this repo — assertions use toBeTruthy()/toBeNull().
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const openExternalUrl = vi.fn();
vi.mock("@/lib/openExternal", () => ({
  openExternalUrl: (url: string) => openExternalUrl(url),
}));
const robustCopy = vi.fn(async (_text: string) => true);
vi.mock("@/lib/clipboard", () => ({
  robustCopy: (text: string) => robustCopy(text),
}));

import {
  GithubSignInDialog,
  PublisherChip,
  type PublishIdentityWire,
} from "@/components/marketplace/PublishIdentity";
import { setUiLanguage } from "@/i18n";

const SIGNED_OUT: PublishIdentityWire = { enabled: true, wallpapers_enabled: true, signed_in: false };
const SIGNED_IN: PublishIdentityWire = {
  enabled: true,
  wallpapers_enabled: true,
  signed_in: true,
  login: "octocat",
  avatar_url: null,
};

function stubServer(initial: PublishIdentityWire) {
  const state = { identity: initial, pollsUntilConnected: 1, deleted: 0 };
  const json = (body: unknown, ok = true, status = 200) => ({ ok, status, json: async () => body });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: unknown, init?: { method?: string }) => {
      const url = String(input);
      const method = (init?.method ?? "GET").toUpperCase();
      if (url === "/api/marketplace/publish/identity" && method === "GET") {
        return json(state.identity);
      }
      if (url === "/api/marketplace/publish/identity" && method === "DELETE") {
        state.deleted += 1;
        state.identity = SIGNED_OUT;
        return json({ ok: true });
      }
      if (url === "/api/marketplace/publish/signin/start") {
        return json({
          flow_id: "flow-1",
          user_code: "ABCD-1234",
          verification_uri: "https://github.com/login/device",
          interval: 1,
        });
      }
      if (url.startsWith("/api/marketplace/publish/signin/poll/")) {
        if (state.pollsUntilConnected > 0) {
          state.pollsUntilConnected -= 1;
          return json({ status: "pending" });
        }
        state.identity = SIGNED_IN;
        return json({ status: "connected", login: "octocat" });
      }
      if (url.startsWith("/api/marketplace/publish/signin/") && method === "DELETE") {
        return json({ ok: true });
      }
      throw new Error(`unexpected fetch: ${method} ${url}`);
    }),
  );
  return state;
}

function renderWith(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  openExternalUrl.mockReset();
  robustCopy.mockClear();
  setUiLanguage("en");
});

describe("PublisherChip", () => {
  it("offers the sign-in while signed out", async () => {
    stubServer(SIGNED_OUT);
    const onSignIn = vi.fn();
    renderWith(<PublisherChip onSignIn={onSignIn} />);

    const button = await screen.findByTestId("publisher-chip-signed-out");
    expect(button.textContent).toContain("Sign in with GitHub");
    fireEvent.click(button);
    expect(onSignIn).toHaveBeenCalledTimes(1);
  });

  it("shows the handle and signs out from its menu", async () => {
    const server = stubServer(SIGNED_IN);
    const onMine = vi.fn();
    renderWith(<PublisherChip onSignIn={() => undefined} onMine={onMine} />);

    const chip = await screen.findByTestId("publisher-chip");
    expect(chip.textContent).toContain("@octocat");

    fireEvent.click(chip);
    fireEvent.click(screen.getByRole("menuitem", { name: /My publications/ }));
    expect(onMine).toHaveBeenCalledTimes(1);

    fireEvent.click(chip);
    fireEvent.click(screen.getByRole("menuitem", { name: /Sign out/ }));
    await waitFor(() => expect(server.deleted).toBe(1));
    // The chip re-reads the identity and falls back to the sign-in button.
    expect(await screen.findByTestId("publisher-chip-signed-out")).toBeTruthy();
  });

  it("renders nothing when publishing is disabled in this deployment", async () => {
    stubServer({ enabled: false, wallpapers_enabled: false, signed_in: false });
    renderWith(<PublisherChip onSignIn={() => undefined} />);
    await waitFor(() => expect((fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.length).toBeGreaterThan(0));
    await waitFor(() => expect(screen.queryByTestId("publisher-chip-signed-out")).toBeNull());
    expect(screen.queryByTestId("publisher-chip")).toBeNull();
  });
});

describe("GithubSignInDialog", () => {
  it("starts the device flow on open and shows the code as a ticket", async () => {
    stubServer(SIGNED_OUT);
    renderWith(<GithubSignInDialog onClose={() => undefined} />);

    const ticket = await screen.findByTestId("device-code-ticket");
    expect(ticket).toBeTruthy();
    // The code is split at the dash for reading, but announced whole.
    expect(screen.getByTestId("device-code").getAttribute("aria-label")).toBe("ABCD-1234");
    expect(screen.getByText("ABCD")).toBeTruthy();
    expect(screen.getByText("1234")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /Open GitHub/ }));
    expect(openExternalUrl).toHaveBeenCalledWith("https://github.com/login/device");

    fireEvent.click(screen.getByRole("button", { name: /Copy the sign-in code|Copy the code/ }));
    await waitFor(() => expect(robustCopy).toHaveBeenCalledWith("ABCD-1234"));
  });

  it("turns into the signed-in card once GitHub approves", async () => {
    stubServer(SIGNED_OUT);
    renderWith(<GithubSignInDialog onClose={() => undefined} />);
    await screen.findByTestId("device-code-ticket");

    // The poller runs every `interval` seconds (1s here); two polls settle it.
    expect(await screen.findByText(/Signed in as @octocat/, undefined, { timeout: 5000 })).toBeTruthy();
    expect(screen.queryByTestId("device-code-ticket")).toBeNull();
    expect(screen.getByRole("button", { name: "Done" })).toBeTruthy();
  }, 10_000);

  it("speaks German when the UI does", async () => {
    stubServer(SIGNED_OUT);
    setUiLanguage("de");
    renderWith(<GithubSignInDialog onClose={() => undefined} />);
    expect(await screen.findByText("Mit GitHub anmelden")).toBeTruthy(); // i18n-allow
    expect(await screen.findByText("Dein Einmal-Code")).toBeTruthy(); // i18n-allow
  });
});
