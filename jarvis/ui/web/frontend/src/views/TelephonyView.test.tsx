/**
 * Component tests for TelephonyView.
 *
 * The view consumes the shared REST contract from
 * docs/superpowers/specs/2026-05-24-twilio-telephony-design.md §4. These tests
 * mock `fetch` per-route (mirroring ProfileView.test.tsx) and pin:
 *   - status renders from a mocked GET /api/telephony/status,
 *   - the "Save" handler POSTs to /config (+ /credentials when a token is typed),
 *   - "Self-test voice" renders the returned transcript + response_text,
 *   - graceful degradation when twilio is unavailable / unconfigured,
 *   - NO TEXT TRUNCATION: a 40+ char public URL and a full Account SID are
 *     rendered verbatim with wrapping CSS, never clipped (explicit user
 *     requirement; cf. commit 44c955329).
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";

import { TelephonyView } from "@/views/TelephonyView";

interface RouteResult {
  status?: number;
  body: unknown;
}

/**
 * Mock `fetch` with per-route status control. Unknown URLs throw so accidental
 * network calls surface as test failures. POST bodies are captured so the save
 * handler can be asserted.
 */
function installFetchMock(routes: Record<string, () => RouteResult>) {
  const calls: { url: string; method: string; body: unknown }[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    let body: unknown = null;
    if (typeof init?.body === "string") {
      try {
        body = JSON.parse(init.body);
      } catch {
        body = init.body;
      }
    }
    calls.push({ url, method, body });
    // Longest-prefix match so "/calls?limit=20" resolves before "/calls".
    const prefixes = Object.keys(routes).sort((a, b) => b.length - a.length);
    for (const prefix of prefixes) {
      if (url.startsWith(prefix)) {
        const { status = 200, body: resBody } = routes[prefix]();
        return {
          ok: status >= 200 && status < 300,
          status,
          statusText: status >= 200 && status < 300 ? "OK" : "ERR",
          json: async () => resBody,
          text: async () => JSON.stringify(resBody),
        } as Response;
      }
    }
    throw new Error(`unexpected fetch ${url}`);
  });
  (globalThis as unknown as { fetch: typeof fetch }).fetch =
    fetchMock as unknown as typeof fetch;
  // jsdom has no clipboard by default; stub so copy buttons never throw.
  Object.assign(navigator, {
    clipboard: { writeText: vi.fn(async () => undefined) },
  });
  return { fetchMock, calls };
}

// Deliberately long values to prove no truncation.
const LONG_PUBLIC_URL =
  "https://jarvis-telephony-gateway.example-company-domain.com/twilio/voice";
const FULL_ACCOUNT_SID = "AC0123456789abcdef0123456789abcdef";

const STATUS_OK = {
  available: true,
  configured: true,
  enabled: true,
  account_sid_masked: "AC••••••cdef",
  phone_number: "+4930123456789",
  public_base_url: LONG_PUBLIC_URL,
  webhook_url: `${LONG_PUBLIC_URL}/api/telephony/voice`,
  auth_token_set: true,
  twilio_reachable: true,
  twilio_error: null,
  tts_provider: "gemini-flash-tts",
  tts_voice: "Charon",
  active_calls: 0,
  max_call_seconds: 600,
};

const CONFIG_OK = {
  enabled: true,
  account_sid: FULL_ACCOUNT_SID,
  phone_number: "+4930123456789",
  public_base_url: LONG_PUBLIC_URL,
  greeting: "",
  language_code: "de-DE",
  fallback_mode: "media",
  max_call_seconds: 600,
  auth_token_set: true,
};

const SCRIPTS_OK = {
  scripts: [
    {
      name: "Cloudflared tunnel",
      path: "scripts/telephony-tunnel.ps1",
      description: "Expose the local FastAPI port over a public HTTPS tunnel.",
      command: "pwsh scripts/telephony-tunnel.ps1 -Port 8765",
    },
  ],
};

const CALLS_OK = {
  calls: [
    {
      call_sid: "CA1111111111111111111111111111aaaa",
      from: "+4915112345678",
      to: "+4930123456789",
      started_at: "2026-05-24T10:00:00Z",
      ended_at: "2026-05-24T10:01:30Z",
      duration_s: 90,
      status: "completed",
      turns: 4,
    },
  ],
};

function okRoutes(): Record<string, () => RouteResult> {
  return {
    "/api/telephony/status": () => ({ body: STATUS_OK }),
    "/api/telephony/config": () => ({ body: CONFIG_OK }),
    "/api/telephony/scripts": () => ({ body: SCRIPTS_OK }),
    "/api/telephony/calls": () => ({ body: CALLS_OK }),
  };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("TelephonyView — status rendering", () => {
  it("renders status from the mocked fetch and shows the Charon voice", async () => {
    installFetchMock(okRoutes());

    render(<TelephonyView />);

    // TTS voice (Charon) surfaced in the status card.
    await waitFor(() => {
      expect(screen.getByTestId("status-tts-voice").textContent).toBe("Charon");
    });

    // Phone number and webhook surface verbatim.
    expect(screen.getByTestId("status-phone-number").textContent).toBe(
      "+4930123456789",
    );
  });

  it("does NOT truncate a long public URL or a full Account SID", async () => {
    installFetchMock(okRoutes());

    render(<TelephonyView />);

    // The full long URL appears verbatim in the status card (no ellipsis/clip).
    await waitFor(() => {
      expect(screen.getByTestId("status-public-url").textContent).toBe(
        LONG_PUBLIC_URL,
      );
    });
    const urlEl = screen.getByTestId("status-public-url");
    expect(urlEl.className).toContain("break-all");
    // The title attribute carries the full value for ellipsis-with-title cases.
    expect(urlEl.getAttribute("title")).toContain(LONG_PUBLIC_URL);

    // The credentials form holds the FULL (unmasked) Account SID verbatim.
    const sidInput = screen.getByDisplayValue(FULL_ACCOUNT_SID);
    expect(sidInput).toBeDefined();
  });
});

describe("TelephonyView — save handler", () => {
  it("POSTs config (and credentials when a token is typed) on Save", async () => {
    const { calls } = installFetchMock(okRoutes());

    render(<TelephonyView />);

    await waitFor(() => {
      expect(screen.getByTestId("status-tts-voice").textContent).toBe("Charon");
    });

    // Type a new auth token so the credentials POST fires too.
    const tokenInput = screen.getByPlaceholderText("Stored — leave blank to keep");
    fireEvent.change(tokenInput, { target: { value: "new-secret-token" } });

    fireEvent.click(screen.getByRole("button", { name: /^Save$/ }));

    await waitFor(() => {
      const configPost = calls.find(
        (c) => c.url.startsWith("/api/telephony/config") && c.method === "POST",
      );
      expect(configPost).toBeDefined();
    });

    const credsPost = calls.find(
      (c) =>
        c.url.startsWith("/api/telephony/credentials") && c.method === "POST",
    );
    expect(credsPost).toBeDefined();
    expect((credsPost?.body as { auth_token?: string }).auth_token).toBe(
      "new-secret-token",
    );
  });
});

describe("TelephonyView — self-test voice", () => {
  it("renders the returned transcript and response_text without clipping", async () => {
    const LONG_RESPONSE =
      "Selbstverständlich, Sir. Ich habe die Verbindung geprüft und alles " +
      "funktioniert einwandfrei über die gesamte Sprach-Kette hinweg, vom " +
      "eingehenden Audio über die Transkription bis hin zur Charon-Stimme.";

    const routes = okRoutes();
    routes["/api/telephony/selftest"] = () => ({
      body: {
        ok: true,
        transcript: "Hallo Jarvis, funktioniert das Telefon?",
        response_text: LONG_RESPONSE,
        audio_bytes: 48000,
      },
    });
    installFetchMock(routes);

    render(<TelephonyView />);

    await waitFor(() => {
      expect(screen.getByTestId("status-tts-voice").textContent).toBe("Charon");
    });

    fireEvent.click(screen.getByRole("button", { name: /Self-test voice/ }));

    await waitFor(() => {
      expect(screen.getByTestId("selftest-response").textContent).toBe(
        LONG_RESPONSE,
      );
    });
    expect(screen.getByTestId("selftest-transcript").textContent).toBe(
      "Hallo Jarvis, funktioniert das Telefon?",
    );
    // The long response is rendered with wrapping CSS, not clipped.
    const respEl = screen.getByTestId("selftest-response");
    expect(respEl.className).toContain("break-words");
    expect(respEl.className).toContain("whitespace-pre-wrap");
  });
});

describe("TelephonyView — graceful degradation", () => {
  it("shows a friendly not-installed notice when twilio is unavailable", async () => {
    const routes = okRoutes();
    routes["/api/telephony/status"] = () => ({
      body: {
        ...STATUS_OK,
        available: false,
        configured: false,
        twilio_reachable: false,
        auth_token_set: false,
      },
    });
    routes["/api/telephony/config"] = () => ({
      body: { ...CONFIG_OK, auth_token_set: false },
    });
    installFetchMock(routes);

    render(<TelephonyView />);

    await waitFor(() => {
      expect(
        screen.getByText("Telephony extra not installed"),
      ).toBeDefined();
    });
    // No crash: the credentials card still renders its Save button.
    expect(screen.getByRole("button", { name: /^Save$/ })).toBeDefined();
  });
});
