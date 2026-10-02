import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { GrantExpiry, ReauthExplanation } from "@/views/PluginsView";

afterEach(cleanup);

/** Minimal plugin shaped like `adapt()` output; only the reauth bits matter. */
function plugin(over: Record<string, unknown> = {}) {
  return {
    id: "gmail",
    name: "Gmail",
    description: "Read, send, organize and delete mail in your inbox",
    category: "Calendar & Mail",
    logoSlug: "gmail",
    authMode: "oauth_pkce_loopback",
    authConfig: { mode: "oauth_pkce_loopback" },
    status: "needs_reauth",
    longevity: "self_renewing",
    ...over,
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
  } as any;
}

describe("ReauthExplanation", () => {
  it("names the cause instead of a bare 'reconnect needed'", () => {
    render(<ReauthExplanation plugin={plugin({ reauthReason: "provider_rejected" })} />);

    expect(screen.getByText(/no longer accepts this authorization/i)).toBeDefined();
  });

  it("distinguishes a refused client from a withdrawn grant", () => {
    render(<ReauthExplanation plugin={plugin({ reauthReason: "client_rejected" })} />);

    expect(screen.getByText(/no longer accepts this app's OAuth client/i)).toBeDefined();
  });

  it("says how long ago it broke", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-09T12:00:00Z"));
    render(
      <ReauthExplanation
        plugin={plugin({
          reauthReason: "provider_rejected",
          reauthAt: "2026-07-30T09:46:13Z",
        })}
      />,
    );

    expect(screen.getByText(/10 days ago/)).toBeDefined();
    vi.useRealTimers();
  });

  it("surfaces the provider-limited fix on its own line, not in a tooltip", () => {
    // The Google case: the note carries the ONLY durable fix (publish the
    // OAuth app), which is worthless if it is only reachable on hover.
    const note =
      "Google expires the authorization after 7 days while your own OAuth app is in Testing mode.";
    render(
      <ReauthExplanation
        plugin={plugin({
          reauthReason: "provider_rejected",
          longevity: "provider_limited",
          longevityNote: note,
        })}
      />,
    );

    expect(screen.getByText(note)).toBeDefined();
  });

  it("does not invent a cause when none was recorded", () => {
    render(<ReauthExplanation plugin={plugin({ reauthReason: undefined })} />);

    expect(screen.getByText(/authorization stopped working/i)).toBeDefined();
  });

  it("promises an automatic retry only when there will be one", () => {
    const { container: retried } = render(
      <ReauthExplanation plugin={plugin({ reauthReason: "provider_rejected" })} />,
    );
    expect(retried.querySelector("[title]")?.getAttribute("title")).toMatch(/once a day/i);

    cleanup();

    const { container: terminal } = render(
      <ReauthExplanation plugin={plugin({ reauthReason: "rotation_lost" })} />,
    );
    expect(terminal.querySelector("[title]")?.getAttribute("title")).toMatch(
      /cannot be retried automatically/i,
    );
  });

  it("ignores an unparseable timestamp rather than rendering NaN", () => {
    render(
      <ReauthExplanation
        plugin={plugin({ reauthReason: "provider_rejected", reauthAt: "not-a-date" })}
      />,
    );

    expect(screen.queryByText(/NaN/)).toBeNull();
  });
});


describe("GrantExpiry", () => {
  it("shows a provider deadline before access tokens stop refreshing", () => {
    render(<GrantExpiry plugin={plugin({ status: "connected", refreshExpiresAt: "2099-01-01T12:00:00Z" })} />);
    expect(screen.getByText(/Authorization ends/)).toBeDefined();
  });
  it("distinguishes an already expired provider grant", () => {
    render(<GrantExpiry plugin={plugin({ status: "connected", refreshExpiresAt: "2000-01-01T12:00:00Z" })} />);
    expect(screen.getByText(/Authorization expired/)).toBeDefined();
  });
  it.each([undefined, "not-a-date"])("does not invent a lifetime for %s", (stamp) => {
    const { container } = render(<GrantExpiry plugin={plugin({ status: "connected", refreshExpiresAt: stamp })} />);
    expect(container.textContent).toBe("");
  });
  it("does not display a retained deadline after disconnect", () => {
    const { container } = render(<GrantExpiry plugin={plugin({ status: "not_connected", refreshExpiresAt: "2099-01-01T12:00:00Z" })} />);
    expect(container.textContent).toBe("");
  });
  it("never promises a retry when there is no refresh token", () => {
    const { container } = render(<ReauthExplanation plugin={plugin({ reauthReason: "refresh_missing" })} />);
    expect(container.querySelector("[title]")?.getAttribute("title")).toMatch(/cannot be retried automatically/);
  });
});
