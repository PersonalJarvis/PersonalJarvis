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

import {
  PROVIDER_STATE_CHIPS,
  ProviderCard,
  managedRuntimeBadge,
  type ProviderStateChip,
} from "@/components/providers/ProviderTierSection";
import type {
  ManagedServerRuntime,
  ProviderDescriptor,
  ProviderTestResult,
} from "@/hooks/useProviders";
import { useI18nStore, type UiLanguage } from "@/i18n";
import enLocale from "@/i18n/locales/en.json";

/** The rendered label of one state chip, resolved through its i18n key.
 *
 * These chips used to be bare English literals in the component, so a test
 * asserting the literal could not tell a translated chip from an untranslated
 * one. Going through the key keeps the assertion about the STATE, not about
 * one locale's wording. */
function chipLabel(chip: ProviderStateChip): string {
  const [namespace, key] = PROVIDER_STATE_CHIPS[chip].key.split(".");
  const bucket = (enLocale as unknown as Record<string, Record<string, string>>)[
    namespace
  ];
  return bucket[key];
}

const EXPERIMENTAL_CONSENT_KEY =
  "jarvis.experimentalConsent.codex-subscription-realtime";

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

function renderCard(
  descriptor: ProviderDescriptor,
  onChanged: () => void = () => {},
  onActivateOptimistic: (tier: string, id: string) => void = () => {},
) {
  return render(
    <ProviderCard
      descriptor={descriptor}
      onChanged={onChanged}
      onActivateOptimistic={onActivateOptimistic}
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
    window.localStorage.clear();
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
    window.localStorage.clear();
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
    window.localStorage.clear();
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
        "The protected Codex voice profile is no longer valid. Connect with ChatGPT rebuilds it fresh.",
      ),
    ).toBeNull();
    // The state chip next to the provider name must not shout "missing" (red)
    // about an install the probe has not judged yet.
    expect(screen.queryByText(chipLabel("missing"))).toBeNull();
    expect(screen.getByText(chipLabel("checking"))).toBeTruthy();
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
        "The protected Codex voice profile is no longer valid. Connect with ChatGPT rebuilds it fresh.",
      ),
    ).toBeTruthy();
    const detail = screen.getByTestId("codex-setup-detail").textContent ?? "";
    expect(detail).toContain("Details:");
    expect(detail).toContain(
      "Subscription voice requires Codex CLI codex-cli 0.146.0.",
    );
  });

  it("keeps model and voice pickers mounted through a busy window", async () => {
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

    // The realtime options control mounts (it loads its options async) even
    // while the card is only transiently unsure about the login — the card
    // must not visibly flicker its pickers away during "one moment".
    expect(await screen.findByLabelText(/model/i)).toBeTruthy();
  });

  it("tells the user to finish a running login instead of restarting it", () => {
    installFetchMock();
    renderCard(
      codexRealtimeCard({
        configured: false,
        codex_status: {
          installed: true,
          connected: false,
          mode: "not_connected",
          message: "Dedicated ChatGPT subscription login is in progress.",
          reason_code: "login_in_progress",
        },
      }),
    );

    expect(
      screen.getByText(
        "The ChatGPT login is running — finish it in the browser window.",
      ),
    ).toBeTruthy();
    const connect = screen.getByRole("button", {
      name: "Connect with ChatGPT",
    }) as HTMLButtonElement;
    expect(connect.disabled).toBe(true);
    expect(screen.queryByText("npm i -g @openai/codex")).toBeNull();
  });

  it("treats an unsupported OS as terminal, not as a missing install", () => {
    installFetchMock();
    renderCard(
      codexRealtimeCard({
        configured: false,
        codex_status: {
          installed: false,
          connected: false,
          mode: "not_connected",
          message: "This operating-system architecture is not approved.",
          reason_code: "lifecycle_unavailable",
        },
      }),
    );

    expect(
      screen.getByText(
        "Subscription voice is not yet available on this operating system. API Realtime and standard voice still work.",
      ),
    ).toBeTruthy();
    // No install invitation for a CLI that would not help, and no active
    // Connect button that can only end in an error toast.
    expect(screen.queryByText("npm i -g @openai/codex")).toBeNull();
    const connect = screen.getByRole("button", {
      name: "Connect with ChatGPT",
    }) as HTMLButtonElement;
    expect(connect.disabled).toBe(true);
    // The state chip stays neutral — nothing is "missing" on an OS the
    // feature does not support.
    expect(screen.queryByText(chipLabel("missing"))).toBeNull();
    expect(screen.getByText(chipLabel("unavailable"))).toBeTruthy();
  });

  it("shows the sticky plan diagnosis after a refused activation", () => {
    installFetchMock();
    renderCard(
      codexRealtimeCard({
        configured: false,
        codex_status: {
          installed: true,
          connected: false,
          mode: "not_connected",
          message:
            "Subscription voice permits only personal ChatGPT accounts; workspace, enterprise, education, and unknown plans are refused.",
          reason_code: "plan_unsupported",
        },
      }),
    );

    expect(
      screen.getByText("This ChatGPT plan does not support subscription voice."),
    ).toBeTruthy();
    expect(screen.getByTestId("codex-setup-detail").textContent).toContain(
      "personal ChatGPT accounts",
    );
  });

  it("lets a plan-blocked card retry activation through the backend", async () => {
    const calls = installFetchMock();
    // Not this test's subject: the route was acknowledged earlier, so the
    // dialog stays away (it asks once per provider, never on every switch).
    window.localStorage.setItem(EXPERIMENTAL_CONSENT_KEY, "1");
    renderCard(
      codexRealtimeCard({
        configured: false,
        codex_status: {
          installed: true,
          connected: false,
          mode: "not_connected",
          message:
            "Subscription voice permits only personal ChatGPT accounts; workspace, enterprise, education, and unknown plans are refused.",
          reason_code: "plan_unsupported",
        },
      }),
    );

    // The backend's live account gate is the ONLY judge that can clear the
    // sticky block — the click must reach it instead of dead-ending in a
    // local toast.
    fireEvent.click(screen.getByText("ChatGPT subscription (Codex)"));

    await waitFor(() =>
      expect(
        calls.some(
          (candidate) =>
            candidate.method === "POST" &&
            candidate.url.includes("/realtime/switch"),
        ),
      ).toBe(true),
    );
  });

  it("shows the pinned install path for a wrong codex release", () => {
    installFetchMock();
    renderCard(
      codexRealtimeCard({
        configured: false,
        install_hint: "npm i -g @openai/codex@0.146.0",
        codex_status: {
          installed: false,
          connected: false,
          mode: "not_connected",
          message: "Subscription voice requires Codex CLI 0.146.0.",
          reason_code: "not_installed",
        },
      }),
    );

    expect(
      screen.getByText(
        "Install the supported Codex version before connecting ChatGPT.",
      ),
    ).toBeTruthy();
    // The actionable fix — the pinned command — must render, and the precise
    // requirement appears as the diagnostic detail.
    expect(screen.getByText("npm i -g @openai/codex@0.146.0")).toBeTruthy();
    expect(screen.getByTestId("codex-setup-detail").textContent).toContain(
      "Subscription voice requires Codex CLI 0.146.0.",
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
    window.localStorage.setItem(EXPERIMENTAL_CONSENT_KEY, "1");
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
    const optimistic = vi.fn();
    renderCard(codexRealtimeCard(), () => {}, optimistic);

    fireEvent.click(screen.getByText("ChatGPT subscription (Codex)"));

    // An in-app dialog, not window.confirm: the desktop shell renders that as
    // a raw "127.0.0.1 says" box and it blocks the whole window.
    const cancel = await screen.findByText("Cancel");
    fireEvent.click(cancel);
    expect(
      calls.some(
        (candidate) =>
          candidate.method === "POST" && candidate.url === "/api/realtime/switch",
      ),
    ).toBe(false);
    // The consent dialog comes BEFORE the optimistic radio flip: a declined
    // dialog used to leave the highlight stuck on the new card with no
    // refetch to roll it back.
    expect(optimistic).not.toHaveBeenCalled();
  });

  it("flips the radio optimistically only after the consent is accepted", async () => {
    installFetchMock();
    const optimistic = vi.fn();
    renderCard(codexRealtimeCard(), () => {}, optimistic);

    fireEvent.click(screen.getByText("ChatGPT subscription (Codex)"));

    const accept = await screen.findByText("Yes");
    expect(optimistic).not.toHaveBeenCalled();
    fireEvent.click(accept);

    await waitFor(() =>
      expect(optimistic).toHaveBeenCalledWith(
        "realtime",
        "codex-subscription-realtime",
      ),
    );
  });

  it("asks for the experimental acknowledgement once, not on every switch", async () => {
    installFetchMock();
    window.localStorage.setItem(EXPERIMENTAL_CONSENT_KEY, "1");
    const optimistic = vi.fn();
    renderCard(codexRealtimeCard(), () => {}, optimistic);

    fireEvent.click(screen.getByText("ChatGPT subscription (Codex)"));

    // Straight through: re-asking every time taught the user to click the
    // notice away unread, which defeats the point of showing it at all.
    await waitFor(() =>
      expect(optimistic).toHaveBeenCalledWith(
        "realtime",
        "codex-subscription-realtime",
      ),
    );
    expect(screen.queryByText("Yes")).toBeNull();
  });
});

/**
 * Regression guard for the stacked-notification wall (2026-08-04).
 *
 * A card the user cannot activate yet answers each click with one fixed
 * sentence. The card used to bind BOTH onClick and onDoubleClick to the same
 * handler, so a double click ran it three times (click, click, dblclick) and
 * two attempts left six identical toasts stacked over the connect button the
 * message points at. One gesture must produce at most one visible notice.
 */
describe("ProviderCard — an unavailable card does not build a warning wall", () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.restoreAllMocks();
    useI18nStore.getState().setUi("en", { push: false });
    useEventStore.setState({ toasts: [] });
  });

  afterEach(() => {
    cleanup();
  });

  function unconnectedSubscriptionCard(): ProviderDescriptor {
    return {
      ...dictationCard(),
      id: "codex-subscription-realtime",
      label: "ChatGPT subscription (Codex)",
      tier: "realtime",
      auth_mode: "codex",
      secret_keys: [],
      secrets_set: {},
      billing: "subscription",
      configured: false,
      optional: false,
      polish_family: null,
      codex_status: {
        installed: true,
        connected: false,
        mode: "not_connected",
        message: "The dedicated Codex voice profile has not been created yet.",
        reason_code: "login_required",
      },
    } as ProviderDescriptor;
  }

  it("raises ONE notice for a double click, not three", async () => {
    installFetchMock();
    renderCard(unconnectedSubscriptionCard());

    const title = screen.getByText("ChatGPT subscription (Codex)");
    // Exactly what a user does: a real double click delivers two click events
    // plus one dblclick. Only the clicks may reach the handler now.
    fireEvent.click(title);
    fireEvent.click(title);
    fireEvent.doubleClick(title);

    await waitFor(() =>
      expect(useEventStore.getState().toasts.length).toBeGreaterThan(0),
    );
    const { toasts } = useEventStore.getState();
    expect(toasts).toHaveLength(1);
    // Collapsed, and honest about how often it was raised.
    expect(toasts[0].count).toBe(2);
  });

  it("never activates a card whose login is still missing", async () => {
    const calls = installFetchMock();
    renderCard(unconnectedSubscriptionCard());

    fireEvent.click(screen.getByText("ChatGPT subscription (Codex)"));

    await waitFor(() =>
      expect(useEventStore.getState().toasts.length).toBe(1),
    );
    expect(calls.some((c) => c.url.includes("/api/realtime/switch"))).toBe(false);
  });
});

describe("managed local realtime model setup", () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.restoreAllMocks();
    useI18nStore.getState().setUi("en", { push: false });
  });

  afterEach(() => cleanup());

  function localRealtimeCard(): ProviderDescriptor {
    return {
      ...dictationCard(),
      id: "local-realtime",
      label: "Self-hosted realtime",
      tier: "realtime",
      auth_mode: "none",
      secret_keys: [],
      secrets_set: {},
      billing: "local",
      configured: true,
      active: true,
      optional: false,
      polish_family: null,
      supports_base_url: true,
      base_url: "http://127.0.0.1:8765",
      managed_server: {
        ready: false,
        components: {},
        sentence: "Managed server not installed.",
      },
    };
  }

  it("shows hear, think, and speak choices before installation", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({
        url,
        method: (init?.method ?? "GET").toUpperCase(),
        body: (init?.body as string | undefined) ?? null,
      });
      const body = url.endsWith("/model-catalog")
        ? {
            brain: {
              reachable: true,
              usable_gb: 16,
              current: "qwen3.5:4b",
              models: [
                {
                  id: "qwen3.5:4b",
                  label: "qwen3.5:4b",
                  size_gb: 3.4,
                  installed: true,
                  fits: true,
                  note: "",
                  recommended: true,
                  current: true,
                },
              ],
            },
            current: "qwen3-tts-1.7b",
            models: [
              {
                id: "qwen3-tts-1.7b",
                label: "Qwen3-TTS 1.7B",
                backend: "qwen3",
                model: "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
                languages: ["de", "en"],
                selectable: true,
                recommended: true,
                note: "tested",
                speaker: "Aiden",
                current: true,
                platform: "all",
                runtime_ready: true,
                frontier: true,
                streaming: true,
                source_url: "https://github.com/QwenLM/Qwen3-TTS",
                release_date: "2026-01",
                license: "Apache-2.0",
                size_gb: 3.4,
              },
              {
                id: "pocket-tts-de-24l",
                label: "Pocket TTS 2.1 · German HQ",
                backend: "pocket",
                model: "kyutai/pocket-tts:german_24l",
                languages: ["de"],
                selectable: true,
                recommended: false,
                note: "current CPU-first streaming model",
                speaker: "juergen",
                current: false,
                platform: "all",
                runtime_ready: false,
                frontier: true,
                streaming: true,
                source_url: "https://github.com/kyutai-labs/pocket-tts",
                release_date: "2026-05",
                license: "MIT",
                size_gb: 1,
              },
              {
                id: "chattts",
                label: "ChatTTS",
                backend: "chatTTS",
                model: "2Noise/ChatTTS",
                languages: ["en", "zh"],
                selectable: false,
                recommended: false,
                note: "not tested",
                speaker: "",
                current: false,
                platform: "all",
              },
              {
                id: "moss-tts-nano-100m",
                label: "MOSS-TTS-Nano 100M",
                backend: "moss",
                model: "OpenMOSS-Team/MOSS-TTS-Nano-100M",
                languages: ["de", "en", "es"],
                selectable: false,
                recommended: false,
                note: "managed realtime adapter is not available yet",
                speaker: "",
                current: false,
                platform: "all",
                runtime_ready: false,
                frontier: true,
                streaming: true,
                source_url: "https://huggingface.co/OpenMOSS-Team/MOSS-TTS-Nano-100M",
                release_date: "2026-04",
                license: "Apache-2.0",
                size_gb: 0.6,
              },
            ],
            hearing: {
              id: "parakeet-tdt",
              label: "Parakeet TDT",
              note: "wake-word is separate",
            },
          }
        : {};
      return {
        ok: true,
        status: 200,
        json: async () => body,
        text: async () => JSON.stringify(body),
      } as Response;
    }));

    renderCard(localRealtimeCard());

    await waitFor(() =>
      expect(screen.getByTestId("managed-model-picker")).toBeTruthy(),
    );
    expect(screen.getByText("Hear")).toBeTruthy();
    expect(screen.getByText("Think")).toBeTruthy();
    expect(screen.getByText("Speak")).toBeTruthy();
    expect(screen.getByText("Parakeet TDT")).toBeTruthy();
    expect(screen.getByText("Browse the full Ollama catalog")).toBeTruthy();
    expect(screen.getByText("Browse open-source speech models")).toBeTruthy();
    expect(screen.getByText("Advanced connection settings")).toBeTruthy();
    fireEvent.click(screen.getByRole("combobox", { name: "Speak" }));
    const unavailable = await screen.findByRole("option", {
      name: /ChatTTS.*not voice-tested yet/,
    });
    expect(unavailable.getAttribute("aria-disabled")).toBe("true");
    expect(calls.some((call) => call.url.endsWith("/model-catalog"))).toBe(true);

    fireEvent.click(screen.getByText("Browse open-source speech models"));
    const search = screen.getByRole("searchbox", {
      name: "Search by model, backend, language, or license…",
    });
    fireEvent.change(search, { target: { value: "german" } });
    expect(screen.getByText("Pocket TTS 2.1 · German HQ")).toBeTruthy();
    expect(screen.queryByText("MOSS-TTS-Nano 100M")).toBeNull();

    fireEvent.change(search, { target: { value: "moss" } });
    expect(screen.getByText("MOSS-TTS-Nano 100M")).toBeTruthy();
    expect(screen.getByText("Integration pending")).toBeTruthy();
  });
});

describe("managed server badge", () => {
  /** A pool snapshot with the fields the rule actually reads. */
  function pool(inUse: number, available: number) {
    return {
      size: inUse + available,
      in_use: inUse,
      available,
      active: inUse,
      draining: 0,
      stuck: 0,
    };
  }

  function runtime(overrides: Partial<ManagedServerRuntime>): ManagedServerRuntime {
    return {
      reachable: true,
      ready: true,
      available: true,
      pool: pool(0, 1),
      port: 8765,
      pid: 4242,
      owned: true,
      stale: false,
      ...overrides,
    };
  }

  it("calls a server with a free pipeline running", () => {
    expect(managedRuntimeBadge(runtime({}), true)?.key).toBe(
      "apikeys_view.managed_server_running",
    );
  });

  it("calls a busy-but-healthy server running, not broken", () => {
    const busy = runtime({ available: false, pool: pool(1, 0) });
    expect(managedRuntimeBadge(busy, true)?.key).toBe(
      "apikeys_view.managed_server_running",
    );
  });

  it("stops claiming 'running' while the model pool is still cold", () => {
    // The live defect: the port answered, so the card stayed green through a
    // ~60 s model load while every call failed on the same server.
    const loading = runtime({ ready: false, available: false, pool: null });
    const badge = managedRuntimeBadge(loading, true);
    expect(badge?.key).toBe("apikeys_view.managed_server_starting");
    expect(badge?.tone).toBe("warn");
  });

  it("flags a wedged pool that serves nobody", () => {
    const wedged = runtime({ available: false, pool: pool(0, 0) });
    expect(managedRuntimeBadge(wedged, true)?.key).toBe(
      "apikeys_view.managed_server_no_capacity",
    );
  });

  it("keeps a foreign listener a port conflict, never ours", () => {
    const foreign = runtime({ owned: false });
    expect(managedRuntimeBadge(foreign, false)?.key).toBe(
      "apikeys_view.managed_server_port_conflict",
    );
  });

  it("trusts an older backend that reports no readiness fields", () => {
    const legacy: ManagedServerRuntime = {
      reachable: true,
      port: 8765,
      pid: 1,
      owned: true,
      stale: false,
    };
    expect(managedRuntimeBadge(legacy, true)?.key).toBe(
      "apikeys_view.managed_server_running",
    );
  });

  it("reads an unreachable owned process as starting, and none as stopped", () => {
    expect(
      managedRuntimeBadge(runtime({ reachable: false }), true)?.key,
    ).toBe("apikeys_view.managed_server_starting");
    expect(
      managedRuntimeBadge(runtime({ reachable: false, owned: false }), false)?.key,
    ).toBe("apikeys_view.managed_server_stopped");
  });

  it("names a crash loop instead of hiding it behind 'stopped'", () => {
    // Live 2026-08-09: four consecutive readiness timeouts read as a quiet
    // muted badge; two burned five-minute boot budgets deserve a warning.
    const looping = runtime({
      reachable: false,
      owned: false,
      boot: { failed_streak: 2, starting: false },
    });
    const badge = managedRuntimeBadge(looping, true);
    expect(badge?.key).toBe("apikeys_view.managed_server_crash_loop");
    expect(badge?.tone).toBe("warn");
    // One failure is still normal churn, not a loop.
    const single = runtime({
      reachable: false,
      owned: false,
      boot: { failed_streak: 1, starting: false },
    });
    expect(managedRuntimeBadge(single, true)?.key).toBe(
      "apikeys_view.managed_server_stopped",
    );
  });
});
