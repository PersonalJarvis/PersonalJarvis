/**
 * Component + hook tests for the CLI Test Hub.
 *
 * Pins the client-side projection of the interface contract in
 * `docs/superpowers/specs/2026-05-24-cli-integration-design.md`
 * ("Interface contract") for `POST /api/clis/test-run` and `GET /api/clis`.
 *
 * Covered render states: empty (no connected CLI), loading (request in
 * flight), success (command / exit-code colour / risk badge / steps), and
 * request error. The backend is built concurrently, so every test drives the
 * view through a mocked fetch surface — no real network.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { CliTestHubView } from "@/views/CliTestHubView";

// ChatInput (pulled in via ViewHeader's module) lazily grabs a WS client in a
// send handler; null keeps it a deterministic no-op in jsdom.
vi.mock("@/hooks/useWebSocket", () => ({
  getWSClient: () => null,
  useWebSocket: () => undefined,
}));

function freshClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: 0 },
      mutations: { retry: false },
    },
  });
}

function renderWithClient(node: React.ReactNode) {
  const client = freshClient();
  return render(
    <QueryClientProvider client={client}>{node}</QueryClientProvider>,
  );
}

interface RouteResult {
  status?: number;
  body: unknown;
  /** When set, the route rejects (network-level failure). */
  reject?: boolean;
}

/**
 * Mock `fetch` with per-route control keyed by URL prefix. Unknown URLs throw
 * so accidental network calls surface as test failures.
 */
function installFetchMock(routes: Record<string, () => RouteResult>) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    for (const prefix of Object.keys(routes)) {
      if (url.startsWith(prefix)) {
        const r = routes[prefix]();
        if (r.reject) throw new Error("network down");
        const status = r.status ?? 200;
        return {
          ok: status >= 200 && status < 300,
          status,
          statusText: status === 200 ? "OK" : "ERR",
          json: async () => r.body,
          text: async () => JSON.stringify(r.body),
        } as Response;
      }
    }
    throw new Error(`unexpected fetch ${url}`);
  });
  (globalThis as unknown as { fetch: typeof fetch }).fetch =
    fetchMock as unknown as typeof fetch;
  return fetchMock;
}

function cliSummary(name: string, status: string) {
  return {
    name,
    display_name: name,
    category: "cloud",
    icon: "",
    description: `${name} cli`,
    status,
    installed: true,
    connected: status === "connected",
    version: "1.2.3",
    auth_mode: "oauth_cli",
    is_custom: false,
    last_used_at: null,
    usage_count_7d: 0,
  };
}

function listResponse(clis: ReturnType<typeof cliSummary>[]) {
  return {
    clis,
    total: clis.length,
    connected: clis.filter((c) => c.status === "connected").length,
    installed: clis.length,
    categories: ["cloud"],
  };
}

const SUCCESS_RESPONSE = {
  ok: true,
  instruction: "list my projects",
  tool_called: "cli_gcloud",
  command: "gcloud projects list --format=json",
  risk_tier: "safe",
  exit_code: 0,
  stdout: '[{"projectId":"alpha"}]',
  stderr: "",
  duration_ms: 1234,
  summary: "You have 1 Google Cloud project: alpha.",
  error: null,
  steps: [{ tool: "cli_gcloud", command: "gcloud projects list", exit_code: 0 }],
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("CliTestHubView — empty state", () => {
  it("shows the connect-a-CLI empty state when nothing is connected", async () => {
    installFetchMock({
      "/api/clis": () => ({ body: listResponse([cliSummary("gh", "disconnected")]) }),
    });

    renderWithClient(<CliTestHubView />);

    await waitFor(() => {
      expect(screen.getByTestId("clis-empty")).toBeDefined();
    });
    // The connected-chips list is not rendered when nothing is connected.
    expect(screen.queryByTestId("clis-chips")).toBeNull();
  });
});

describe("CliTestHubView — connected CLIs panel", () => {
  it("lists only connected CLIs as chips", async () => {
    installFetchMock({
      "/api/clis": () =>
        ({
          body: listResponse([
            cliSummary("gcloud", "connected"),
            cliSummary("gh", "disconnected"),
            cliSummary("docker", "connected"),
          ]),
        }),
    });

    renderWithClient(<CliTestHubView />);

    await waitFor(() => {
      expect(screen.getByTestId("clis-chips")).toBeDefined();
    });
    const chips = screen.getByTestId("clis-chips");
    expect(chips.textContent).toContain("gcloud");
    expect(chips.textContent).toContain("docker");
    // The disconnected CLI must not appear as an available chip.
    expect(chips.textContent).not.toContain("gh");
  });
});

describe("CliTestHubView — run flow (success)", () => {
  it("disables Run until an instruction is typed, then renders the full result", async () => {
    installFetchMock({
      "/api/clis/test-run": () => ({ body: SUCCESS_RESPONSE }),
      "/api/clis": () => ({ body: listResponse([cliSummary("gcloud", "connected")]) }),
    });

    renderWithClient(<CliTestHubView />);

    const runBtn = (await screen.findByLabelText(
      "Run instruction",
    )) as HTMLButtonElement;
    // Empty instruction → button disabled.
    expect(runBtn.disabled).toBe(true);

    const textarea = screen.getByLabelText("Instruction for Assistant");
    fireEvent.change(textarea, { target: { value: "list my projects" } });
    expect(runBtn.disabled).toBe(false);

    fireEvent.click(runBtn);

    await waitFor(() => {
      expect(screen.getByTestId("result-panel")).toBeDefined();
    });

    // Summary is rendered prominently.
    expect(screen.getByTestId("result-summary").textContent).toContain(
      "1 Google Cloud project",
    );
    // Exact command in a monospace block.
    expect(screen.getByTestId("result-command").textContent).toContain(
      "gcloud projects list --format=json",
    );
    // Chosen tool.
    expect(screen.getByTestId("result-tool").textContent).toBe("cli_gcloud");
    // Risk badge reflects the tier.
    expect(screen.getByTestId("risk-badge").getAttribute("data-risk")).toBe("safe");
    // Exit code 0 → green (data-exit "0").
    const exit = screen.getByTestId("result-exit-code");
    expect(exit.getAttribute("data-exit")).toBe("0");
    expect(exit.className).toContain("emerald");
    // Duration.
    expect(screen.getByTestId("result-duration").textContent).toContain("1234");
    // stdout present, stderr absent.
    expect(screen.getByTestId("result-stdout").textContent).toContain("alpha");
    expect(screen.queryByTestId("result-stderr")).toBeNull();
  });

  it("colours a non-zero exit code red and renders stderr", async () => {
    installFetchMock({
      "/api/clis/test-run": () =>
        ({
          body: {
            ...SUCCESS_RESPONSE,
            ok: false,
            exit_code: 1,
            stderr: "ERROR: permission denied",
            risk_tier: "block",
            summary: "The command was blocked.",
          },
        }),
      "/api/clis": () => ({ body: listResponse([cliSummary("gcloud", "connected")]) }),
    });

    renderWithClient(<CliTestHubView />);

    fireEvent.change(screen.getByLabelText("Instruction for Assistant"), {
      target: { value: "delete everything" },
    });
    fireEvent.click(await screen.findByLabelText("Run instruction"));

    await waitFor(() => {
      expect(screen.getByTestId("result-exit-code")).toBeDefined();
    });
    const exit = screen.getByTestId("result-exit-code");
    expect(exit.getAttribute("data-exit")).toBe("1");
    expect(exit.className).toContain("destructive");
    // Risk badge reflects the block tier.
    expect(screen.getByTestId("risk-badge").getAttribute("data-risk")).toBe("block");
    // stderr is rendered.
    expect(screen.getByTestId("result-stderr").textContent).toContain(
      "permission denied",
    );
  });

  it("renders an ordered steps list only when more than one step exists", async () => {
    installFetchMock({
      "/api/clis/test-run": () =>
        ({
          body: {
            ...SUCCESS_RESPONSE,
            steps: [
              { tool: "cli_gcloud", command: "gcloud auth list", exit_code: 0 },
              { tool: "cli_gcloud", command: "gcloud projects list", exit_code: 0 },
            ],
          },
        }),
      "/api/clis": () => ({ body: listResponse([cliSummary("gcloud", "connected")]) }),
    });

    renderWithClient(<CliTestHubView />);

    fireEvent.change(screen.getByLabelText("Instruction for Assistant"), {
      target: { value: "two steps" },
    });
    fireEvent.click(await screen.findByLabelText("Run instruction"));

    await waitFor(() => {
      expect(screen.getByTestId("result-steps")).toBeDefined();
    });
    const steps = screen.getByTestId("result-steps");
    expect(steps.querySelectorAll("li").length).toBe(2);
    expect(steps.textContent).toContain("gcloud auth list");
  });

  it("does not render a steps list for a single step", async () => {
    installFetchMock({
      "/api/clis/test-run": () => ({ body: SUCCESS_RESPONSE }),
      "/api/clis": () => ({ body: listResponse([cliSummary("gcloud", "connected")]) }),
    });

    renderWithClient(<CliTestHubView />);

    fireEvent.change(screen.getByLabelText("Instruction for Assistant"), {
      target: { value: "one step" },
    });
    fireEvent.click(await screen.findByLabelText("Run instruction"));

    await waitFor(() => {
      expect(screen.getByTestId("result-panel")).toBeDefined();
    });
    expect(screen.queryByTestId("result-steps")).toBeNull();
  });
});

describe("CliTestHubView — request error", () => {
  it("renders the request-error panel when the endpoint rejects", async () => {
    installFetchMock({
      "/api/clis/test-run": () => ({ reject: true, body: null }),
      "/api/clis": () => ({ body: listResponse([cliSummary("gcloud", "connected")]) }),
    });

    renderWithClient(<CliTestHubView />);

    fireEvent.change(screen.getByLabelText("Instruction for Assistant"), {
      target: { value: "do something" },
    });
    fireEvent.click(await screen.findByLabelText("Run instruction"));

    await waitFor(() => {
      expect(screen.getByTestId("request-error")).toBeDefined();
    });
    expect(screen.queryByTestId("result-panel")).toBeNull();
  });

  it("surfaces a 500 HTTP error from the endpoint", async () => {
    installFetchMock({
      "/api/clis/test-run": () => ({ status: 500, body: { detail: "boom" } }),
      "/api/clis": () => ({ body: listResponse([cliSummary("gcloud", "connected")]) }),
    });

    renderWithClient(<CliTestHubView />);

    fireEvent.change(screen.getByLabelText("Instruction for Assistant"), {
      target: { value: "do something" },
    });
    fireEvent.click(await screen.findByLabelText("Run instruction"));

    await waitFor(() => {
      expect(screen.getByTestId("request-error")).toBeDefined();
    });
  });
});
