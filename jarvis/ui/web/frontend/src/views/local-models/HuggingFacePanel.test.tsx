/**
 * HuggingFacePanel — off by default, browse when on, pull one quantization.
 *
 * No jest-dom in this repo — assertions use toBeTruthy()/toBeNull().
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockState } = vi.hoisted(() => ({
  mockState: { setActiveSection: vi.fn() },
}));

vi.mock("@/store/events", () => ({
  useEventStore: (selector: (s: typeof mockState) => unknown) =>
    selector(mockState),
}));

vi.mock("@/i18n", () => ({
  // Identity translator: assertions match the keys themselves.
  useT: () => (key: string) => key,
  useUiLanguage: () => "en",
}));

const openExternalUrl = vi.fn();
vi.mock("@/lib/openExternal", () => ({
  openExternalUrl: (url: string) => openExternalUrl(url),
}));

import {
  HuggingFacePanel,
  formatContext,
  formatParams,
} from "./HuggingFacePanel";

const BASE = "/api/providers/ollama/local-models";

const REPO = {
  id: "unsloth/Qwen3.8-27B-GGUF",
  author: "unsloth",
  downloads: 123456,
  likes: 42,
  last_modified: "2026-08-20T10:00:00Z",
  architecture: "qwen3",
  total_params: 27_000_000_000,
  context_length: 262144,
};

const FILES = [
  {
    filename: "Qwen3.8-27B-Q4_K_M.gguf",
    quant: "Q4_K_M",
    size_gb: 16.5,
    fit: "comfortable",
    fit_note: "",
  },
  {
    filename: "Qwen3.8-27B-Q8_0.gguf",
    quant: "Q8_0",
    size_gb: 28.9,
    fit: "tight",
    fit_note: "Needs more memory than this machine has free.",
  },
];

type Answer = { ok?: boolean; status?: number; body: unknown };

function installFetch(
  routes: (url: string, method: string, body: unknown) => Answer | undefined,
) {
  let enabled = false;
  const fetchMock = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      if (url === `${BASE}/hf/enabled` && method === "GET") {
        return { ok: true, status: 200, json: async () => ({ enabled }) };
      }
      if (url === `${BASE}/hf/enabled` && method === "PUT") {
        enabled = Boolean((body as { enabled: boolean }).enabled);
        return { ok: true, status: 200, json: async () => ({ enabled }) };
      }
      const hit = routes(url, method, body);
      if (!hit) throw new Error(`unexpected fetch ${method} ${url}`);
      return {
        ok: hit.ok ?? true,
        status: hit.status ?? 200,
        json: async () => hit.body,
      };
    },
  );
  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, setEnabled: (v: boolean) => (enabled = v) };
}

function renderPanel() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <HuggingFacePanel providerId="ollama" />
    </QueryClientProvider>,
  );
}

async function typeAndSettle(text: string) {
  fireEvent.change(screen.getByRole("textbox"), { target: { value: text } });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(400);
  });
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  mockState.setActiveSection = vi.fn();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  vi.clearAllMocks();
});

describe("HuggingFacePanel", () => {
  it("starts off, shows only the toggle sentence and makes no browse request", async () => {
    const { fetchMock } = installFetch(() => undefined);
    renderPanel();

    await waitFor(() =>
      expect(screen.getByTestId("hf-enabled-switch")).toBeTruthy(),
    );
    expect(
      screen.getByText("local_models.huggingface.toggle_sentence"),
    ).toBeTruthy();
    expect(screen.queryByRole("textbox")).toBeNull();
    const urls = fetchMock.mock.calls.map((c) => String(c[0]));
    expect(urls.every((u) => u === `${BASE}/hf/enabled`)).toBe(true);
  });

  it("switching on writes hf/enabled and reveals search, sort chips and the notes", async () => {
    const { fetchMock } = installFetch(() => undefined);
    renderPanel();

    const toggle = await screen.findByTestId("hf-enabled-switch");
    fireEvent.click(toggle);

    await waitFor(() => expect(screen.getByRole("textbox")).toBeTruthy());
    const put = fetchMock.mock.calls.find(
      (c) => (c[1] as RequestInit | undefined)?.method === "PUT",
    );
    expect(put).toBeTruthy();
    expect(JSON.parse(String((put![1] as RequestInit).body))).toEqual({
      enabled: true,
    });

    expect(
      screen.getByRole("tab", {
        name: "local_models.huggingface.sort_downloads",
      }),
    ).toBeTruthy();
    expect(
      screen.getByRole("tab", { name: "local_models.huggingface.sort_newest" }),
    ).toBeTruthy();
    expect(
      screen.getByRole("tab", {
        name: "local_models.huggingface.sort_trending",
      }),
    ).toBeTruthy();
    expect(
      screen.getByText("local_models.huggingface.empty_query"),
    ).toBeTruthy();
    expect(
      screen.getByText("local_models.huggingface.token_note"),
    ).toBeTruthy();
    expect(
      screen.getByText("local_models.huggingface.private_hint"),
    ).toBeTruthy();

    fireEvent.click(screen.getByText("local_models.huggingface.token_link"));
    expect(mockState.setActiveSection).toHaveBeenCalledWith("apikeys");
    fireEvent.click(screen.getByText("hf.co/settings/keys"));
    expect(openExternalUrl).toHaveBeenCalledWith(
      "https://huggingface.co/settings/keys",
    );
  });

  it("searches with the chosen sort, expands a repository and pulls a quantization with inline progress", async () => {
    let polls = 0;
    const { fetchMock, setEnabled } = installFetch((url, method) => {
      if (url.startsWith(`${BASE}/hf/search?`))
        return { body: { repos: [REPO], error: null } };
      if (url === `${BASE}/hf/unsloth/Qwen3.8-27B-GGUF/files`)
        return { body: { files: FILES, error: null } };
      if (url === `${BASE}/hf/pull` && method === "POST")
        return {
          body: {
            state: "running",
            model: "hf.co/unsloth/Qwen3.8-27B-GGUF:Q4_K_M",
            message: "",
            percent: 0,
          },
        };
      if (url.startsWith("/api/providers/ollama/pull/status?")) {
        polls += 1;
        return {
          body:
            polls < 2
              ? {
                  state: "running",
                  model: "hf.co/unsloth/Qwen3.8-27B-GGUF:Q4_K_M",
                  message: "pulling",
                  percent: 42,
                }
              : {
                  state: "done",
                  model: "hf.co/unsloth/Qwen3.8-27B-GGUF:Q4_K_M",
                  message: "Installed.",
                  percent: 100,
                },
        };
      }
      return undefined;
    });
    setEnabled(true);
    renderPanel();

    await screen.findByRole("textbox");
    fireEvent.click(
      screen.getByRole("tab", { name: "local_models.huggingface.sort_newest" }),
    );
    await typeAndSettle("qwen3.8");

    await waitFor(() =>
      expect(screen.getByTestId(`hf-repo-${REPO.id}`)).toBeTruthy(),
    );
    const searchUrl = fetchMock.mock.calls
      .map((c) => String(c[0]))
      .find((u) => u.includes("/hf/search?"));
    expect(searchUrl).toContain("q=qwen3.8");
    expect(searchUrl).toContain("sort=lastModified");
    // Row facts: author, architecture, params, context.
    expect(screen.getByText("unsloth")).toBeTruthy();
    expect(screen.getByText("qwen3")).toBeTruthy();
    expect(
      screen.getByText("local_models.huggingface.col_params 27B"),
    ).toBeTruthy();
    expect(
      screen.getByText("local_models.huggingface.col_context 256K"),
    ).toBeTruthy();

    fireEvent.click(
      screen.getByRole("button", { name: /unsloth\/Qwen3\.8-27B-GGUF/ }),
    );
    await waitFor(() =>
      expect(
        screen.getByTestId("hf-file-Qwen3.8-27B-Q4_K_M.gguf"),
      ).toBeTruthy(),
    );
    expect(screen.getByText("Q8_0")).toBeTruthy();
    expect(screen.getByText("16.50 GB")).toBeTruthy();
    expect(screen.getByText("local_models.huggingface.fit_tight")).toBeTruthy();
    expect(
      screen.getByText("Needs more memory than this machine has free."),
    ).toBeTruthy();

    fireEvent.click(
      screen.getByRole("button", {
        name: "local_models.huggingface.pull Q4_K_M",
      }),
    );
    await waitFor(() => {
      const post = fetchMock.mock.calls.find(
        (c) => String(c[0]) === `${BASE}/hf/pull`,
      );
      expect(post).toBeTruthy();
      expect(JSON.parse(String((post![1] as RequestInit).body))).toEqual({
        user: "unsloth",
        repo: "Qwen3.8-27B-GGUF",
        quant: "Q4_K_M",
      });
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2600);
    });
    await waitFor(() => expect(screen.getByText(/42%/)).toBeTruthy());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2600);
    });
    await waitFor(() => expect(screen.getByText("Installed.")).toBeTruthy());
    expect(
      screen.getByText("local_models.huggingface.pull_again"),
    ).toBeTruthy();
    const statusUrl = fetchMock.mock.calls
      .map((c) => String(c[0]))
      .find((u) => u.includes("/pull/status?"));
    expect(statusUrl).toContain(
      encodeURIComponent("hf.co/unsloth/Qwen3.8-27B-GGUF:Q4_K_M"),
    );
  });

  it("shows the backend's rate-limit sentence instead of an empty list", async () => {
    const { setEnabled } = installFetch((url) => {
      if (url.startsWith(`${BASE}/hf/search?`))
        return {
          body: {
            repos: [],
            error: "Hugging Face is rate-limiting this address for 5 minutes.",
          },
        };
      return undefined;
    });
    setEnabled(true);
    renderPanel();
    await screen.findByRole("textbox");
    await typeAndSettle("llama");
    await waitFor(() =>
      expect(
        screen.getByText(
          "Hugging Face is rate-limiting this address for 5 minutes.",
        ),
      ).toBeTruthy(),
    );
  });

  it("shows the 404 sentence when the backend says browsing is off", async () => {
    const { setEnabled } = installFetch((url) => {
      if (url.startsWith(`${BASE}/hf/search?`))
        return {
          ok: false,
          status: 404,
          body: { detail: "Hugging Face browsing is switched off." },
        };
      return undefined;
    });
    setEnabled(true);
    renderPanel();
    await screen.findByRole("textbox");
    await typeAndSettle("llama");
    await waitFor(() =>
      expect(
        screen.getByText("Hugging Face browsing is switched off."),
      ).toBeTruthy(),
    );
  });
});

describe("formatters", () => {
  it("abbreviate parameters and context the way the hub does", () => {
    expect(formatParams(null)).toBe("—");
    expect(formatParams(8_030_000_000)).toBe("8B");
    expect(formatParams(1_500_000_000)).toBe("1.5B");
    expect(formatParams(540_000_000)).toBe("540M");
    expect(formatContext(null)).toBe("—");
    expect(formatContext(131072)).toBe("128K");
    expect(formatContext(512)).toBe("512");
  });
});
