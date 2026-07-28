/**
 * Component tests for the voice section's Language tab.
 *
 * Two things are worth pinning: the four choices must be rendered as human
 * language names (not the bare codes the backend speaks), and the "automatic
 * detection is right for almost everyone" reasoning must be on screen — it is
 * the difference between a neutral-looking dropdown and one that tells the
 * user pinning a language can make recognition worse.
 *
 * No jest-dom in this repo — assertions use toBeTruthy()/toBeNull().
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

vi.mock("@/views/ChatsView", () => ({
  ViewHeader: ({ title }: { title: string }) => <header>{title}</header>,
}));

import { LanguageTab } from "@/views/voice/LanguageTab";
import { setUiLanguage } from "@/i18n";

interface RouteResult {
  status?: number;
  body: unknown;
}
interface Call {
  url: string;
  method: string;
  body: string | null;
}

function installFetchMock(routes: Record<string, () => RouteResult>) {
  const calls: Call[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    calls.push({ url, method, body: (init?.body as string | undefined) ?? null });
    const keys = Object.keys(routes).sort((a, b) => b.length - a.length);
    for (const key of keys) {
      const [routeMethod, prefix] = key.split(" ");
      if (method === routeMethod && url.startsWith(prefix)) {
        const { status = 200, body: resBody } = routes[key]();
        return {
          ok: status >= 200 && status < 300,
          status,
          statusText: status >= 200 && status < 300 ? "OK" : "ERR",
          json: async () => resBody,
          text: async () => JSON.stringify(resBody),
        } as Response;
      }
    }
    throw new Error(`unexpected fetch ${method} ${url}`);
  });
  (globalThis as unknown as { fetch: typeof fetch }).fetch =
    fetchMock as unknown as typeof fetch;
  return calls;
}

const SETTINGS = {
  mode: "hold",
  target: "auto",
  insert_method: "clipboard",
  paste_chord: "auto",
  paste_delay_ms: 40,
  paste_delay_after_ms: 40,
  restore_clipboard: true,
  remove_fillers: true,
  filler_max_removed_fraction: 0.3,
  max_seconds: 300,
  partial_interval_s: 1.0,
  segment_seconds: 8.0,
  history_enabled: true,
  history_max_entries: 200,
  history_retention_days: 30,
  language: "auto",
  keep_failed_audio: true,
  audio_retention_days: 7,
  audio_max_files: 20,
};

const CHOICES = {
  mode: ["hold", "toggle"],
  target: ["auto", "insert", "chat"],
  insert_method: ["clipboard", "type"],
  paste_chord: ["auto", "ctrl_v", "ctrl_shift_v", "shift_insert"],
  language: ["auto", "de", "en", "es"],
};

const STATUS = {
  available: true,
  active: false,
  reason: "",
  hotkey: "",
  hotkey_toggle: "",
  mode: "hold",
  target: "auto",
  insertion: { can_insert: true, reason: "", detail: "" },
};

function routes(extra: Record<string, () => RouteResult> = {}) {
  return {
    "GET /api/dictation/status": () => ({ body: STATUS }),
    "GET /api/dictation/settings": () => ({
      body: { settings: SETTINGS, choices: CHOICES },
    }),
    "GET /api/dictation/history": () => ({ body: { entries: [], count: 0 } }),
    "GET /api/dictation/stats": () => ({ status: 404, body: { detail: "none" } }),
    "PUT /api/settings/ui-language": () => ({ body: { ok: true } }),
    ...extra,
  };
}

beforeEach(() => {
  setUiLanguage("en");
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("LanguageTab", () => {
  it("offers automatic detection plus the three supported languages, by name", async () => {
    installFetchMock(routes());
    render(<LanguageTab hideHeader />);

    await waitFor(() =>
      expect(screen.queryByTestId("dictation-language")).toBeTruthy(),
    );
    const select = screen.getByTestId("dictation-language") as HTMLSelectElement;
    expect([...select.options].map((o) => o.value)).toEqual([
      "auto",
      "de",
      "en",
      "es",
    ]);
    expect([...select.options].map((o) => o.textContent)).toEqual([
      "Detect automatically",
      "German",
      "English",
      "Spanish",
    ]);
    expect(select.value).toBe("auto");
  });

  it("says why automatic is the right choice for almost everyone", async () => {
    installFetchMock(routes());
    render(<LanguageTab hideHeader />);

    await waitFor(() =>
      expect(screen.queryByTestId("dictation-language-hint")).toBeTruthy(),
    );
    const hint = screen.getByTestId("dictation-language-hint").textContent ?? "";
    expect(hint.toLowerCase()).toContain("almost everyone");
    expect(hint.toLowerCase()).toContain("worse");
  });

  it("saves the pick through the dictation settings endpoint", async () => {
    const calls = installFetchMock(
      routes({
        "PUT /api/dictation/settings": () => ({
          body: { settings: { ...SETTINGS, language: "es" }, persisted: true },
        }),
      }),
    );
    render(<LanguageTab hideHeader />);

    await waitFor(() =>
      expect(screen.queryByTestId("dictation-language")).toBeTruthy(),
    );
    fireEvent.change(screen.getByTestId("dictation-language"), {
      target: { value: "es" },
    });

    await waitFor(() => {
      const put = calls.find(
        (c) => c.method === "PUT" && c.url === "/api/dictation/settings",
      );
      expect(put).toBeTruthy();
      expect(JSON.parse(put?.body ?? "{}")).toMatchObject({
        language: "es",
        persist: true,
      });
    });
    expect(
      (screen.getByTestId("dictation-language") as HTMLSelectElement).value,
    ).toBe("es");
  });

  it("renders its own header when used standalone", async () => {
    installFetchMock(routes());
    const { container } = render(<LanguageTab />);

    await waitFor(() =>
      expect(screen.queryByTestId("dictation-language")).toBeTruthy(),
    );
    expect(container.querySelector("header")).toBeTruthy();
  });
});
