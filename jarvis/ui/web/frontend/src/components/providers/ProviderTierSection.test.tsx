/**
 * Component tests for the provider CARD — specifically what it sends when the
 * user activates one.
 *
 * Every tier here has its own switch route and its own vocabulary, and the
 * dictation-polish tier is the one where the card id and the stored value
 * genuinely differ: the card is "openai-polish" (a bare "openai" would collide
 * with the brain card) while `[dictation].polish_provider` stores "openai".
 * The backend has always shipped the translation as `polish_family` on the card
 * payload, but nothing pinned that a client actually READS it — and a client
 * that sent `id` got HTTP 200, a success toast, and no change at all, because
 * an unrecognised family resolves exactly like `auto`. That is the failure this
 * file exists to keep out.
 *
 * No jest-dom in this repo — assertions use toBeTruthy()/toBeNull().
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { ProviderCard } from "@/components/providers/ProviderTierSection";
import type { ProviderDescriptor, ProviderTestResult } from "@/hooks/useProviders";
import { useI18nStore, type UiLanguage } from "@/i18n";
import { useEventStore } from "@/store/events";

interface Call {
  url: string;
  method: string;
  body: string | null;
}

/** The verdict the verification probe returns; `ok` unless a test overrides it. */
const OK_TEST: ProviderTestResult = {
  provider: "openai-polish",
  status: "ok",
  detail: "Answered in 300 ms.",
  latency_ms: 300,
  integration_ok: true,
};

function installFetchMock(testResult: ProviderTestResult = OK_TEST, status = 200): Call[] {
  const calls: Call[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    calls.push({
      url,
      method: (init?.method ?? "GET").toUpperCase(),
      body: (init?.body as string | undefined) ?? null,
    });
    const body = url.includes("/test") ? testResult : {};
    return {
      ok: status >= 200 && status < 300,
      status,
      statusText: status >= 200 && status < 300 ? "OK" : "ERR",
      json: async () => body,
      text: async () => JSON.stringify(body),
    } as Response;
  });
  (globalThis as unknown as { fetch: typeof fetch }).fetch =
    fetchMock as unknown as typeof fetch;
  return calls;
}

/** Toast messages of one kind currently in the store. */
function toastsOf(kind: string): string[] {
  return useEventStore
    .getState()
    .toasts.filter((toast) => toast.kind === kind)
    .map((toast) => toast.message);
}

function dictationCard(over: Partial<ProviderDescriptor> = {}): ProviderDescriptor {
  return {
    id: "openai-polish",
    label: "OpenAI: dictation polish",
    tier: "dictation",
    auth_mode: "api_key",
    secret_keys: ["openai_api_key"],
    secrets_set: { openai_api_key: true },
    dashboard_url: null,
    login_cli: null,
    install_hint: null,
    credential_path_hint: null,
    configured: true,
    active: false,
    cli_installed: null,
    credential_help: null,
    signup_url: null,
    billing: "api",
    optional: true,
    polish_family: "openai",
    alt_credential: null,
    ...over,
  };
}

function codexRealtimeCard(
  over: Partial<ProviderDescriptor> = {},
): ProviderDescriptor {
  return {
    id: "codex-subscription-realtime",
    label: "ChatGPT subscription (Codex)",
    tier: "realtime",
    auth_mode: "codex",
    secret_keys: [],
    secrets_set: {},
    dashboard_url: null,
    login_cli: ["codex", "login"],
    install_hint: "npm i -g @openai/codex",
    credential_path_hint: null,
    configured: true,
    active: false,
    cli_installed: true,
    credential_help: "Uses the ChatGPT plan signed in through Codex.",
    signup_url: "https://chatgpt.com",
    billing: "subscription",
    experimental: true,
    alt_credential: null,
    codex_status: {
      installed: true,
      connected: true,
      mode: "chatgpt",
      message: "Connected with ChatGPT.",
    },
    ...over,
  };
}

function renderCard(descriptor: ProviderDescriptor, onChanged: () => void = () => {}) {
  return render(
    <ProviderCard
      descriptor={descriptor}
      onChanged={onChanged}
      onActivateOptimistic={() => {}}
      autoActivateOnSave={false}
    />,
  );
}

/** The PUT the card fired at the dictation settings route, parsed. */
function polishPin(calls: Call[]): Record<string, unknown> {
  const put = calls.find(
    (c) => c.method === "PUT" && c.url.startsWith("/api/dictation/settings"),
  );
  expect(put).toBeTruthy();
  return JSON.parse(put!.body ?? "{}") as Record<string, unknown>;
}

describe("ProviderCard — dictation polish activation", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  it("pins the polish FAMILY, not the card id", async () => {
    const calls = installFetchMock();

    renderCard(dictationCard());
    fireEvent.click(screen.getByText("OpenAI: dictation polish"));

    await waitFor(() => {
      // "openai", never "openai-polish": the chain ignores a family id it does
      // not know and quietly keeps the previously active provider, so sending
      // the card id here is a switch that reports success and does nothing.
      expect(polishPin(calls).polish_provider).toBe("openai");
    });
  });

  it("persists the pin, so it survives a restart", async () => {
    const calls = installFetchMock();

    renderCard(dictationCard());
    fireEvent.click(screen.getByText("OpenAI: dictation polish"));

    await waitFor(() => {
      expect(polishPin(calls).persist).toBe(true);
    });
  });

  it("falls back to the card id when an older payload carries no family", async () => {
    const calls = installFetchMock();

    renderCard(dictationCard({ polish_family: null }));
    fireEvent.click(screen.getByText("OpenAI: dictation polish"));

    await waitFor(() => {
      // Not the ideal value, but a live one: the route translates the card
      // vocabulary, so an old frontend against a current backend still works.
      expect(polishPin(calls).polish_provider).toBe("openai-polish");
    });
  });

  it("never reconfigures the voice engine from a dictation card", async () => {
    const calls = installFetchMock();

    renderCard(dictationCard());
    fireEvent.click(screen.getByText("OpenAI: dictation polish"));

    await waitFor(() => expect(calls.length).toBeGreaterThan(0));
    // The realtime/STT/TTS switch routes rebuild the live voice pipeline. A
    // wording preference must never reach them.
    expect(calls.some((c) => c.url.includes("/switch"))).toBe(false);
  });
});

describe("ProviderCard — a switched-to polish provider proves it works", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useI18nStore.getState().setUi("en", { push: false });
    useEventStore.setState({ toasts: [] });
  });

  afterEach(() => {
    cleanup();
  });

  it("verifies the provider after switching to it", async () => {
    const calls = installFetchMock();

    renderCard(dictationCard());
    fireEvent.click(screen.getByText("OpenAI: dictation polish"));

    // A stored key is not a working provider, and this pass is INVISIBLE when
    // it fails — it just delivers the raw transcript. Without this probe an
    // out-of-credits account is indistinguishable from a healthy one.
    await waitFor(() => {
      expect(
        calls.some(
          (c) => c.method === "POST" && c.url.includes("/api/providers/openai-polish/test"),
        ),
      ).toBe(true);
    });
  });

  it("says what went wrong when the provider cannot answer", async () => {
    installFetchMock({
      provider: "openai-polish",
      status: "no_credits",
      detail: "OpenAI polish request returned HTTP 429: you exceeded your quota",
      latency_ms: 235,
      integration_ok: true,
    });

    renderCard(dictationCard());
    fireEvent.click(screen.getByText("OpenAI: dictation polish"));

    await waitFor(() => {
      const warnings = toastsOf("warning");
      expect(warnings.length).toBe(1);
      // The backend sentence is passed through verbatim — it already names the
      // cause and the fix, which a generic "does not work" would throw away.
      expect(warnings[0]).toContain("exceeded your quota");
    });
  });

  it("stays quiet when the provider answers", async () => {
    installFetchMock();

    renderCard(dictationCard());
    fireEvent.click(screen.getByText("OpenAI: dictation polish"));

    await waitFor(() => expect(toastsOf("success").length).toBe(1));
    expect(toastsOf("warning")).toEqual([]);
  });

  it("does not let a broken verification undo the switch", async () => {
    // The probe is a check, never a gate: the user is entitled to pin a
    // provider even when the verification itself cannot run. A probe that
    // fails for its OWN reasons must stay quieter than the switch it checks.
    const calls: Call[] = [];
    const failingProbe = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({
        url,
        method: (init?.method ?? "GET").toUpperCase(),
        body: (init?.body as string | undefined) ?? null,
      });
      if (url.includes("/test")) throw new Error("network down");
      return {
        ok: true,
        status: 200,
        statusText: "OK",
        json: async () => ({}),
        text: async () => "{}",
      } as Response;
    });
    (globalThis as unknown as { fetch: typeof fetch }).fetch =
      failingProbe as unknown as typeof fetch;

    renderCard(dictationCard());
    fireEvent.click(screen.getByText("OpenAI: dictation polish"));

    await waitFor(() => {
      expect(
        calls.some(
          (c) => c.method === "PUT" && c.url.startsWith("/api/dictation/settings"),
        ),
      ).toBe(true);
    });
    // The switch stands and reports success; the dead probe raises no alarm.
    expect(toastsOf("success").length).toBe(1);
    expect(toastsOf("warning")).toEqual([]);
  });
});

describe("ProviderCard: ChatGPT subscription Realtime", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useEventStore.setState({ toasts: [] });
    useI18nStore.getState().setUi("en", { push: false });
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("shows login setup without rendering an API key field", () => {
    installFetchMock();
    renderCard(
      codexRealtimeCard({
        configured: false,
        codex_status: {
          installed: true,
          connected: false,
          mode: "not_connected",
          message: "Sign in required.",
        },
      }),
    );

    expect(screen.getByRole("button", { name: "Connect with ChatGPT" })).toBeTruthy();
    expect(document.querySelector('input[type="password"]')).toBeNull();
    expect(
      screen.getByTestId("provider-experimental-codex-subscription-realtime"),
    ).toBeTruthy();
    expect(
      screen.getByTestId("provider-experimental-note-codex-subscription-realtime"),
    ).toBeTruthy();
  });

  it("uses the isolated subscription login instead of the normal Codex profile", async () => {
    const calls = installFetchMock();
    renderCard(
      codexRealtimeCard({
        configured: false,
        codex_status: {
          installed: true,
          connected: false,
          mode: "not_connected",
          message: "Sign in required.",
        },
      }),
    );

    fireEvent.click(screen.getByRole("button", { name: "Connect with ChatGPT" }));

    await waitFor(() =>
      expect(
        calls.some(
          (candidate) =>
            candidate.method === "POST" &&
            candidate.url === "/api/codex/subscription-voice/login",
        ),
      ).toBe(true),
    );
  });

  it("keeps refreshing until a slow browser login becomes visible", async () => {
    vi.useFakeTimers();
    const onChanged = vi.fn();
    installFetchMock();
    renderCard(
      codexRealtimeCard({
        configured: false,
        codex_status: {
          installed: true,
          connected: false,
          mode: "not_connected",
          message: "Sign in required.",
        },
      }),
      onChanged,
    );

    fireEvent.click(screen.getByRole("button", { name: "Connect with ChatGPT" }));
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(onChanged).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(11_000);
    });
    expect(onChanged.mock.calls.length).toBeGreaterThanOrEqual(3);
    vi.useRealTimers();
  });

  it("disconnects only the isolated subscription voice login", async () => {
    const calls = installFetchMock();
    renderCard(codexRealtimeCard());

    fireEvent.click(screen.getByRole("button", { name: "Disconnect" }));

    await waitFor(() =>
      expect(
        calls.some(
          (candidate) =>
            candidate.method === "POST" &&
            candidate.url === "/api/codex/subscription-voice/logout",
        ),
      ).toBe(true),
    );
  });

  it.each([
    ["de", "Über dein ChatGPT-Abo verbunden.", "Trennen"], // i18n-allow: German UI fixture.
    ["es", "Conectado mediante tu suscripción de ChatGPT.", "Desconectar"],
  ] as const)(
    "localizes the connected subscription controls in %s",
    async (language, connectedLabel, disconnectLabel) => {
      installFetchMock();
      useI18nStore.getState().setUi(language as UiLanguage, { push: false });
      renderCard(
        codexRealtimeCard({
          codex_status: {
            installed: true,
            connected: true,
            mode: "chatgpt",
            message: "Backend English must stay hidden.",
            reason_code: "ready",
          },
        }),
      );

      await waitFor(() => expect(screen.getByText(connectedLabel)).toBeTruthy());
      expect(screen.getByRole("button", { name: disconnectLabel })).toBeTruthy();
      expect(screen.queryByText("Backend English must stay hidden.")).toBeNull();
    },
  );

  it("renders a transient busy status as a neutral check, never as a defect", () => {
    installFetchMock();
    renderCard(
      codexRealtimeCard({
        configured: false,
        codex_status: {
          installed: false,
          connected: false,
          mode: "not_connected",
          message: "Dedicated subscription voice status is being checked or changed.",
          reason_code: "busy",
        },
      }),
    );

    expect(
      screen.getByText("Checking the ChatGPT voice status — one moment."),
    ).toBeTruthy();
    // Busy means "state unknown for a moment" — the card must not fall back
    // to the install invitation or the reconnect warning.
    expect(screen.queryByText("npm i -g @openai/codex")).toBeNull();
    expect(
      screen.queryByText(
        "The protected Codex voice profile needs attention. Review the setup guide, then reconnect.",
      ),
    ).toBeNull();
    // The state chip next to the provider name must not shout "missing" (red)
    // about an install the probe has not judged yet.
    expect(screen.queryByText("missing")).toBeNull();
    expect(screen.getByText("checking")).toBeTruthy();
    // The connect action stays available: login validates itself and a busy
    // flicker must not lock the user out of it.
    const connect = screen.getByRole("button", {
      name: "Connect with ChatGPT",
    }) as HTMLButtonElement;
    expect(connect.disabled).toBe(false);

    // Clicking the card during busy must not demand a redo of a working
    // login — it answers with the same neutral "checking" note.
    fireEvent.click(screen.getByText("ChatGPT subscription (Codex)"));
    expect(toastsOf("warning")).toEqual([]);
    expect(toastsOf("info")).toEqual([
      "Checking the ChatGPT voice status — one moment.",
    ]);
  });

  it("surfaces the precise backend diagnosis on a real setup defect", () => {
    installFetchMock();
    renderCard(
      codexRealtimeCard({
        configured: false,
        codex_status: {
          installed: true,
          connected: false,
          mode: "not_connected",
          message: "Subscription voice requires Codex CLI codex-cli 0.146.0.",
          reason_code: "setup_invalid",
        },
      }),
    );

    expect(
      screen.getByText(
        "The protected Codex voice profile needs attention. Review the setup guide, then reconnect.",
      ),
    ).toBeTruthy();
    const detail = screen.getByTestId("codex-setup-detail").textContent ?? "";
    expect(detail).toContain("Details:");
    expect(detail).toContain(
      "Subscription voice requires Codex CLI codex-cli 0.146.0.",
    );
  });

  it("asks for a ChatGPT subscription login without suggesting an API key", () => {
    installFetchMock();
    renderCard(
      codexRealtimeCard({
        configured: false,
        codex_status: {
          installed: true,
          connected: false,
          mode: "not_connected",
          message: "Sign in required.",
        },
      }),
    );

    fireEvent.click(screen.getByText("ChatGPT subscription (Codex)"));

    expect(toastsOf("warning")).toEqual([
      "This voice provider needs a ChatGPT subscription login. Connect with ChatGPT below.",
    ]);
    expect(toastsOf("warning")[0]).not.toContain("API key");
  });

  it.each([
    [
      "de",
      "Nutzt den über Codex verbundenen ChatGPT-Tarif. Ein API-Key ist nicht nötig.", // i18n-allow: German UI fixture.
    ],
    [
      "es",
      "Usa el plan de ChatGPT conectado mediante Codex. No necesita una clave API.",
    ],
  ] as const)(
    "uses localized setup guidance in %s instead of backend English",
    async (language, expected) => {
      installFetchMock();
      useI18nStore.getState().setUi(language as UiLanguage, { push: false });
      renderCard(
        codexRealtimeCard({
          credential_help: "Backend English credential help must stay hidden.",
        }),
      );

      await waitFor(() => expect(screen.getByText(expected)).toBeTruthy());
      expect(
        screen.queryByText("Backend English credential help must stay hidden."),
      ).toBeNull();
    },
  );

  it("activates through the Realtime switch without the Brain API-key guard", async () => {
    const calls = installFetchMock();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderCard(codexRealtimeCard());

    fireEvent.click(screen.getByText("ChatGPT subscription (Codex)"));

    await waitFor(() => {
      const call = calls.find(
        (candidate) =>
          candidate.method === "POST" && candidate.url === "/api/realtime/switch",
      );
      expect(call).toBeTruthy();
      expect(JSON.parse(call?.body ?? "{}")).toMatchObject({
        provider: "codex-subscription-realtime",
        persist: true,
        accept_experimental: true,
      });
    });
  });

  it("does not activate until the user accepts the experimental boundary", async () => {
    const calls = installFetchMock();
    vi.spyOn(window, "confirm").mockReturnValue(false);
    renderCard(codexRealtimeCard());

    fireEvent.click(screen.getByText("ChatGPT subscription (Codex)"));

    await waitFor(() => expect(window.confirm).toHaveBeenCalledOnce());
    expect(
      calls.some(
        (candidate) =>
          candidate.method === "POST" && candidate.url === "/api/realtime/switch",
      ),
    ).toBe(false);
  });
});
