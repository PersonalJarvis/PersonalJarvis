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
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { ProviderCard } from "@/components/providers/ProviderTierSection";
import type { ProviderDescriptor } from "@/hooks/useProviders";

interface Call {
  url: string;
  method: string;
  body: string | null;
}

function installFetchMock(status = 200): Call[] {
  const calls: Call[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    calls.push({
      url,
      method: (init?.method ?? "GET").toUpperCase(),
      body: (init?.body as string | undefined) ?? null,
    });
    return {
      ok: status >= 200 && status < 300,
      status,
      statusText: status >= 200 && status < 300 ? "OK" : "ERR",
      json: async () => ({}),
      text: async () => "{}",
    } as Response;
  });
  (globalThis as unknown as { fetch: typeof fetch }).fetch =
    fetchMock as unknown as typeof fetch;
  return calls;
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

function renderCard(descriptor: ProviderDescriptor) {
  return render(
    <ProviderCard
      descriptor={descriptor}
      onChanged={() => {}}
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
