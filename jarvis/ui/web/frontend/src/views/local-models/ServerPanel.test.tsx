import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockStore, mockProviders, clipboard } = vi.hoisted(() => ({
  mockStore: { pushToast: vi.fn(), assistantName: "Jarvis" },
  mockProviders: {
    providers: [] as Array<Record<string, unknown>>,
    loading: false,
    error: null as string | null,
    refetch: vi.fn(async () => undefined),
  },
  clipboard: { robustCopy: vi.fn(async () => true) },
}));

vi.mock("@/store/events", () => ({
  useEventStore: (selector: (s: typeof mockStore) => unknown) =>
    selector(mockStore),
}));

vi.mock("@/i18n", () => ({
  // Identity translator: assertions match the keys themselves; fill() appends
  // the variables so placeholders stay visible in the assertions.
  useT: () => (key: string) => key,
  fill: (template: string, vars: Record<string, string | number>) =>
    `${template}:${Object.values(vars).join(",")}`,
}));

vi.mock("@/hooks/useProviders", () => ({
  useProviders: () => mockProviders,
}));

vi.mock("@/lib/clipboard", () => clipboard);

// The provider-card panels have their own tests; here only their mount matters.
vi.mock("@/components/providers/ProviderTierSection", () => ({
  OllamaRuntimePanel: ({ alwaysVisible }: { alwaysVisible?: boolean }) => (
    <div
      data-testid="runtime-panel"
      data-always={alwaysVisible ? "true" : "false"}
    />
  ),
  BaseUrlField: () => <div data-testid="base-url-field" />,
}));

import { ServerPanel, formatExpiry, formatGb } from "./ServerPanel";

const OLLAMA = {
  id: "ollama",
  label: "Ollama",
  supports_model_pull: true,
  supports_base_url: true,
  base_url: "http://127.0.0.1:11434",
};

type Json = Record<string, unknown>;

function serverBody(overrides: Json = {}): Json {
  return {
    installed: true,
    binary: "C:/Users/me/AppData/Local/Programs/Ollama/ollama.exe",
    running: true,
    version: "0.32.15",
    detail: "Ollama 0.32.15 is running.",
    base_url: "http://127.0.0.1:11434",
    host_kind: "local",
    models_dir: "C:/Users/me/.ollama/models",
    running_models: [
      {
        name: "qwen3.5:4b",
        size_bytes: 3_400_000_000,
        size_vram_bytes: 3_650_722_201,
        expires_at: new Date(Date.now() + 4 * 60_000).toISOString(),
        context_length: 8192,
        digest: "abc",
      },
    ],
    disk_bytes: 21_474_836_480,
    loaded_vram_bytes: 3_650_722_201,
    error: null,
    ...overrides,
  };
}

const calls: Array<{ url: string; method: string; body: unknown }> = [];

function installFetch(server: Json, extra: Record<string, Json> = {}) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      calls.push({
        url,
        method,
        body: init?.body ? JSON.parse(String(init.body)) : null,
      });
      const respond = (body: Json, status = 200) =>
        new Response(JSON.stringify(body), {
          status,
          headers: { "Content-Type": "application/json" },
        });
      if (url.endsWith("/server") && method === "GET") return respond(server);
      if (url.endsWith("/server/test")) {
        return respond(
          extra.test ?? {
            ok: true,
            version: "0.32.15",
            latency_ms: 12,
            detail: "",
          },
        );
      }
      if (url.endsWith("/server/stop")) {
        return respond(extra.stop ?? { ok: true, message: "Ollama stopped." });
      }
      if (url.includes("/server/log")) {
        return respond(extra.log ?? { lines: ["line one", "line two"] });
      }
      if (url.includes("/server/env-guide")) {
        const os = new URL(url, "http://x").searchParams.get("os") ?? "windows";
        return respond({
          os,
          rows: [
            {
              key: "OLLAMA_HOST",
              purpose: "Address the server listens on.",
              command:
                os === "windows"
                  ? "setx OLLAMA_HOST 0.0.0.0"
                  : "launchctl setenv OLLAMA_HOST 0.0.0.0",
              restart: "Restart Ollama afterwards.",
            },
          ],
        });
      }
      if (url.includes("/unload")) {
        return respond({
          ok: true,
          model: "qwen3.5:4b",
          message: "Unloaded qwen3.5:4b.",
        });
      }
      if (url.endsWith("/runtime/autostart")) {
        const enabled =
          method === "PUT"
            ? Boolean(JSON.parse(String(init?.body)).enabled)
            : true;
        return respond({
          enabled,
          in_use: true,
          reason: enabled
            ? "local models serve the chat role"
            : "autostart is switched off in the Server tab",
        });
      }
      if (url.endsWith("/verify") && method === "POST") {
        return respond(
          extra.verify ?? {
            ok: false,
            status: "error",
            reason: "embeddinggemma: /api/embed failed",
            steps: [
              { id: "server", ok: true, model: "", detail: "Ollama 0.32.15", ms: 9 },
              { id: "chat", ok: true, model: "qwen3.5:4b", detail: "Answered.", ms: 2400 },
              {
                id: "embedding",
                ok: false,
                model: "embeddinggemma",
                detail: "/api/embed failed",
                ms: 30,
              },
            ],
          },
        );
      }
      return respond({ detail: `unexpected ${method} ${url}` }, 404);
    }),
  );
}

function renderPanel() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ServerPanel providerId="ollama" />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  calls.length = 0;
  mockProviders.providers = [OLLAMA];
  mockStore.pushToast = vi.fn();
  clipboard.robustCopy = vi.fn(async () => true);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("ServerPanel", () => {
  it("shows the host, the runtime, what is loaded and the facts for a local server", async () => {
    installFetch(serverBody());
    renderPanel();

    expect(screen.getByTestId("base-url-field")).toBeDefined();
    expect(
      screen.getByTestId("runtime-panel").getAttribute("data-always"),
    ).toBe("true");

    await waitFor(() => expect(screen.getByText("qwen3.5:4b")).toBeDefined());
    expect(screen.getByText("3.4 GB")).toBeDefined();
    expect(
      screen.getByText("local_models.server.expires_in_min:4"),
    ).toBeDefined();
    expect(screen.getByText("0.32.15")).toBeDefined();
    expect(screen.getByText("C:/Users/me/.ollama/models")).toBeDefined();
    expect(screen.getByText("20.0 GB")).toBeDefined();
    expect(
      screen.getByText("local_models.server.keep_alive_value:5m"),
    ).toBeDefined();
    expect(
      screen.getByRole("button", { name: "local_models.server.stop" }),
    ).toBeDefined();
  });

  it("probes the host and reports version and latency", async () => {
    installFetch(serverBody());
    renderPanel();
    await waitFor(() => expect(screen.getByText("qwen3.5:4b")).toBeDefined());

    fireEvent.click(
      screen.getByRole("button", { name: "local_models.server.test" }),
    );
    await waitFor(() =>
      expect(
        screen.getByText("local_models.server.probe_ok:0.32.15,12"),
      ).toBeDefined(),
    );
    const probe = calls.find((c) => c.url.endsWith("/server/test"));
    expect(probe?.body).toEqual({ base_url: "http://127.0.0.1:11434" });
  });

  it("stops only after a second click and keeps the honest refusal", async () => {
    installFetch(serverBody(), {
      stop: {
        ok: false,
        message:
          "This Ollama was not started by Jarvis, so Jarvis will not stop it.",
      },
    });
    renderPanel();
    await waitFor(() => expect(screen.getByText("qwen3.5:4b")).toBeDefined());

    fireEvent.click(
      screen.getByRole("button", { name: "local_models.server.stop" }),
    );
    expect(calls.some((c) => c.url.endsWith("/server/stop"))).toBe(false);
    expect(screen.getByText("local_models.server.stop_confirm")).toBeDefined();

    fireEvent.click(
      screen.getByRole("button", { name: "local_models.server.stop" }),
    );
    await waitFor(() =>
      expect(screen.getByTestId("stop-refusal")).toBeDefined(),
    );
    expect(screen.getByTestId("stop-refusal").textContent).toContain(
      "not started by Jarvis",
    );
    expect(
      (
        screen.getByRole("button", {
          name: "local_models.server.stop",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
  });

  it("unloads a running model through the dangerous route", async () => {
    installFetch(serverBody());
    renderPanel();
    await waitFor(() => expect(screen.getByText("qwen3.5:4b")).toBeDefined());

    fireEvent.click(
      screen.getByRole("button", {
        name: "local_models.server.unload qwen3.5:4b",
      }),
    );
    await waitFor(() =>
      expect(
        calls.some((c) => c.url.endsWith("/inventory/qwen3.5%3A4b/unload")),
      ).toBe(true),
    );
    await waitFor(() =>
      expect(mockStore.pushToast).toHaveBeenCalledWith(
        "success",
        "Unloaded qwen3.5:4b.",
      ),
    );
  });

  it("hides the local-only controls for a remote host", async () => {
    installFetch(
      serverBody({ host_kind: "remote", base_url: "http://nas.local:11434" }),
    );
    renderPanel();

    await waitFor(() =>
      expect(screen.getByTestId("remote-note")).toBeDefined(),
    );
    expect(screen.queryByTestId("runtime-panel")).toBeNull();
    expect(
      screen.queryByRole("button", { name: "local_models.server.stop" }),
    ).toBeNull();
    expect(screen.queryByText("local_models.server.log_title")).toBeNull();
    expect(screen.getByText("local_models.server.host_remote")).toBeDefined();
    // Nothing can be started on a remote host; the check still can run.
    expect(screen.queryByTestId("autostart")).toBeNull();
    expect(screen.getByTestId("verify")).toBeDefined();
  });

  it("flips 'start with Jarvis' and says what the next start would do", async () => {
    installFetch(serverBody());
    renderPanel();

    const box = (await screen.findByRole("checkbox", {
      name: "local_models.server.autostart_label",
    })) as HTMLInputElement;
    await waitFor(() => expect(box.checked).toBe(true));
    expect(screen.getByTestId("autostart-now").textContent).toContain(
      "local_models.server.autostart_now:local models serve the chat role",
    );

    fireEvent.click(box);
    await waitFor(() =>
      expect(
        calls.some(
          (c) =>
            c.url.endsWith("/runtime/autostart") &&
            c.method === "PUT" &&
            (c.body as { enabled?: boolean } | null)?.enabled === false,
        ),
      ).toBe(true),
    );
    await waitFor(() => expect(box.checked).toBe(false));
    expect(screen.getByTestId("autostart-now").textContent).toContain(
      "switched off",
    );
  });

  it("runs the check on demand and names the failing step", async () => {
    installFetch(serverBody());
    renderPanel();

    fireEvent.click(
      await screen.findByRole("button", { name: "local_models.verify.run" }),
    );
    const result = await screen.findByTestId("verify-result");
    expect(result.textContent).toContain(
      "local_models.verify.result_problem:embeddinggemma: /api/embed failed",
    );
    expect(result.textContent).toContain(
      "local_models.verify.chat — local_models.verify.ok · qwen3.5:4b · 2.4 s",
    );
    expect(result.textContent).toContain(
      "local_models.verify.embedding — local_models.verify.failed · embeddinggemma · /api/embed failed",
    );
  });

  it("switches the environment guide per OS and copies a command", async () => {
    installFetch(serverBody());
    renderPanel();

    await waitFor(() => expect(screen.getByTestId("env-guide")).toBeDefined());
    fireEvent.click(
      screen.getByRole("tab", { name: "local_models.server.os_macos" }),
    );
    await waitFor(() =>
      expect(
        screen.getByText("launchctl setenv OLLAMA_HOST 0.0.0.0"),
      ).toBeDefined(),
    );

    fireEvent.click(
      screen.getByRole("button", {
        name: "local_models.server.copy OLLAMA_HOST",
      }),
    );
    await waitFor(() =>
      expect(clipboard.robustCopy).toHaveBeenCalledWith(
        "launchctl setenv OLLAMA_HOST 0.0.0.0",
      ),
    );
    expect(mockStore.pushToast).toHaveBeenCalledWith(
      "success",
      "local_models.server.copied",
    );
  });

  it("loads the log only once it is opened", async () => {
    installFetch(serverBody());
    renderPanel();
    await waitFor(() => expect(screen.getByText("qwen3.5:4b")).toBeDefined());
    expect(calls.some((c) => c.url.includes("/server/log"))).toBe(false);

    fireEvent.click(
      screen.getByRole("button", { name: "local_models.server.log_title" }),
    );
    await waitFor(() => expect(screen.getByTestId("server-log")).toBeDefined());
    expect(screen.getByTestId("server-log").textContent).toBe(
      "line one\nline two",
    );
    expect(
      screen.getByRole("button", { name: "local_models.server.log_refresh" }),
    ).toBeDefined();
  });
});

describe("formatters", () => {
  const t = (key: string) => key;

  it("formats gigabytes and the empty case", () => {
    expect(formatGb(0)).toBe("—");
    expect(formatGb(null)).toBe("—");
    expect(formatGb(3 * 1024 ** 3)).toBe("3.0 GB");
  });

  it("reads expiry as minutes, now, or kept", () => {
    const now = Date.parse("2026-08-24T10:00:00Z");
    expect(formatExpiry("2026-08-24T10:05:00Z", t, now)).toBe(
      "local_models.server.expires_in_min:5",
    );
    expect(formatExpiry("2026-08-24T09:59:00Z", t, now)).toBe(
      "local_models.server.expires_now",
    );
    expect(formatExpiry("2030-01-01T00:00:00Z", t, now)).toBe(
      "local_models.server.expires_kept",
    );
    expect(formatExpiry("garbage", t, now)).toBe("—");
    expect(formatExpiry(null, t, now)).toBe("—");
  });
});
