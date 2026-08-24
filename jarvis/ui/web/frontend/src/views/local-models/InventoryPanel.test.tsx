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

vi.mock("@/i18n", () => ({
  // Identity translator: assertions match the keys themselves.
  useT: () => (key: string) => key,
  useUiLanguage: () => "en",
  // Keys carry no placeholders, so the variables are appended instead — that
  // keeps two "Use for …" entries distinguishable by their role.
  fill: (template: string, vars: Record<string, string | number>) =>
    `${template} ${Object.values(vars).join(" ")}`.trim(),
}));

// The sheet has its own tests; here only its mount matters.
vi.mock("./TuneSheet", () => ({
  TuneSheet: ({ model }: { model: { name: string } }) => (
    <div data-testid={`tune-stub-${model.name}`} />
  ),
}));

import { InventoryPanel } from "./InventoryPanel";

interface RouteResult {
  status?: number;
  body: unknown;
}

type Handler = (
  method: string,
  url: string,
  body: unknown,
) => RouteResult | undefined;

const calls: { url: string; method: string; body: unknown }[] = [];

function installFetch(handler: Handler) {
  calls.length = 0;
  const fetchMock = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? "GET").toUpperCase();
      let body: unknown = null;
      if (typeof init?.body === "string") body = JSON.parse(init.body);
      calls.push({ url, method, body });
      const res = handler(method, url, body);
      if (!res) throw new Error(`unexpected fetch ${method} ${url}`);
      const status = res.status ?? 200;
      return {
        ok: status >= 200 && status < 300,
        status,
        json: async () => res.body,
        text: async () => JSON.stringify(res.body),
      } as Response;
    },
  );
  (globalThis as unknown as { fetch: typeof fetch }).fetch =
    fetchMock as unknown as typeof fetch;
}

const ROW = {
  name: "qwen3.5:4b",
  size_bytes: 3 * 1024 ** 3,
  digest: "abcdef0123456789",
  modified_at: "2026-08-20T10:00:00Z",
  family: "qwen3",
  parameter_size: "4.0B",
  quantization_level: "Q4_K_M",
  context_length: 131072,
  capabilities: ["completion", "tools", "thinking"],
  license: "Apache-2.0",
  probed: true,
  used_by: ["chat"],
  loaded: true,
  size_vram_bytes: 2 * 1024 ** 3,
  expires_at: new Date(Date.now() + 5 * 60_000).toISOString(),
  running_context_length: 16384,
};

const EMBED = {
  ...ROW,
  name: "embeddinggemma:latest",
  family: "gemma",
  parameter_size: "300M",
  capabilities: ["embedding"],
  used_by: [],
  loaded: false,
  size_vram_bytes: 0,
  expires_at: "",
  size_bytes: 600 * 1024 ** 2,
};

function inventoryBody(models = [ROW, EMBED]) {
  return {
    provider: "ollama",
    server: "http://127.0.0.1:11434",
    models,
    running: models
      .filter((m) => m.loaded)
      .map((m) => ({
        name: m.name,
        size_bytes: m.size_bytes,
        size_vram_bytes: m.size_vram_bytes,
        expires_at: m.expires_at,
        context_length: m.running_context_length,
        digest: m.digest,
      })),
    disk_bytes: 3.6 * 1024 ** 3,
    loaded_vram_bytes: 2 * 1024 ** 3,
    error: null,
  };
}

function renderPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <InventoryPanel providerId="ollama" />
    </QueryClientProvider>,
  );
}

const BASE = "/api/providers/ollama/local-models";

beforeEach(() => {
  installFetch((method, url) => {
    if (method === "GET" && url === `${BASE}/inventory`)
      return { body: inventoryBody() };
    return undefined;
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

async function openMenu(name: string) {
  // The identity translator gives every row the same menu label, so scope
  // the lookup to the row itself.
  const row = await screen.findByRole("row", { name });
  fireEvent.click(
    within(row).getByRole("button", {
      name: `local_models.inventory.menu_label ${name}`,
    }),
  );
}

describe("InventoryPanel", () => {
  it("renders every download with its facts, the loaded state and the stat tiles", async () => {
    renderPanel();

    const row = await screen.findByRole("row", { name: "qwen3.5:4b" });
    expect(row.textContent).toContain("qwen3");
    expect(row.textContent).toContain("4.0B");
    expect(row.textContent).toContain("Q4_K_M");
    expect(row.textContent).toContain("128k");
    expect(row.textContent).toContain("3.0 GB");
    expect(row.textContent).toContain("tools");
    // Loaded: VRAM, share of everything loaded, expiry.
    expect(row.textContent).toContain("2.0 GB · 100% · 5 min");
    // Used-by badge for the chat role.
    expect(row.textContent).toContain("local_models.inventory.role_chat");

    const embed = screen.getByRole("row", { name: "embeddinggemma:latest" });
    expect(embed.textContent).toContain("local_models.inventory.not_loaded");

    // Footer tiles.
    expect(
      screen.getByRole("group", { name: "local_models.inventory.stat_disk" })
        .textContent,
    ).toContain("3.6 GB");
    expect(
      screen.getByRole("group", { name: "local_models.inventory.stat_loaded" })
        .textContent,
    ).toContain("2.0 GB");
    expect(
      screen.getByRole("group", { name: "local_models.inventory.stat_total" })
        .textContent,
    ).toContain("2");
  });

  it("assigns a role from the menu and disables roles the model cannot serve", async () => {
    installFetch((method, url) => {
      if (method === "GET" && url === `${BASE}/inventory`)
        return { body: inventoryBody() };
      if (method === "PUT" && url === `${BASE}/roles/deep`)
        return {
          body: {
            ok: true,
            role: "deep",
            model: "qwen3.5:4b",
            config_key: "x",
            message: "Deep now uses qwen3.5:4b.",
          },
        };
      return undefined;
    });
    renderPanel();
    await openMenu("qwen3.5:4b");

    // Already the chat pick, no embedding capability -> both disabled.
    expect(
      (
        screen.getByRole("menuitem", {
          name: "local_models.inventory.use_for local_models.inventory.role_chat",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
    expect(
      (
        screen.getByRole("menuitem", {
          name: "local_models.inventory.use_for local_models.inventory.role_embedding",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);

    fireEvent.click(
      screen.getByRole("menuitem", {
        name: "local_models.inventory.use_for local_models.inventory.role_deep",
      }),
    );
    await waitFor(() =>
      expect(screen.getByTestId("inventory-notice").textContent).toBe(
        "Deep now uses qwen3.5:4b.",
      ),
    );
    const put = calls.find((c) => c.method === "PUT");
    expect(put?.url).toBe(`${BASE}/roles/deep`);
    expect(put?.body).toEqual({ model: "qwen3.5:4b" });
  });

  it("unloads a loaded model and opens Details and Tune inline", async () => {
    installFetch((method, url) => {
      if (method === "GET" && url === `${BASE}/inventory`)
        return { body: inventoryBody() };
      if (method === "GET" && url === `${BASE}/inventory/qwen3.5%3A4b`)
        return {
          body: {
            ...ROW,
            parameters: "num_ctx 8192",
            template: "{{ .Prompt }}",
          },
        };
      if (method === "POST" && url === `${BASE}/inventory/qwen3.5%3A4b/unload`)
        return {
          body: { ok: true, model: "qwen3.5:4b", message: "Unloaded." },
        };
      return undefined;
    });
    renderPanel();

    await openMenu("qwen3.5:4b");
    fireEvent.click(
      screen.getByRole("menuitem", {
        name: "local_models.inventory.action_details",
      }),
    );
    const details = await screen.findByTestId("details-qwen3.5:4b");
    expect(details.textContent).toContain("Apache-2.0");
    await waitFor(() => expect(details.textContent).toContain("num_ctx 8192"));

    await openMenu("qwen3.5:4b");
    fireEvent.click(
      screen.getByRole("menuitem", {
        name: "local_models.inventory.action_tune",
      }),
    );
    expect(screen.getByTestId("tune-stub-qwen3.5:4b")).toBeDefined();
    expect(screen.queryByTestId("details-qwen3.5:4b")).toBeNull();

    await openMenu("qwen3.5:4b");
    fireEvent.click(
      screen.getByRole("menuitem", {
        name: "local_models.inventory.action_unload",
      }),
    );
    await waitFor(() =>
      expect(screen.getByTestId("inventory-notice").textContent).toBe(
        "Unloaded.",
      ),
    );
    expect(
      calls.some((c) => c.method === "POST" && c.url.endsWith("/unload")),
    ).toBe(true);
  });

  it("deletes in two steps and turns a 409 into the sentence plus a reassign picker", async () => {
    let deletes = 0;
    installFetch((method, url) => {
      if (method === "GET" && url === `${BASE}/inventory`)
        return { body: inventoryBody() };
      if (
        method === "DELETE" &&
        url.startsWith(`${BASE}/inventory/embeddinggemma%3Alatest`)
      ) {
        deletes += 1;
        return {
          status: 409,
          body: {
            detail: "The embedding role still points at embeddinggemma:latest.",
          },
        };
      }
      return undefined;
    });
    renderPanel();

    await openMenu("embeddinggemma:latest");
    fireEvent.click(
      screen.getByRole("menuitem", {
        name: "local_models.inventory.action_delete",
      }),
    );
    // Nothing was deleted yet: the confirm drawer is the first step.
    expect(deletes).toBe(0);
    const drawer = await screen.findByTestId("delete-embeddinggemma:latest");
    expect(drawer.textContent).toContain(
      "local_models.inventory.delete_confirm",
    );
    expect(screen.queryByTestId("delete-reassign")).toBeNull();

    fireEvent.click(
      screen.getByRole("button", { name: "local_models.inventory.delete_do" }),
    );
    await waitFor(() => expect(deletes).toBe(1));
    expect(
      (await screen.findByTestId("delete-conflict")).textContent,
    ).toContain("still points at");
    // The sentence unlocks the reassign picker, and the button waits for a pick.
    expect(screen.getByTestId("delete-reassign")).toBeDefined();
    expect(
      (
        screen.getByRole("button", {
          name: "local_models.inventory.delete_do",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);

    // A model still in use needs the pick before the first attempt.
    fireEvent.click(
      screen.getByRole("button", { name: "local_models.inventory.cancel" }),
    );
    await openMenu("qwen3.5:4b");
    fireEvent.click(
      screen.getByRole("menuitem", {
        name: "local_models.inventory.action_delete",
      }),
    );
    await screen.findByTestId("delete-qwen3.5:4b");
    expect(
      (
        screen.getByRole("button", {
          name: "local_models.inventory.delete_do",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
    expect(screen.getByTestId("delete-reassign")).toBeDefined();
  });

  it("shows the backend sentence when the server is down", async () => {
    installFetch((method, url) => {
      if (method === "GET" && url === `${BASE}/inventory`)
        return {
          body: {
            ...inventoryBody([]),
            error: "Ollama is not answering on http://127.0.0.1:11434.",
          },
        };
      return undefined;
    });
    renderPanel();
    expect(
      (await screen.findByTestId("inventory-server-error")).textContent,
    ).toContain("not answering");
    expect(screen.getByText("local_models.inventory.empty")).toBeDefined();
  });
});
