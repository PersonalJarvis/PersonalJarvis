import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Keys whose real strings carry a placeholder: the identity translator keeps
// the key AND the token, so `fill` can be asserted on.
const TEMPLATED: Record<string, string> = {
  "local_models.catalogue.reviewed": "local_models.catalogue.reviewed {date}",
  "local_models.catalogue.pulling":
    "local_models.catalogue.pulling {model} {percent}",
  "local_models.catalogue.pull_done":
    "local_models.catalogue.pull_done {model}",
  "local_models.catalogue.pull_failed":
    "local_models.catalogue.pull_failed {model} {message}",
};

vi.mock("@/i18n", () => ({
  // Identity translator: assertions match the keys themselves.
  useT: () => (key: string) => TEMPLATED[key] ?? key,
  fill: (template: string, vars: Record<string, string | number>) =>
    template.replace(/\{(\w+)\}/g, (m, k: string) =>
      k in vars ? String(vars[k]) : m,
    ),
}));

import { CataloguePanel } from "./CataloguePanel";

const RECOMMENDED = {
  server: "http://localhost:11434",
  server_reachable: true,
  message: "",
  memory_gb: 32,
  accelerator_gb: 16,
  accelerator_source: "nvidia-smi",
  roles: ["chat", "coder", "embedding"],
  installed: ["qwen3.5:4b"],
  curated_reviewed_on: "2026-08-24",
  models: [
    {
      id: "qwen3.5:4b",
      label: "Qwen 3.5 4B",
      size_gb: 3.4,
      purpose: "Small and quick.",
      role: "chat",
      tools: true,
      vision: true,
      installed: true,
      fit: "comfortable",
      fit_note: "Fits in the 16 GB of graphics memory on this machine.",
      recommended: true,
      recommended_for: ["chat"],
    },
    {
      id: "ornith:9b",
      label: "Ornith 9B",
      size_gb: 5.6,
      purpose: "Agentic coding.",
      role: "coder",
      tools: true,
      vision: false,
      installed: false,
      fit: "tight",
      fit_note: "Tight against the 16 GB of graphics memory.",
      recommended: false,
      recommended_for: [],
    },
  ],
};

const LIBRARY = {
  query: "",
  sort: "newest",
  capability: null,
  error: null,
  models: [
    {
      name: "qwen3.8",
      description: "The current flagship.",
      capabilities: ["vision", "tools", "thinking"],
      cloud: false,
      sizes: ["27b"],
      pulls: "696K",
      updated: "1 week ago",
      installed: false,
    },
  ],
};

const TAGS = {
  model: "qwen3.8",
  error: null,
  tags: [
    {
      tag: "27b",
      id: "qwen3.8:27b",
      size_gb: 18,
      context: "256K",
      inputs: "Text, Image",
      updated: "1 week ago",
      cloud: false,
      installed: false,
      fit: "tight",
      fit_note: "Tight: 18 GB against 16 GB of graphics memory.",
      quantization: "q4_K_M",
    },
  ],
};

const ROLES = {
  provider: "ollama",
  server: "http://localhost:11434",
  error: null,
  roles: [
    {
      id: "chat",
      label_key: "local_models.role_chat",
      config_key: "brain.providers.ollama.model",
      current: "",
      installed: false,
      required: [],
      recommended_capabilities: ["tools"],
      qualifying: ["qwen3.5:4b"],
      recommended: "qwen3.5:4b",
      writable: true,
      advanced: false,
      note: "",
    },
  ],
};

type Route = (url: string, init?: RequestInit) => unknown;

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** Routes every fetch by URL; unknown paths answer 404 with a sentence. */
function stubFetch(route: Route) {
  const calls: string[] = [];
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    calls.push(`${init?.method ?? "GET"} ${url}`);
    const body = route(url, init);
    if (body === undefined)
      return jsonResponse({ detail: `No route for ${url}` }, 404);
    if (body instanceof Response) return body;
    return jsonResponse(body);
  });
  vi.stubGlobal("fetch", fn);
  return calls;
}

function defaultRoute(url: string): unknown {
  if (url.includes("/catalog/recommended")) return RECOMMENDED;
  if (url.includes("/catalog/qwen3.8/tags")) return TAGS;
  if (url.includes("/local-models/catalog")) return LIBRARY;
  if (url.includes("/local-models/roles")) return ROLES;
  return undefined;
}

function renderPanel() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <CataloguePanel providerId="ollama" />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.useRealTimers();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("CataloguePanel", () => {
  it("opens on the shortlist grouped by role with fit notes and the review date", async () => {
    const calls = stubFetch(defaultRoute);
    renderPanel();

    await waitFor(() =>
      expect(screen.getByTestId("catalogue-recommended")).toBeDefined(),
    );
    expect(calls.some((c) => c.includes("/catalog/recommended"))).toBe(true);
    // Browse-by-default fetches the shortlist, not the whole library.
    expect(calls.some((c) => /\/local-models\/catalog(\?|$)/.test(c))).toBe(
      false,
    );

    expect(screen.getByText("local_models.catalogue.group_chat")).toBeDefined();
    expect(
      screen.getByText("local_models.catalogue.group_coder"),
    ).toBeDefined();
    expect(
      screen.getByText("Fits in the 16 GB of graphics memory on this machine."),
    ).toBeDefined();
    expect(screen.getByTestId("catalogue-reviewed").textContent).toContain(
      "2026-08-24",
    );

    // Installed pick: "Installed" + Use for…; the other one offers Pull.
    const installed = screen.getByTestId("catalogue-pick-qwen3.5:4b");
    expect(installed.textContent).toContain("local_models.catalogue.installed");
    expect(installed.textContent).toContain("local_models.catalogue.use_for");
    const coder = screen.getByTestId("catalogue-pick-ornith:9b");
    expect(coder.textContent).toContain("local_models.catalogue.pull");
  });

  it("switches to Newest, renders the library and expands a row to its tag ledger", async () => {
    const calls = stubFetch(defaultRoute);
    renderPanel();
    await waitFor(() =>
      expect(screen.getByTestId("catalogue-recommended")).toBeDefined(),
    );

    fireEvent.click(
      screen.getByRole("tab", { name: "local_models.catalogue.mode_newest" }),
    );
    await waitFor(() => expect(screen.getByText("qwen3.8")).toBeDefined());
    expect(
      calls.some((c) => c.includes("/local-models/catalog?sort=newest")),
    ).toBe(true);
    expect(screen.getByText("696K")).toBeDefined();

    fireEvent.click(screen.getByRole("row", { name: "qwen3.8" }));
    await waitFor(() =>
      expect(screen.getByTestId("catalogue-tags-qwen3.8")).toBeDefined(),
    );
    expect(screen.getByText("qwen3.8:27b")).toBeDefined();
    expect(screen.getByText("q4_K_M")).toBeDefined();
    expect(screen.getByText("256K")).toBeDefined();
    expect(
      screen.getByText("Tight: 18 GB against 16 GB of graphics memory."),
    ).toBeDefined();

    // A capability chip narrows the library query.
    fireEvent.click(
      screen.getByRole("button", { name: "local_models.catalogue.cap_vision" }),
    );
    await waitFor(() =>
      expect(
        calls.some((c) => c.includes("sort=newest&capability=vision")),
      ).toBe(true),
    );
  });

  it("shows the backend sentence when the library is offline", async () => {
    stubFetch((url) => {
      if (url.includes("/catalog/recommended")) return RECOMMENDED;
      if (url.includes("/local-models/catalog")) {
        return {
          ...LIBRARY,
          models: [],
          error: "ollama.com did not answer within 10 seconds.",
        };
      }
      return defaultRoute(url);
    });
    renderPanel();
    await waitFor(() =>
      expect(screen.getByTestId("catalogue-recommended")).toBeDefined(),
    );

    fireEvent.click(
      screen.getByRole("tab", { name: "local_models.catalogue.mode_popular" }),
    );
    await waitFor(() =>
      expect(
        screen.getByText("ollama.com did not answer within 10 seconds."),
      ).toBeDefined(),
    );
  });

  it("pulls an exact name, shows progress and polls until done", async () => {
    let polls = 0;
    const calls = stubFetch((url, init) => {
      if (
        url.endsWith("/api/providers/ollama/pull") &&
        init?.method === "POST"
      ) {
        return {
          state: "running",
          model: "lfm2.5",
          message: "pulling manifest",
          percent: 0,
        };
      }
      if (url.includes("/pull/status")) {
        polls += 1;
        return polls < 2
          ? {
              state: "running",
              model: "lfm2.5",
              message: "downloading",
              percent: 40,
            }
          : {
              state: "done",
              model: "lfm2.5",
              message: "done",
              installed: true,
              percent: 100,
            };
      }
      return defaultRoute(url);
    });
    renderPanel();
    await waitFor(() =>
      expect(screen.getByTestId("catalogue-recommended")).toBeDefined(),
    );

    const field = screen.getByPlaceholderText(
      "local_models.catalogue.exact_placeholder",
    );
    fireEvent.change(field, { target: { value: "lfm2.5" } });
    fireEvent.click(
      screen.getByRole("button", { name: "local_models.catalogue.exact_pull" }),
    );

    await waitFor(() =>
      expect(screen.getByTestId("catalogue-pull-line")).toBeDefined(),
    );
    expect(calls.some((c) => c === "POST /api/providers/ollama/pull")).toBe(
      true,
    );
    expect(screen.getByTestId("catalogue-pull-line").textContent).toContain(
      "lfm2.5",
    );

    await act(async () => {
      await new Promise((r) => setTimeout(r, 2_700));
    });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 2_700));
    });
    await waitFor(() =>
      expect(screen.getByTestId("catalogue-pull-line").textContent).toContain(
        "local_models.catalogue.pull_done",
      ),
    );
    expect(
      calls.filter((c) => c.includes("/pull/status")).length,
    ).toBeGreaterThanOrEqual(2);
  }, 10_000);
});
