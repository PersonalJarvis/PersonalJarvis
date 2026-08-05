/**
 * Component tests for the local-model download panel.
 *
 * The feature exists because a keyless install used to dead-end at "run:
 * ollama pull <model>" — a terminal instruction in an app with no terminal.
 * What these tests pin is that the panel never invents any part of that story:
 *
 *  - it appears only where the SERVER can actually be told to fetch a model,
 *    decided by a capability flag rather than a provider name (AP-21);
 *  - installed state and the memory verdict come from the server, verbatim;
 *  - a "tight" fit is shown as a caution, never as a disabled button — a GPU
 *    runs models the RAM rule calls tight;
 *  - a started download reports real progress instead of freezing on a spinner.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { AuthWidget } from "@/components/providers/ProviderTierSection";
import type { ProviderDescriptor } from "@/hooks/useProviders";

vi.mock("@/i18n", () => ({
  useT: () => (key: string) =>
    ({
      "apikeys_model_pull.title": "Models on this machine",
      "apikeys_model_pull.memory": "{0} GB RAM",
      "apikeys_model_pull.size": "~{0} GB",
      "apikeys_model_pull.installed": "Installed",
      "apikeys_model_pull.download": "Download",
      "apikeys_model_pull.custom_placeholder": "Any model name",
    })[key] ?? key,
}));

const PULL_CARD: ProviderDescriptor = {
  id: "ollama",
  label: "Ollama (local)",
  tier: "brain",
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
  credential_help: "Runs models fully local.",
  signup_url: null,
  billing: "local",
  alt_credential: null,
  local_runtime: null,
  supports_model_pull: true,
} as ProviderDescriptor;

const CATALOG = {
  server: "http://localhost:11434",
  server_reachable: true,
  message: "",
  memory_gb: 32,
  installed: ["qwen3.5:latest"],
  models: [
    {
      id: "qwen3.5",
      label: "Qwen 3.5",
      size_gb: 5.2,
      purpose: "The balanced default.",
      tools: true,
      vision: false,
      installed: true,
      fit: "comfortable",
      fit_note: "Runs comfortably in 32 GB of memory.",
    },
    {
      id: "qwen3-vl",
      label: "Qwen 3 VL",
      size_gb: 6,
      purpose: "Sees images.",
      tools: true,
      vision: true,
      installed: false,
      fit: "tight",
      fit_note: "Needs about 8 GB with 8 GB installed.",
    },
  ],
};

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
});

describe("local model download panel", () => {
  it("renders nothing on a card whose server has no download API", async () => {
    const fetchMock = installFetchMock(() => CATALOG);
    const cloudCard = { ...PULL_CARD, id: "openai", supports_model_pull: false };

    render(<AuthWidget descriptor={cloudCard} onChanged={() => {}} />);

    expect(screen.queryByText("Models on this machine")).toBeNull();
    // And it must not even ASK: a cloud card firing this request would get a
    // 400 on every render of the provider list.
    expect(
      fetchMock.mock.calls.filter((c) => String(c[0]).includes("pullable-models")),
    ).toHaveLength(0);
  });

  it("shows what the server reports: sizes, memory, and what is already there", async () => {
    installFetchMock(() => CATALOG);

    render(<AuthWidget descriptor={PULL_CARD} onChanged={() => {}} />);

    await waitFor(() => expect(screen.getByText("Qwen 3 VL")).toBeTruthy());
    expect(screen.getByText("32 GB RAM")).toBeTruthy();
    expect(screen.getByText("Installed")).toBeTruthy();
    expect(screen.getByText("Sees images.")).toBeTruthy();
  });

  it("shows a tight fit as a caution, never as a blocked download", async () => {
    installFetchMock(() => CATALOG);

    render(<AuthWidget descriptor={PULL_CARD} onChanged={() => {}} />);

    await waitFor(() =>
      expect(screen.getByText("Needs about 8 GB with 8 GB installed.")).toBeTruthy(),
    );
    const buttons = screen.getAllByRole("button", { name: /download/i });
    expect(buttons.some((b) => !(b as HTMLButtonElement).disabled)).toBe(true);
  });

  it("starts a download and then reports the server's progress", async () => {
    const calls: string[] = [];
    installFetchMock((url) => {
      calls.push(url);
      if (url.includes("/pull/status")) {
        return {
          state: "running",
          model: "qwen3-vl",
          message: "pulling manifest",
          percent: 42,
        };
      }
      if (url.endsWith("/pull")) {
        return { state: "running", model: "qwen3-vl", message: "Starting…" };
      }
      return CATALOG;
    });

    render(<AuthWidget descriptor={PULL_CARD} onChanged={() => {}} />);
    await waitFor(() => expect(screen.getByText("Qwen 3 VL")).toBeTruthy());

    const download = screen
      .getAllByRole("button", { name: /download/i })
      .find((b) => !(b as HTMLButtonElement).disabled)!;
    fireEvent.click(download);

    await waitFor(() => expect(screen.getByText(/qwen3-vl: Starting/)).toBeTruthy());
    expect(calls.some((u) => u.endsWith("/api/providers/ollama/pull"))).toBe(true);
  });

  it("keeps the shortlist visible when the server is down, with its reason", async () => {
    installFetchMock(() => ({
      ...CATALOG,
      server_reachable: false,
      message: "Ollama did not answer at http://localhost:11434.",
    }));

    render(<AuthWidget descriptor={PULL_CARD} onChanged={() => {}} />);

    await waitFor(() =>
      expect(screen.getByText(/Ollama did not answer/)).toBeTruthy(),
    );
    expect(screen.getByText("Qwen 3 VL")).toBeTruthy();
    // Nothing can be pulled from a server that is not there.
    for (const button of screen.getAllByRole("button", { name: /download/i })) {
      expect((button as HTMLButtonElement).disabled).toBe(true);
    }
  });
});
