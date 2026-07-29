/**
 * Component tests for the on-device provider panel.
 *
 * What these pin is the lesson behind the whole feature: a local provider card
 * may never imply a readiness nobody verified. The previous local Whisper card
 * was removed from the product because it rendered as ready on installs where
 * the engine was never installed, so the panel must:
 *
 *  - render the SERVER's sentence verbatim, never a client-side guess;
 *  - offer the install exactly when the server says it is not ready;
 *  - stay silent on cloud cards, which have no local runtime at all;
 *  - keep polling to a finished state, because the download takes minutes and
 *    a card frozen on "Installing…" is its own kind of lie.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { AuthWidget } from "@/components/providers/ProviderTierSection";
import type { ProviderDescriptor } from "@/hooks/useProviders";

vi.mock("@/i18n", () => ({
  useT: () => (key: string) =>
    ({
      "apikeys_view.local_install_cta": "Install on this machine",
      "apikeys_view.local_installing": "Installing…",
      "apikeys_view.local_install_retry": "Try again",
      "apikeys_view.local_install_hint": "This downloads a few gigabytes.",
    })[key] ?? key,
}));

const LOCAL_CARD: ProviderDescriptor = {
  id: "faster-whisper",
  label: "Whisper (on this machine)",
  tier: "stt",
  auth_mode: "none",
  secret_keys: [],
  secrets_set: {},
  dashboard_url: null,
  login_cli: null,
  install_hint: null,
  credential_path_hint: null,
  configured: true,
  active: false,
  cli_installed: null,
  credential_help: "Runs on this machine.",
  signup_url: null,
  billing: "local",
  alt_credential: null,
  local_runtime: {
    runtime: "faster-whisper",
    engine_installed: false,
    model_present: false,
    model_label: "Whisper large-v3",
    ready: false,
    detail: "The local speech engine is not installed yet.",
  },
} as ProviderDescriptor;

function withRuntime(
  patch: Partial<NonNullable<ProviderDescriptor["local_runtime"]>> | null,
): ProviderDescriptor {
  return {
    ...LOCAL_CARD,
    local_runtime: patch === null ? null : { ...LOCAL_CARD.local_runtime!, ...patch },
  };
}

function installFetchMock(handler: (url: string, init?: RequestInit) => unknown) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const body = handler(String(input), init);
    return {
      ok: true,
      status: 200,
      statusText: "OK",
      json: async () => body,
      text: async () => JSON.stringify(body),
    } as Response;
  });
  (globalThis as unknown as { fetch: typeof fetch }).fetch =
    fetchMock as unknown as typeof fetch;
  return fetchMock;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("local runtime panel", () => {
  it("shows the server's own explanation, not a client-side guess", () => {
    render(<AuthWidget descriptor={LOCAL_CARD} onChanged={() => {}} />);

    expect(
      screen.getByText("The local speech engine is not installed yet."),
    ).toBeTruthy();
  });

  it("offers the install while the provider is not ready", () => {
    render(<AuthWidget descriptor={LOCAL_CARD} onChanged={() => {}} />);

    expect(screen.getByRole("button", { name: /install on this machine/i })).toBeTruthy();
  });

  it("hides the install button once the server reports it ready", () => {
    render(
      <AuthWidget
        descriptor={withRuntime({
          ready: true,
          engine_installed: true,
          model_present: true,
          detail: "Ready — large-v3 runs entirely on this machine.",
        })}
        onChanged={() => {}}
      />,
    );

    expect(screen.queryByRole("button", { name: /install/i })).toBeNull();
    expect(screen.getByText(/runs entirely on this machine/i)).toBeTruthy();
  });

  it("renders nothing for a cloud provider", () => {
    render(<AuthWidget descriptor={withRuntime(null)} onChanged={() => {}} />);

    expect(screen.queryByRole("button", { name: /install/i })).toBeNull();
    expect(
      screen.queryByText("The local speech engine is not installed yet."),
    ).toBeNull();
  });

  it("starts the install and then reports progress from the server", async () => {
    const calls: string[] = [];
    installFetchMock((url) => {
      calls.push(url);
      if (url.endsWith("/local-install")) {
        return { state: "running", ready: false, message: "Install started." };
      }
      return { state: "running", ready: false, message: "Downloading encoder…" };
    });

    render(<AuthWidget descriptor={LOCAL_CARD} onChanged={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: /install on this machine/i }));

    await waitFor(() => {
      expect(calls.some((u) => u.endsWith("/local-install"))).toBe(true);
    });
    await waitFor(() => {
      expect(screen.getByText("Install started.")).toBeTruthy();
    });
    // The button must not stay clickable while a download is in flight.
    expect(
      (screen.getByRole("button", { name: /installing/i }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
  });

  it("surfaces a failed install as retryable instead of pretending it worked", async () => {
    installFetchMock(() => ({
      state: "error",
      ready: false,
      message: "Could not download Whisper large-v3: connection reset.",
    }));

    render(<AuthWidget descriptor={LOCAL_CARD} onChanged={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: /install on this machine/i }));

    await waitFor(() => {
      expect(screen.getByText(/connection reset/i)).toBeTruthy();
    });
    expect(
      (screen.getByRole("button", { name: /try again/i }) as HTMLButtonElement)
        .disabled,
    ).toBe(false);
  });
});
