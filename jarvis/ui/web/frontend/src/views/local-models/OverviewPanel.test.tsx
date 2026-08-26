import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
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
import type { OverviewResponse } from "@/hooks/useLocalModels";
import {
  snapshotKey,
  writeOverviewSnapshot,
} from "@/lib/localModelsSnapshot";

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
  /** "404" = a backend without the route (the four legacy reads compose);
   *  "live" = the route answers with the composed payload. */
  overview?: "404" | "live";
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
  const rolesBody = {
    provider: "ollama",
    server: "",
    roles: fx.roles ?? ROLES,
    error: null,
  };
  const inventoryBody = {
    provider: "ollama",
    server: "",
    models,
    running: [],
    disk_bytes: 0,
    loaded_vram_bytes: 0,
    error: null,
  };
  const serverBody = fx.server ?? SERVER;
  const recommendedBody = {
    server: "",
    server_reachable: true,
    message: "",
    memory_gb: 32,
    accelerator_gb: fx.accelerator_gb ?? 16,
    accelerator_source: "nvidia-smi",
    models: [],
    installed: [],
    curated_reviewed_on: "2026-08-24",
  };
  const fetchMock = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.startsWith(`${BASE}/overview`)) {
        if ((fx.overview ?? "404") === "404")
          return {
            ok: false,
            status: 404,
            json: async () => ({ detail: "Not Found" }),
          } as Response;
        return ok({
          server: serverBody,
          roles: rolesBody,
          inventory: inventoryBody,
          recommended: recommendedBody,
          source: "live",
          fetched_at: 1_700_000_000,
        });
      }
      if (url === `${BASE}/roles`) return ok(rolesBody);
      if (url === `${BASE}/inventory`) return ok(inventoryBody);
      if (url === `${BASE}/server`) return ok(serverBody);
      if (url === `${BASE}/catalog/recommended`) return ok(recommendedBody);
      if (url.startsWith(`${BASE}/roles/`) && method === "PUT")
        return ok({
          ok: true,
          role: url.split("/").pop(),
          model: JSON.parse(String(init?.body)).model,
          config_key: "",
          message: "",
        });
      if (url === "/api/brain/switch" && method === "POST")
        return ok({ ok: true, active: "ollama" });
      if (url === `${BASE}/verify` && method === "POST")
        return ok({
          ok: true,
          status: "ok",
          reason: "",
          steps: [
            { id: "server", ok: true, model: "", detail: "Ollama 0.32.15", ms: 12 },
            { id: "chat", ok: true, model: "qwen3.8:27b", detail: "Answered.", ms: 1800 },
            {
              id: "embedding",
              ok: null,
              model: "",
              detail: "No embedding role is configured.",
              ms: 0,
            },
          ],
        });
      if (url === `${BASE}/runtime/autostart`)
        return ok({
          enabled: true,
          in_use: true,
          reason: "local models serve the chat role",
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

function snapshotPayload(): OverviewResponse {
  return {
    server: { ...SERVER, host_kind: "local", disk_bytes: 80 * 1024 ** 3 },
    roles: { provider: "ollama", server: "", roles: ROLES, error: null },
    inventory: {
      provider: "ollama",
      server: "",
      models: [],
      running: [],
      disk_bytes: 0,
      loaded_vram_bytes: 0,
      error: null,
    },
    recommended: {
      server: "",
      server_reachable: true,
      message: "",
      memory_gb: 32,
      accelerator_gb: 15.9,
      accelerator_source: "nvidia-smi",
      models: [],
      installed: [],
      curated_reviewed_on: "2026-08-24",
    } as OverviewResponse["recommended"],
    source: "live",
    fetched_at: 1_700_000_000,
  } as OverviewResponse;
}

beforeEach(() => {
  window.localStorage.removeItem(snapshotKey("ollama"));
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

describe("OverviewPanel paint-first", () => {
  it("paints the tiles synchronously from the snapshot while the fetch never answers", () => {
    writeOverviewSnapshot("ollama", snapshotPayload());
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise<Response>(() => undefined)),
    );
    renderPanel();

    // No await: the snapshot is on screen in the first render.
    const disk = screen.getByRole("group", {
      name: "local_models.overview.disk",
    });
    expect(disk.textContent).toContain("80 GB");
    expect(screen.getByTestId("overview-status").textContent).toContain(
      "local_models.overview.status_runningOllama|0.32.15",
    );
    expect(screen.getByTestId("role-row-chat")).toBeDefined();
    // The refresh is underway, the snapshot is what is shown -> "Checking…".
    expect(screen.getByTestId("overview-checking")).toBeDefined();
  });

  it("drops 'Checking…' and writes the snapshot once live data arrives", async () => {
    writeOverviewSnapshot("ollama", snapshotPayload());
    installFetchMock({ overview: "live" });
    renderPanel();

    await waitFor(() =>
      expect(screen.queryByTestId("overview-checking")).toBeNull(),
    );
    const disk = screen.getByRole("group", {
      name: "local_models.overview.disk",
    });
    expect(disk.textContent).toContain("12 GB");
    const stored = JSON.parse(
      String(window.localStorage.getItem(snapshotKey("ollama"))),
    );
    expect(stored.data.server.disk_bytes).toBe(12 * 1024 ** 3);
  });

  it("reads the overview route when the backend has it", async () => {
    const fetchMock = installFetchMock({ overview: "live" });
    renderPanel();

    await screen.findByTestId("role-row-chat");
    const urls = fetchMock.mock.calls.map(([u]) => String(u));
    expect(urls).toContain(`${BASE}/overview`);
    expect(urls).not.toContain(`${BASE}/roles`);
    expect(urls).not.toContain(`${BASE}/server`);
  });

  it("composes the four legacy reads when the route answers 404", async () => {
    const fetchMock = installFetchMock({ overview: "404" });
    renderPanel();

    await screen.findByTestId("role-row-chat");
    const urls = fetchMock.mock.calls.map(([u]) => String(u));
    expect(urls).toContain(`${BASE}/overview`);
    expect(urls).toContain(`${BASE}/roles`);
    expect(urls).toContain(`${BASE}/inventory`);
    expect(urls).toContain(`${BASE}/server`);
    expect(urls).toContain(`${BASE}/catalog/recommended`);
  });
});

describe("OverviewPanel", () => {
  it("opens with one status sentence: server, graphics memory, active brain", async () => {
    installFetchMock();
    renderPanel();

    const status = await screen.findByTestId("overview-status");
    await waitFor(() =>
      expect(status.textContent).toContain(
        "local_models.overview.status_runningOllama|0.32.15",
      ),
    );
    expect(status.textContent).toContain(
      "local_models.overview.status_gpu16.0",
    );
    expect(status.textContent).toContain(
      "local_models.overview.status_brain_activeOllama",
    );
  });

  it("names the other brain in the status sentence when Ollama is not active", async () => {
    installFetchMock();
    mockProviders.providers = [
      { id: "ollama", label: "Ollama", tier: "brain", active: false },
      { id: "openrouter", label: "OpenRouter", tier: "brain", active: true },
    ];
    renderPanel();

    const clause = await screen.findByTestId("overview-brain-clause");
    expect(clause.textContent).toBe(
      "local_models.overview.status_brain_otherOllama|OpenRouter",
    );
  });

  it("offers one action: browse the catalogue", async () => {
    installFetchMock();
    const onBrowse = vi.fn();
    renderPanel({ onBrowse });

    await screen.findByTestId("overview-actions");
    fireEvent.click(
      screen.getByRole("button", { name: "local_models.overview.action_browse" }),
    );
    expect(onBrowse).toHaveBeenCalledTimes(1);
  });

  it("the detail level changes the roles on this very screen", async () => {
    installFetchMock();
    const { unmount } = renderPanel();
    const simple = await screen.findByTestId("local-models-roles");
    expect(simple.getAttribute("data-variant")).toBe("checklist");
    unmount();

    installFetchMock();
    renderPanel({ advanced: true });
    const advanced = await screen.findByTestId("local-models-roles");
    expect(advanced.getAttribute("data-variant")).not.toBe("checklist");
  });

  it("keeps 'Set up everything' even when no browse handler is wired", async () => {
    installFetchMock();
    renderPanel();
    await screen.findByTestId("overview-status");
    expect(
      screen.queryByRole("button", {
        name: "local_models.overview.action_browse",
      }),
    ).toBeNull();
    expect(
      screen.getByRole("button", { name: "local_models.overview.action_setup" }),
    ).toBeDefined();
  });

  it("names the install on the setup button when the server is not installed", async () => {
    installFetchMock({ server: { ...SERVER, installed: false, running: false } });
    renderPanel();
    // The label follows the server facts, so it settles with the data.
    expect(
      await screen.findByRole("button", {
        name: "local_models.overview.action_setup_installOllama",
      }),
    ).toBeDefined();
  });

  it("shows the three tiles: downloads on disk, graphics memory in use, loaded now", async () => {
    installFetchMock();
    renderPanel();

    const disk = await screen.findByRole("group", {
      name: "local_models.overview.disk",
    });
    await waitFor(() => expect(disk.textContent).toContain("12 GB"));
    await waitFor(() =>
      expect(disk.textContent).toContain("local_models.overview.disk_model_one"),
    );
    expect(disk.textContent).toContain("/home/x/.ollama/models");

    const gpu = screen.getByRole("group", {
      name: "local_models.overview.gpu",
    });
    expect(gpu.textContent).toContain("16.0 GB");
    expect(gpu.textContent).toContain("local_models.overview.gpu_in_use2.8 GB");

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

    const status = await screen.findByTestId("overview-status");
    await waitFor(() =>
      expect(status.textContent).toContain(
        "local_models.overview.status_stoppedOllama",
      ),
    );
    expect(screen.getByText("local_models.overview.gpu_unknown")).toBeDefined();
  });

  it("renders the roles as a checklist, expands one row to the full ledger row and folds it back", async () => {
    installFetchMock();
    renderPanel({ onTune: vi.fn() });

    const chat = await screen.findByTestId("role-row-chat");
    expect(chat.getAttribute("data-variant")).toBe("checklist");
    expect(screen.getByTestId("role-row-tools_screen")).toBeDefined();
    expect(screen.getByTestId("role-row-deep")).toBeDefined();
    expect(screen.getByTestId("role-row-embedding")).toBeDefined();
    expect(screen.queryByText("local_models.role_voice")).toBeNull();
    // Compact rows: the model, "not set", no badges, no picker, no Tune.
    // (Scoped to the ledger: the installed list below names the model too.)
    expect(
      within(screen.getByTestId("roles-ledger")).getByText("qwen3.5:4b", {
        selector: "span",
      }),
    ).toBeDefined();
    expect(
      screen.getAllByText("local_models.roles.checklist_not_set").length,
    ).toBe(3);
    expect(screen.queryByText("tools")).toBeNull();
    expect(screen.queryByText("local_models.roles.tune")).toBeNull();
    // Embedding has no recommendation applied yet -> "Download ..."; the
    // trailing action is exactly one per row.
    expect(
      screen.getByText("local_models.roles.download_recommendedqwen3-embedding:4b"),
    ).toBeDefined();

    // Expand chat via its line -> the full row with badges, picker, Tune, Done.
    fireEvent.click(
      screen.getByRole("button", { name: /local_models.role_chat/ }),
    );
    expect(screen.getAllByText("tools").length).toBeGreaterThan(0);
    expect(screen.getByText("local_models.roles.tune")).toBeDefined();
    expect(
      screen.getByLabelText("local_models.roles.pick_labellocal_models.role_chat"),
    ).toBeDefined();
    fireEvent.click(screen.getByText("local_models.roles.checklist_done"));
    expect(screen.queryByText("local_models.roles.tune")).toBeNull();

    fireEvent.click(screen.getByText("local_models.roles.more_roles"));
    expect(screen.getByText("local_models.role_voice")).toBeDefined();
  });

  it("writes the pick through PUT roles/{role} from the expanded row", async () => {
    const fetchMock = installFetchMock();
    renderPanel();

    await screen.findByTestId("role-row-deep");
    fireEvent.click(
      screen.getByRole("button", { name: /local_models.role_deep/ }),
    );
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
    // Every role's own pick is fetched — nothing falls back to the chat pick.
    expect(pulls.sort()).toEqual([
      "qwen3-embedding:4b",
      "qwen3.5:4b",
      "qwen3.8:27b",
    ]);
    await screen.findByText("local_models.overview.setup_done");
  });

  it("'Set up everything' keeps a role on its pick, writes the rest, then offers the brain switch", async () => {
    const fetchMock = installFetchMock({
      roles: ROLES.map((r) =>
        r.id === "tools_screen"
          ? { ...r, current: "qwen3.5:4b", installed: true }
          : r,
      ),
    });
    mockProviders.providers = [
      { id: "ollama", label: "Ollama", tier: "brain", active: false },
      { id: "openrouter", label: "OpenRouter", tier: "brain", active: true },
    ];
    renderPanel();

    await screen.findByTestId("overview-actions");
    fireEvent.click(
      screen.getByRole("button", { name: "local_models.overview.action_setup" }),
    );

    const progress = await screen.findByTestId("setup-progress");
    await screen.findByText("local_models.overview.setup_done");
    // Chat moved to the recommended download, tools & screen kept its pick.
    expect(progress.textContent).toContain(
      "local_models.overview.setup_assignedlocal_models.role_chat|qwen3.8:27b",
    );
    expect(progress.textContent).toContain(
      "local_models.overview.setup_kept_one",
    );
    // The proof and the boot switch are part of "done".
    expect(progress.textContent).toContain(
      "local_models.verify.server — local_models.verify.ok · local_models.verify.ms12",
    );
    expect(progress.textContent).toContain(
      "local_models.verify.chat — local_models.verify.ok · qwen3.8:27b · 1.8 s",
    );
    expect(progress.textContent).toContain(
      "local_models.verify.embedding — local_models.verify.skipped · No embedding role is configured.",
    );
    expect(progress.textContent).toContain(
      "local_models.overview.setup_autostart_on",
    );
    const puts = fetchMock.mock.calls
      .filter(
        ([u, i]) =>
          String(u).startsWith(`${BASE}/roles/`) &&
          (i as RequestInit)?.method === "PUT",
      )
      .map(([u]) => String(u).split("/").pop());
    expect(puts.sort()).toEqual(["chat", "deep", "embedding"]);

    // Another brain answers: the one decision left is offered in place.
    fireEvent.click(
      screen.getByRole("button", {
        name: "local_models.overview.setup_brain_buttonOllama",
      }),
    );
    await screen.findByTestId("setup-brain-done");
    expect(
      fetchMock.mock.calls.some(
        ([u, i]) =>
          String(u) === "/api/brain/switch" &&
          (i as RequestInit)?.method === "POST",
      ),
    ).toBe(true);
    expect(mockProviders.refetch).toHaveBeenCalled();
  });

  it("lists every installed model with what uses it and what it is recommended for", async () => {
    installFetchMock({
      models: [{ name: "qwen3.5:4b" }, { name: "qwen3-embedding:4b" }],
      roles: ROLES.map((r) =>
        r.id === "embedding"
          ? { ...r, recommended: "qwen3-embedding:4b" }
          : r,
      ),
    });
    const onManage = vi.fn();
    renderPanel({ onManage });

    const list = await screen.findByTestId("local-models-installed");
    await screen.findByTestId("installed-qwen3.5:4b");
    expect(list.textContent).toContain("local_models.installed.subtitle2|");
    // Each line says which roles it is the pick for (the fixture's rows
    // carry no used_by, so the markers are the recommendations).
    expect(
      screen.getByTestId("installed-recommended-qwen3.5:4b").textContent,
    ).toContain("local_models.role_tools_screen, local_models.role_deep");
    expect(
      screen.getByTestId("installed-recommended-qwen3-embedding:4b").textContent,
    ).toContain("local_models.role_embedding");

    fireEvent.click(
      screen.getByRole("button", { name: "local_models.installed.manage" }),
    );
    expect(onManage).toHaveBeenCalledTimes(1);
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
