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
import { footprint } from "@/views/local-models/ServerLaunch";
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
    layout: "card",
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
    id: "voice",
    label_key: "local_models.role_voice",
    current: "qwen3.5:4b",
    installed: true,
    qualifying: ["qwen3.5:4b"],
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
    layout: "row",
    required: ["embedding"],
    recommended: "qwen3-embedding:4b",
  }),
  role({
    id: "ack",
    label_key: "local_models.role_ack",
    layout: "footnote",
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
  starting: false,
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
  models?: Array<{ name: string; size_bytes?: number; capabilities?: string[] } & Record<string, unknown>>;
  server?: Record<string, unknown>;
  /** `roles.resident` — what sits in memory when every job is loaded at once. */
  resident?: Record<string, unknown>;
  accelerator_gb?: number;
  /** "404" = a backend without the route (the four legacy reads compose);
   *  "live" = the route answers with the composed payload. */
  overview?: "404" | "live";
}

function installFetchMock(fx: Fixture = {}) {
  const models = (fx.models ?? [{ name: "qwen3.5:4b" }]).map((m) => ({
    size_bytes: m.size_bytes ?? 1,
    digest: "",
    modified_at: "",
    family: "",
    parameter_size: "",
    quantization_level: "",
    context_length: null,
    capabilities: m.capabilities ?? ["completion"],
    license: "",
    probed: true,
    used_by: [],
    loaded: false,
    size_vram_bytes: 0,
    expires_at: "",
    running_context_length: null,
    ...m,
  }));
  const ok = (body: unknown) =>
    ({ ok: true, status: 200, json: async () => body }) as Response;
  const rolesBody = {
    provider: "ollama",
    server: "",
    roles: fx.roles ?? ROLES,
    resident: fx.resident,
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

describe("footprint", () => {
  it("counts one model once, however many jobs use it", () => {
    const models = [
      { name: "a:1", size_bytes: 4 * 1024 ** 3 },
      { name: "b:1", size_bytes: 9 * 1024 ** 3 },
    ] as Parameters<typeof footprint>[1];
    const rows = [
      { id: "chat", current: "a:1" },
      { id: "deep", current: "b:1" },
      { id: "tools_screen", current: "a:1" },
      { id: "embedding", current: "" },
    ] as Parameters<typeof footprint>[0];
    const { totalBytes, largestBytes, distinct } = footprint(rows, models);
    expect(distinct).toBe(2);
    expect(totalBytes).toBe(13 * 1024 ** 3);
    // The largest is what must sit in memory at once — not the sum.
    expect(largestBytes).toBe(9 * 1024 ** 3);
  });
});

describe("OverviewPanel paint-first", () => {
  it("paints the grid synchronously from the snapshot while the fetch never answers", () => {
    writeOverviewSnapshot("ollama", snapshotPayload());
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise<Response>(() => undefined)),
    );
    renderPanel();

    // No await: the snapshot is on screen in the first render.
    expect(screen.getByTestId("model-card-chat")).toBeDefined();
    expect(screen.getByTestId("model-card-current-chat").textContent).toBe(
      "qwen3.5:4b",
    );
    expect(screen.getByTestId("server-launch").textContent).toContain(
      "local_models.launch.server_runningOllama|0.32.15",
    );
    // The refresh is underway, the snapshot is what is shown -> "Checking…".
    expect(screen.getByTestId("overview-checking")).toBeDefined();
  });

  it("drops the checking line and writes the snapshot once live data arrives", async () => {
    writeOverviewSnapshot("ollama", snapshotPayload());
    installFetchMock({ overview: "live" });
    renderPanel();

    await waitFor(() =>
      expect(screen.queryByTestId("overview-checking")).toBeNull(),
    );
    const stored = JSON.parse(
      String(window.localStorage.getItem(snapshotKey("ollama"))),
    );
    expect(stored.data.server.disk_bytes).toBe(12 * 1024 ** 3);
  });

  it("reads the overview route when the backend has it", async () => {
    const fetchMock = installFetchMock({ overview: "live" });
    renderPanel();

    await screen.findByTestId("model-card-chat");
    const urls = fetchMock.mock.calls.map(([u]) => String(u));
    expect(urls).toContain(`${BASE}/overview`);
    expect(urls).not.toContain(`${BASE}/roles`);
    expect(urls).not.toContain(`${BASE}/server`);
  });

  it("composes the four legacy reads when the route answers 404", async () => {
    const fetchMock = installFetchMock({ overview: "404" });
    renderPanel();

    await screen.findByTestId("model-card-chat");
    const urls = fetchMock.mock.calls.map(([u]) => String(u));
    expect(urls).toContain(`${BASE}/overview`);
    expect(urls).toContain(`${BASE}/roles`);
    expect(urls).toContain(`${BASE}/inventory`);
    expect(urls).toContain(`${BASE}/server`);
    expect(urls).toContain(`${BASE}/catalog/recommended`);
  });
});

describe("OverviewPanel grid", () => {
  it("shows the four jobs as peers, in reading order, each with its own picker", async () => {
    installFetchMock();
    renderPanel();

    await screen.findByTestId("model-card-chat");
    const grid = screen.getByTestId("model-card-grid");
    const cards = [...grid.querySelectorAll("[data-testid^='model-card-']")].filter(
      (el) => el.tagName === "ARTICLE",
    );
    expect(cards.map((c) => c.getAttribute("data-testid"))).toEqual([
      "model-card-chat",
      "model-card-voice",
      "model-card-tools_screen",
      "model-card-deep",
    ]);
    // Every job on the page can be changed, side row included.
    for (const job of ["chat", "voice", "tools_screen", "deep", "embedding"])
      expect(screen.getByTestId(`role-picker-${job}`)).toBeDefined();
  });

  it("carries each job's state on the card so the grid reads at a glance", async () => {
    installFetchMock({
      roles: ROLES.map((r) =>
        r.id === "deep" ? { ...r, current: "gone:7b", installed: false } : r,
      ),
    });
    renderPanel();

    await screen.findByTestId("model-card-chat");
    expect(screen.getByTestId("model-card-chat").dataset.state).toBe("ready");
    expect(screen.getByTestId("model-card-deep").dataset.state).toBe("missing");
    expect(screen.getByTestId("model-card-tools_screen").dataset.state).toBe(
      "empty",
    );
    expect(screen.getByTestId("model-card-deep").textContent).toContain(
      "local_models.jobs.not_on_disk",
    );
  });

  it("sizes the memory bar from the model against this machine's graphics memory", async () => {
    installFetchMock({
      models: [{ name: "qwen3.5:4b", size_bytes: 4 * 1024 ** 3 }],
      accelerator_gb: 16,
    });
    renderPanel();

    await screen.findByTestId("model-card-chat");
    const bar = screen.getByTestId("model-card-memory-chat");
    // 4 GB of weights plus the context estimate, against 16 GB.
    await waitFor(() =>
      expect(bar.textContent).toContain("local_models.jobs.memory_line4.0 GB"),
    );
    expect(
      (screen.getByTestId("model-card-memory-fill-chat") as HTMLElement).style.width,
    ).toBe("25%");
  });

  it("warns on the card when the model is larger than the graphics memory", async () => {
    installFetchMock({
      models: [{ name: "qwen3.5:4b", size_bytes: 20 * 1024 ** 3 }],
      accelerator_gb: 8,
    });
    renderPanel();

    await screen.findByTestId("model-card-chat");
    const bar = screen.getByTestId("model-card-memory-chat");
    await waitFor(() =>
      expect(bar.textContent).toContain("local_models.jobs.memory_over8.0"),
    );
    // Clamped, not spilling out of its track.
    const width = (screen.getByTestId("model-card-memory-fill-chat") as HTMLElement)
      .style.width;
    expect(parseInt(width, 10)).toBeGreaterThan(0);
    expect(parseInt(width, 10)).toBeLessThanOrEqual(100);
  });

  it("writes a pick through PUT roles/{role}", async () => {
    const fetchMock = installFetchMock();
    renderPanel();

    await screen.findByTestId("model-card-deep");
    fireEvent.click(screen.getByTestId("role-picker-deep"));
    fireEvent.click(screen.getByTestId("role-option-qwen3.5:4b"));

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

  it("downloads, assigns and tunes the recommendation from the card", async () => {
    const fetchMock = installFetchMock({
      // An empty chat job: the recommendation button has a reason to be there.
      roles: ROLES.map((r) =>
        r.id === "chat" ? { ...r, current: "", installed: false } : r,
      ),
    });
    renderPanel();

    await screen.findByTestId("model-card-chat");
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

  it("gives speech a card of its own, with the window it runs on", async () => {
    installFetchMock({
      models: [{ name: "qwen3.5:4b", size_bytes: 3 * 1024 ** 3, context_length: 262144 }],
      roles: ROLES.map((r) =>
        r.id === "voice"
          ? { ...r, context_tokens: 32768, context_source: "automatic" }
          : r,
      ),
    });
    renderPanel();

    const card = await screen.findByTestId("model-card-voice");
    expect(card.textContent).toContain("local_models.jobs.voice_purpose");
    expect(screen.getByTestId("role-picker-voice")).toBeDefined();
    // The window the call runs with, against the model's native one.
    expect(screen.getByTestId("model-card-chips-voice").textContent).toContain(
      "local_models.jobs.context_of32k|256k",
    );
  });

  it("names what is missing when the job's model cannot do it, and offers the pick", async () => {
    installFetchMock({
      roles: ROLES.map((r) =>
        r.id === "tools_screen"
          ? {
              ...r,
              current: "qwen3.5:4b",
              installed: true,
              current_fit: "unfit",
              current_reason: "no vision",
              recommended: "gemma4:12b-it-qat",
            }
          : r,
      ),
    });
    renderPanel();

    const card = await screen.findByTestId("model-card-tools_screen");
    expect(card.dataset.state).toBe("unfit");
    expect(screen.getByTestId("model-card-verdict-tools_screen").textContent).toContain(
      "local_models.jobs.fit_unfitno vision",
    );
    expect(card.textContent).toContain(
      "local_models.roles.download_recommendedgemma4:12b-it-qat",
    );
    // A fine choice is not nagged: the chat card, on a fit, shows no switch button.
    expect(screen.getByTestId("model-card-chat").textContent).not.toContain(
      "local_models.jobs.switch_to",
    );
  });

  it("shows the readable name with the tag beside it, and the strip of what is loaded at once", async () => {
    installFetchMock({
      models: [
        {
          name: "qwen3.5:4b",
          size_bytes: 3 * 1024 ** 3,
          display_label: "Qwen 3.5 4B",
          params_label: "4B",
          quant_label: "Q4_K_M",
        },
      ],
      resident: {
        items: [
          {
            tag: "qwen3.5:4b",
            display_label: "Qwen 3.5 4B",
            roles: ["chat", "voice"],
            weights_gb: 3.0,
            context_gb: 0.7,
            context_tokens: 8192,
            loaded: true,
          },
        ],
        reserve_gb: 4.0,
        total_gb: 7.7,
        accelerator_gb: 16,
        over: false,
      },
    });
    renderPanel();

    await screen.findByTestId("model-card-chat");
    expect(screen.getByTestId("model-card-current-chat").textContent).toBe("Qwen 3.5 4B");
    expect(screen.getByTestId("model-card-tag-chat").textContent).toBe("qwen3.5:4b");
    const strip = screen.getByTestId("memory-strip");
    expect(strip.dataset.over).toBe("false");
    expect(screen.getByTestId("memory-strip-total").textContent).toContain(
      "local_models.overview.resident_fits7.7 GB|16.0",
    );
    expect(strip.textContent).toContain("local_models.role_chat · local_models.role_voice");
    expect(strip.textContent).toContain("local_models.overview.resident_reserve");
  });

  it("blocks the card, rather than offering a doomed picker, when a job has no server", async () => {
    installFetchMock({
      roles: ROLES.map((r) =>
        r.id === "voice"
          ? {
              ...r,
              current: "",
              installed: false,
              note: "The managed voice server is not installed.",
            }
          : r,
      ),
    });
    renderPanel();

    const card = await screen.findByTestId("model-card-voice");
    expect(card.dataset.state).toBe("blocked");
    expect(card.textContent).toContain(
      "The managed voice server is not installed.",
    );
    expect(screen.queryByTestId("role-picker-voice")).toBeNull();
  });

  it("keeps embeddings settable in the side row instead of the grid", async () => {
    installFetchMock();
    renderPanel();

    await screen.findByTestId("model-card-chat");
    expect(screen.queryByTestId("model-card-embedding")).toBeNull();
    const side = await screen.findByTestId("side-job-embedding");
    expect(side.textContent).toContain("local_models.role_embedding");
    expect(side.textContent).toContain("local_models.jobs.embedding_purpose");
    // The setting has nowhere else to live, so the row keeps the picker.
    expect(within(side).getByTestId("role-picker-embedding")).toBeDefined();
  });

  it("keeps every control on the card, and the plumbing one fold away", async () => {
    installFetchMock();
    renderPanel({ onTune: vi.fn() });
    await screen.findByTestId("model-card-chat");
    expect(screen.getByTestId("role-picker-chat")).toBeDefined();
    // Tune sits beside the picker on every card with an installed model.
    expect(screen.getAllByText("local_models.roles.tune").length).toBeGreaterThan(0);
    // The job's requirements are in the Details fold, not on the face.
    expect(screen.getAllByText("tools").length).toBeGreaterThan(0);
    expect(screen.getAllByText("local_models.jobs.details").length).toBeGreaterThan(0);
    // The jobs that follow another card's pick, as a footnote.
    expect(screen.getByTestId("roles-more").textContent).toContain(
      "local_models.role_ack",
    );
  });
});

describe("OverviewPanel launch bar", () => {
  it("adds up what the picks cost and names the largest against the card", async () => {
    installFetchMock({
      models: [
        { name: "qwen3.5:4b", size_bytes: 3 * 1024 ** 3 },
        { name: "qwen3-embedding:4b", size_bytes: 2 * 1024 ** 3 },
      ],
      accelerator_gb: 16,
      roles: ROLES.map((r) =>
        r.id === "voice" || r.id === "embedding"
          ? { ...r, current: "qwen3-embedding:4b", installed: true }
          : r.id === "tools_screen" || r.id === "deep"
            ? { ...r, current: "qwen3.5:4b", installed: true }
            : r,
      ),
    });
    renderPanel();

    const facts = await screen.findByTestId("launch-facts");
    // chat + tools + deep share qwen3.5:4b, voice + embeddings add 2 GB -> 5 GB.
    await waitFor(() =>
      expect(facts.textContent).toContain(
        "local_models.launch.total_on_disk5.0 GB",
      ),
    );
    expect(facts.textContent).toContain(
      "local_models.launch.largest_fits3.0 GB|16.0",
    );
    expect(screen.getByTestId("server-launch").textContent).toContain(
      "local_models.launch.all_picked",
    );
  });

  it("says how many jobs are still open without blocking the button", async () => {
    installFetchMock();
    renderPanel();

    const launch = await screen.findByTestId("server-launch");
    // chat and voice are set in the fixture, tools, deep & embeddings are not: 2 of 5.
    await waitFor(() =>
      expect(launch.textContent).toContain(
        "local_models.launch.partly_picked2|5",
      ),
    );
    const button = within(screen.getByTestId("launch-run")).getByRole("button");
    // Filling the rest is exactly what this button is for.
    expect(button.hasAttribute("disabled")).toBe(false);
  });

  it("names the install when the server is not there yet", async () => {
    installFetchMock({ server: { ...SERVER, installed: false, running: false } });
    renderPanel();

    expect(
      await screen.findByRole("button", {
        name: /local_models\.launch\.action_installOllama/,
      }),
    ).toBeDefined();
  });

  it("runs the flow, reports what it did, then offers the brain switch", async () => {
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

    await screen.findByTestId("launch-run");
    fireEvent.click(
      within(screen.getByTestId("launch-run")).getByRole("button"),
    );

    const progress = await screen.findByTestId("setup-progress");
    await screen.findByText("local_models.overview.setup_done");
    expect(progress.textContent).toContain(
      "local_models.overview.setup_assignedlocal_models.role_chat|qwen3.8:27b",
    );
    expect(progress.textContent).toContain(
      "local_models.overview.setup_kept_one",
    );
    expect(progress.textContent).toContain(
      "local_models.verify.server — local_models.verify.ok · local_models.verify.ms12",
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
});

describe("OverviewPanel notices", () => {
  it("names the active brain when it is not the local server", async () => {
    installFetchMock();
    mockProviders.providers = [
      { id: "gemini", label: "Gemini", tier: "brain", active: true },
    ];
    renderPanel({ onOpenApiKeys: vi.fn() });

    const clause = await screen.findByTestId("overview-brain-clause");
    expect(clause.textContent).toContain(
      "local_models.overview.status_brain_other" +
        "local_models.overview.status_generic_server|Gemini",
    );
    expect(clause.textContent).toContain("local_models.roles.other_brain_link");
  });

  it("does not claim the server was silent when it answered with nothing", async () => {
    installFetchMock({ models: [] });
    renderPanel();
    await screen.findByTestId("model-card-chat");
    expect(screen.queryByTestId("roles-server-silent")).toBeNull();
  });

  it("lists every installed model, and offers Browse and Manage", async () => {
    installFetchMock({
      models: [{ name: "qwen3.5:4b" }, { name: "qwen3-embedding:4b" }],
    });
    const onManage = vi.fn();
    const onBrowse = vi.fn();
    renderPanel({ onManage, onBrowse });

    await screen.findByTestId("installed-qwen3.5:4b");
    expect(
      screen.getByTestId("installed-recommended-qwen3-embedding:4b").textContent,
    ).toContain("local_models.role_embedding");

    fireEvent.click(
      screen.getByRole("button", { name: "local_models.installed.manage" }),
    );
    expect(onManage).toHaveBeenCalledTimes(1);
    fireEvent.click(
      screen.getByRole("button", { name: "local_models.installed.browse" }),
    );
    expect(onBrowse).toHaveBeenCalledTimes(1);
  });
});
