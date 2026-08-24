import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockProviders } = vi.hoisted(() => ({
  mockProviders: {
    providers: [] as Array<Record<string, unknown>>,
    loading: false,
    error: null as string | null,
    refetch: vi.fn(async () => undefined),
  },
}));

vi.mock("@/i18n", () => ({
  // Identity translator: assertions match the keys themselves.
  useT: () => (key: string) => key,
  fill: (template: string, vars: Record<string, string | number>) =>
    `${template}${Object.values(vars).join("|")}`,
}));

// Keep the real pull helpers (they go through the faked fetch); only the
// provider list is scripted.
vi.mock("@/hooks/useProviders", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/hooks/useProviders")>()),
  useProviders: () => mockProviders,
}));

import {
  OverviewPanel,
  formatGigabytes,
} from "@/views/local-models/OverviewPanel";

const BASE = "/api/providers/ollama/local-models";

function role(overrides: Partial<Record<string, unknown>>) {
  return {
    id: "chat",
    label_key: "local_models.role_chat",
    config_key: "brain.providers.ollama.model",
    current: "",
    installed: false,
    required: ["tools"],
    recommended_capabilities: [],
    qualifying: [],
    recommended: "",
    writable: true,
    advanced: false,
    note: "",
    ...overrides,
  };
}

const ROLES = [
  role({
    id: "chat",
    current: "qwen3.5:4b",
    installed: true,
    qualifying: ["qwen3.5:4b"],
    recommended: "qwen3.8:27b",
  }),
  role({
    id: "tools_screen",
    label_key: "local_models.role_tools_screen",
    required: ["tools", "vision"],
    recommended: "qwen3.5:4b",
    qualifying: ["qwen3.5:4b"],
  }),
  role({
    id: "deep",
    label_key: "local_models.role_deep",
    recommended: "qwen3.5:4b",
    qualifying: ["qwen3.5:4b"],
  }),
  role({
    id: "embedding",
    label_key: "local_models.role_embedding",
    required: ["embedding"],
    recommended: "qwen3-embedding:4b",
  }),
  role({
    id: "voice",
    label_key: "local_models.role_voice",
    writable: false,
    advanced: true,
    current: "qwen3.5:4b",
    installed: true,
  }),
];

const SERVER = {
  installed: true,
  binary: "ollama",
  running: true,
  version: "0.32.15",
  detail: "",
  base_url: "http://127.0.0.1:11434",
  host_kind: "local",
  models_dir: "/home/x/.ollama/models",
  running_models: [
    {
      name: "qwen3.5:4b",
      size_bytes: 1,
      size_vram_bytes: 3_000_000_000,
      expires_at: "",
      context_length: 8192,
      digest: "",
    },
  ],
  disk_bytes: 12 * 1024 ** 3,
  loaded_vram_bytes: 3_000_000_000,
  error: null,
};

interface Fixture {
  roles?: unknown[];
  models?: Array<{ name: string }>;
  server?: Record<string, unknown>;
  accelerator_gb?: number;
}

function installFetchMock(fx: Fixture = {}) {
  const models = (fx.models ?? [{ name: "qwen3.5:4b" }]).map((m) => ({
    name: m.name,
    size_bytes: 1,
    digest: "",
    modified_at: "",
    family: "",
    parameter_size: "",
    quantization_level: "",
    context_length: null,
    capabilities: ["completion"],
    license: "",
    probed: true,
    used_by: [],
    loaded: false,
    size_vram_bytes: 0,
    expires_at: "",
    running_context_length: null,
  }));
  const ok = (body: unknown) =>
    ({ ok: true, status: 200, json: async () => body }) as Response;
  const fetchMock = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url === `${BASE}/roles`)
        return ok({
          provider: "ollama",
          server: "",
          roles: fx.roles ?? ROLES,
          error: null,
        });
      if (url === `${BASE}/inventory`)
        return ok({
          provider: "ollama",
          server: "",
          models,
          running: [],
          disk_bytes: 0,
          loaded_vram_bytes: 0,
          error: null,
        });
      if (url === `${BASE}/server`) return ok(fx.server ?? SERVER);
      if (url === `${BASE}/catalog/recommended`)
        return ok({
          server: "",
          server_reachable: true,
          message: "",
          memory_gb: 32,
          accelerator_gb: fx.accelerator_gb ?? 16,
          accelerator_source: "nvidia-smi",
          models: [],
          installed: [],
          curated_reviewed_on: "2026-08-24",
        });
      if (url.startsWith(`${BASE}/roles/`) && method === "PUT")
        return ok({
          ok: true,
          role: url.split("/").pop(),
          model: JSON.parse(String(init?.body)).model,
          config_key: "",
          message: "",
        });
      if (url === "/api/providers/ollama/pull" && method === "POST")
        return ok({
          state: "running",
          model: JSON.parse(String(init?.body)).model,
          message: "",
        });
      if (url.startsWith("/api/providers/ollama/pull/status"))
        return ok({ state: "done", model: "", message: "", percent: 100 });
      if (url.endsWith("/suggested-options"))
        return ok({
          model: "",
          options: { num_ctx: 16384, num_gpu: 999, keep_alive: "30m" },
          reasons: ["fits"],
          size_gb: 3.4,
          native_context: 262144,
          accelerator_gb: 16,
          accelerator_source: "nvidia-smi",
          ram_gb: 32,
        });
      if (url.endsWith("/options") && method === "PUT")
        return ok({
          model: "",
          options: JSON.parse(String(init?.body)),
          configured: true,
          profile_alias: null,
        });
      throw new Error(`unexpected fetch: ${method} ${url}`);
    },
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderPanel(props: Partial<Parameters<typeof OverviewPanel>[0]> = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <OverviewPanel providerId="ollama" {...props} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  mockProviders.providers = [
    { id: "ollama", label: "Ollama", tier: "brain", active: true },
  ];
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("formatGigabytes", () => {
  it("rounds sensibly", () => {
    expect(formatGigabytes(0)).toBe("0 GB");
    expect(formatGigabytes(12 * 1024 ** 3)).toBe("12 GB");
    expect(formatGigabytes(3.45 * 1024 ** 3)).toBe("3.5 GB");
    expect(formatGigabytes(50 * 1024 ** 2)).toBe("50 MB");
  });
});

describe("OverviewPanel", () => {
  it("shows the four tiles from the server and the shortlist", async () => {
    installFetchMock();
    renderPanel();

    await screen.findByText("local_models.overview.server_running");
    expect(screen.getByText("16.0 GB")).toBeDefined();
    expect(screen.getByText("12 GB")).toBeDefined();
    const loaded = screen.getByRole("group", {
      name: "local_models.overview.loaded",
    });
    expect(loaded.textContent).toContain("1");
    expect(loaded.textContent).toContain("qwen3.5:4b");
  });

  it("says 'unknown' for graphics memory when the probe read 0", async () => {
    installFetchMock({
      accelerator_gb: 0,
      server: { ...SERVER, running: false },
    });
    renderPanel();

    await screen.findByText("local_models.overview.server_stopped");
    expect(screen.getByText("local_models.overview.gpu_unknown")).toBeDefined();
  });

  it("renders the four writable rows, badges, and hides the read-only ones under More roles", async () => {
    installFetchMock();
    renderPanel({ onTune: vi.fn() });

    await screen.findByTestId("role-row-chat");
    expect(screen.getByTestId("role-row-tools_screen")).toBeDefined();
    expect(screen.getByTestId("role-row-deep")).toBeDefined();
    expect(screen.getByTestId("role-row-embedding")).toBeDefined();
    expect(screen.queryByText("local_models.role_voice")).toBeNull();
    expect(screen.getByText("qwen3.5:4b", { selector: "span" })).toBeDefined();
    expect(screen.getAllByText("tools").length).toBeGreaterThan(0);
    expect(screen.getByText("local_models.roles.tune")).toBeDefined();

    fireEvent.click(screen.getByText("local_models.roles.more_roles"));
    expect(screen.getByText("local_models.role_voice")).toBeDefined();
  });

  it("writes the pick through PUT roles/{role}", async () => {
    const fetchMock = installFetchMock();
    renderPanel();

    await screen.findByTestId("role-row-deep");
    const select = screen.getByLabelText(
      "local_models.roles.pick_labellocal_models.role_deep",
    );
    fireEvent.change(select, { target: { value: "qwen3.5:4b" } });

    await waitFor(() => {
      const put = fetchMock.mock.calls.find(
        ([u, i]) =>
          String(u) === `${BASE}/roles/deep` &&
          (i as RequestInit)?.method === "PUT",
      );
      expect(put).toBeDefined();
      expect(JSON.parse(String((put![1] as RequestInit).body))).toEqual({
        model: "qwen3.5:4b",
      });
    });
  });

  it("'Use recommended' pulls a missing model, assigns it, tunes silently and reads back", async () => {
    const fetchMock = installFetchMock();
    renderPanel();

    await screen.findByTestId("role-row-chat");
    // qwen3.8:27b is recommended for chat but not installed -> download label.
    fireEvent.click(
      screen.getByText("local_models.roles.download_recommendedqwen3.8:27b"),
    );

    await screen.findByText("local_models.roles.readback_gpu16k");
    const urls = fetchMock.mock.calls.map(
      ([u, i]) => `${(i as RequestInit)?.method ?? "GET"} ${String(u)}`,
    );
    expect(urls).toContain("POST /api/providers/ollama/pull");
    expect(urls).toContain(`PUT ${BASE}/roles/chat`);
    expect(urls).toContain(`PUT ${BASE}/models/qwen3.8%3A27b/options`);
  });

  it("offers one setup button when nothing is installed and fills all four rows", async () => {
    const fetchMock = installFetchMock({
      models: [],
      roles: ROLES.map((r) => ({
        ...r,
        current: "",
        installed: false,
        qualifying: [],
      })),
    });
    renderPanel();

    await screen.findByTestId("roles-empty");
    fireEvent.click(screen.getByText("local_models.roles.setup_button"));

    await waitFor(() => {
      const puts = fetchMock.mock.calls
        .filter(
          ([u, i]) =>
            String(u).startsWith(`${BASE}/roles/`) &&
            (i as RequestInit)?.method === "PUT",
        )
        .map(([u]) => String(u).split("/").pop());
      expect(puts.sort()).toEqual([
        "chat",
        "deep",
        "embedding",
        "tools_screen",
      ]);
    });
    const pulls = fetchMock.mock.calls
      .filter(
        ([u, i]) =>
          String(u) === "/api/providers/ollama/pull" &&
          (i as RequestInit)?.method === "POST",
      )
      .map(([, i]) => JSON.parse(String((i as RequestInit).body)).model);
    expect(pulls.sort()).toEqual(["qwen3-embedding:4b", "qwen3.8:27b"]);
  });

  it("names the active brain when it is not the local server", async () => {
    installFetchMock();
    mockProviders.providers = [
      { id: "gemini", label: "Gemini", tier: "brain", active: true },
    ];
    const onOpenApiKeys = vi.fn();
    renderPanel({ onOpenApiKeys });

    await screen.findByTestId("roles-other-brain");
    fireEvent.click(screen.getByText("local_models.roles.other_brain_link"));
    expect(onOpenApiKeys).toHaveBeenCalledTimes(1);
  });
});
