import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/i18n", () => ({
  useT: () => (key: string) => key,
  fill: (template: string, vars: Record<string, string | number>) =>
    template.replace(/\{(\w+)\}/g, (m, key: string) =>
      key in vars ? String(vars[key]) : m,
    ),
}));

import { TuneSheet } from "./TuneSheet";
import type { LocalModelRow } from "@/hooks/useLocalModels";

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
      } as Response;
    },
  );
  (globalThis as unknown as { fetch: typeof fetch }).fetch =
    fetchMock as unknown as typeof fetch;
}

const MODEL: LocalModelRow = {
  name: "qwen3.5:4b",
  size_bytes: 3 * 1024 ** 3,
  digest: "abc",
  modified_at: "2026-08-20T10:00:00Z",
  family: "qwen3",
  parameter_size: "4.0B",
  quantization_level: "Q4_K_M",
  context_length: 32768,
  capabilities: ["completion", "tools", "thinking"],
  license: "Apache-2.0",
  probed: true,
  used_by: [],
  loaded: false,
  size_vram_bytes: 0,
  expires_at: "",
  running_context_length: null,
};

const BASE = "/api/providers/ollama/local-models/models/qwen3.5%3A4b";

function routes(overrides: Partial<Record<string, RouteResult>> = {}): Handler {
  return (method, url) => {
    const key = `${method} ${url}`;
    if (overrides[key]) return overrides[key];
    if (key === `GET ${BASE}/options`)
      return {
        body: {
          model: "qwen3.5:4b",
          options: {},
          configured: false,
          profile_alias: null,
        },
      };
    if (key === `GET ${BASE}/suggested-options`)
      return {
        body: {
          model: "qwen3.5:4b",
          options: { num_ctx: 16384, num_gpu: -1, keep_alive: "30m" },
          reasons: [
            "A 16k context fits beside the model.",
            "The whole model fits on the graphics card.",
          ],
          size_gb: 3,
          native_context: 32768,
          accelerator_gb: 16,
          accelerator_source: "nvidia-smi",
          ram_gb: 32,
        },
      };
    return undefined;
  };
}

function renderSheet(model: LocalModelRow = MODEL) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onClose = vi.fn();
  render(
    <QueryClientProvider client={qc}>
      <TuneSheet providerId="ollama" model={model} onClose={onClose} />
    </QueryClientProvider>,
  );
  return { onClose };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("TuneSheet", () => {
  it("lists the machine's reasons, caps the context chips at the native window and applies the suggestion", async () => {
    installFetch(routes());
    renderSheet();

    const reasons = await screen.findByTestId("tune-reasons");
    expect(reasons.textContent).toContain("16k context fits");

    // 4k..16k plus the native 32k; 64k and 128k are beyond the model.
    expect(screen.getByTestId("tune-ctx-4096")).toBeDefined();
    expect(screen.getByTestId("tune-ctx-16384")).toBeDefined();
    expect(screen.getByTestId("tune-ctx-32768").textContent).toContain(
      "local_models.tune.chip_native",
    );
    expect(screen.queryByTestId("tune-ctx-65536")).toBeNull();
    // Thinking chips are there because the model declares the capability.
    expect(screen.getByTestId("tune-think-high")).toBeDefined();

    fireEvent.click(
      screen.getByRole("button", { name: "local_models.tune.suggested_apply" }),
    );
    expect(
      screen.getByTestId("tune-ctx-16384").getAttribute("aria-pressed"),
    ).toBe("true");
    expect(
      screen.getByTestId("tune-gpu-all").getAttribute("aria-pressed"),
    ).toBe("true");
    expect(
      screen.getByTestId("tune-keep-30m").getAttribute("aria-pressed"),
    ).toBe("true");
    expect(
      screen.getByText("local_models.tune.footnote_will_bake"),
    ).toBeDefined();
  });

  it("saves the whole compacted set and reads back the alias sentence", async () => {
    const saved = {
      model: "qwen3.5:4b",
      options: { num_ctx: 8192, keep_alive: -1, think: "low" },
      configured: true,
      profile_alias: "qwen3.5-4b-jarvis-1a2b3c4d",
    };
    let stored: RouteResult | undefined;
    const base = routes();
    installFetch((method, url, body) => {
      if (`${method} ${url}` === `PUT ${BASE}/options`) {
        // The re-read after the save sees what was written.
        stored = { body: saved };
        return stored;
      }
      if (`${method} ${url}` === `GET ${BASE}/options` && stored) return stored;
      return base(method, url, body);
    });
    renderSheet();
    await screen.findByTestId("tune-reasons");

    fireEvent.click(screen.getByTestId("tune-ctx-8192"));
    fireEvent.click(screen.getByTestId("tune-keep-forever"));
    fireEvent.click(screen.getByTestId("tune-think-low"));
    fireEvent.click(
      screen.getByRole("button", { name: "local_models.tune.save" }),
    );

    const put = await waitFor(() => {
      const c = calls.find((x) => x.method === "PUT");
      if (!c) throw new Error("no PUT yet");
      return c;
    });
    expect(put.url).toBe(`${BASE}/options`);
    expect(put.body).toEqual({ num_ctx: 8192, keep_alive: -1, think: "low" });

    const readback = await screen.findByTestId("tune-readback");
    expect(readback.textContent).toBe("local_models.tune.saved_with_alias");
    await waitFor(() =>
      expect(
        screen.getByText("local_models.tune.footnote_alias"),
      ).toBeDefined(),
    );
  });

  it("hides the thinking knob for a model without that capability and resets with DELETE", async () => {
    installFetch(
      routes({
        [`GET ${BASE}/options`]: {
          body: {
            model: "qwen3.5:4b",
            options: { num_ctx: 4096 },
            configured: true,
            profile_alias: "qwen3.5-4b-jarvis-deadbeef",
          },
        },
        [`DELETE ${BASE}/options`]: {
          body: {
            model: "qwen3.5:4b",
            options: {},
            configured: false,
            profile_alias: null,
          },
        },
      }),
    );
    renderSheet({ ...MODEL, capabilities: ["completion"] });
    await screen.findByTestId("tune-reasons");

    expect(screen.queryByTestId("tune-think-high")).toBeNull();
    // The stored set arrives in the draft.
    await waitFor(() =>
      expect(
        screen.getByTestId("tune-ctx-4096").getAttribute("aria-pressed"),
      ).toBe("true"),
    );

    fireEvent.click(
      screen.getByRole("button", { name: "local_models.tune.reset" }),
    );
    await waitFor(() =>
      expect(calls.some((c) => c.method === "DELETE")).toBe(true),
    );
    expect((await screen.findByTestId("tune-readback")).textContent).toBe(
      "local_models.tune.reset_done",
    );
  });

  it("shows the backend's sentence when a save fails", async () => {
    installFetch(
      routes({
        [`PUT ${BASE}/options`]: {
          status: 503,
          body: { detail: "Ollama is not answering." },
        },
      }),
    );
    renderSheet();
    await screen.findByTestId("tune-reasons");
    fireEvent.click(
      screen.getByRole("button", { name: "local_models.tune.save" }),
    );
    expect((await screen.findByTestId("tune-failure")).textContent).toBe(
      "Ollama is not answering.",
    );
  });
});
