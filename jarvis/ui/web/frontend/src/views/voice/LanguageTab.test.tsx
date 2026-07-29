/**
 * Component tests for the voice section's Language tab.
 *
 * Two things are worth pinning about the language control: the four choices
 * must be rendered as human language names (not the bare codes the backend
 * speaks), and the "automatic detection is right for almost everyone"
 * reasoning must be on screen — it is the difference between a neutral-looking
 * dropdown and one that tells the user pinning a language can make recognition
 * worse.
 *
 * The wording pass lives on the same tab and is pinned for a third reason: it
 * lets a model rewrite what the user actually said. The switch must persist,
 * the description must state the trade honestly, and the Test button must show
 * the sample before AND after — because a pass that is invisible when it works
 * and silently falls back when it fails has no other way of being seen.
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
  polish: true,
  polish_provider: "auto",
  polish_model: "",
  polish_timeout_ms: 1200,
  polish_max_input_chars: 4000,
  polish_min_words: 4,
  polish_max_output_tokens: 1200,
  polish_temperature: 0.0,
  polish_drift_max_shrink: 0.55,
  polish_drift_max_growth: 1.2,
  polish_style: "neutral",
};

const CHOICES = {
  mode: ["hold", "toggle"],
  target: ["auto", "insert", "chat"],
  insert_method: ["clipboard", "type"],
  paste_chord: ["auto", "ctrl_v", "ctrl_shift_v", "shift_insert"],
  language: ["auto", "de", "en", "es"],
  polish_provider: ["auto", "groq", "gemini", "openrouter"],
  polish_style: ["neutral", "messaging", "email"],
};

// What POST /api/dictation/polish/test answers on a host that has a key: the
// backend's own fixed sample, plus the polished version of it.
const POLISH_TEST = {
  status: "applied",
  provider: "groq",
  model: "llama-3.1-8b-instant",
  latency_ms: 240,
  reason: "",
  sample_in: "so um i think we should ship the report on tuesday ... actually wednesday",
  sample_out: "I think we should ship the report on Wednesday.",
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

/** Open the themed picker and return its rendered options in list order. */
async function openPicker(testId: string) {
  const trigger = await waitFor(() => screen.getByTestId(testId));
  fireEvent.click(trigger);
  const panel = await waitFor(() => screen.getByTestId(`${testId}-panel`));
  return {
    trigger,
    panel,
    options: [...panel.querySelectorAll('[role="option"]')] as HTMLElement[],
  };
}

describe("LanguageTab", () => {
  it("offers automatic detection plus the three supported languages, by name", async () => {
    installFetchMock(routes());
    render(<LanguageTab hideHeader />);

    const { trigger, options } = await openPicker("dictation-language");
    // "Automatic" first, then alphabetically by the name shown in the user's
    // own UI language — not the order the backend happens to list the codes in.
    // With an English UI that puts English ahead of German; the recognizer
    // accepts ~100 languages, and an ISO-code ordering would be unreadable.
    expect(options.map((o) => o.getAttribute("data-value"))).toEqual([
      "auto",
      "en",
      "de",
      "es",
    ]);
    // The row's first span is the name in the user's UI language …
    expect(options.map((o) => o.querySelector("span")?.textContent)).toEqual([
      "Detect automatically",
      "English",
      "German",
      "Spanish",
    ]);
    // … and the second is what the language calls itself, which is how someone
    // who does not read the interface language finds their own row at all. It
    // is dropped where it would only repeat the name (English in an English UI).
    expect(options.map((o) => o.textContent)).toEqual([
      "Detect automatically",
      "English",
      "GermanDeutsch",
      "Spanishespañol",
    ]);
    expect(trigger.getAttribute("data-value")).toBe("auto");
    // The closed control still names the pick — a chevron alone would not.
    expect(trigger.textContent).toContain("Detect automatically");
  });

  it("puts the caret in the search field the moment the list opens", async () => {
    // The panel is not mounted on the render that opens it — it waits for its
    // first measurement — so focusing it from an effect keyed on "open" runs
    // against a ref that is still null, and everything the user types goes to
    // whatever had focus before. That shipped once; this pins it.
    installFetchMock(routes());
    render(<LanguageTab hideHeader />);

    await openPicker("dictation-language");
    expect(document.activeElement).toBe(
      screen.getByTestId("dictation-language-search"),
    );
  });

  it("walks the list and picks with the keyboard", async () => {
    const calls = installFetchMock(
      routes({
        "PUT /api/dictation/settings": () => ({
          body: { settings: { ...SETTINGS, language: "en" }, persisted: true },
        }),
      }),
    );
    render(<LanguageTab hideHeader />);

    const { panel } = await openPicker("dictation-language");
    const search = screen.getByTestId("dictation-language-search");
    // Opens highlighting the stored value ("auto"); one step down is English.
    fireEvent.keyDown(panel, { key: "ArrowDown" });
    fireEvent.keyDown(panel, { key: "Enter" });

    await waitFor(() => {
      const put = calls.find(
        (c) => c.method === "PUT" && c.url === "/api/dictation/settings",
      );
      expect(JSON.parse(put?.body ?? "{}")).toMatchObject({ language: "en" });
    });
    expect(search.isConnected).toBe(false);
  });

  it("closes on Escape without changing the setting", async () => {
    const calls = installFetchMock(routes());
    render(<LanguageTab hideHeader />);

    const { panel, trigger } = await openPicker("dictation-language");
    fireEvent.keyDown(panel, { key: "Escape" });

    await waitFor(() =>
      expect(screen.queryByTestId("dictation-language-panel")).toBeNull(),
    );
    // Focus comes back to the control that was operated, not to the page body.
    expect(document.activeElement).toBe(trigger);
    expect(
      calls.some((c) => c.method === "PUT" && c.url === "/api/dictation/settings"),
    ).toBe(false);
  });

  it("filters the list by name, own name, or code", async () => {
    installFetchMock(routes());
    render(<LanguageTab hideHeader />);

    const { panel } = await openPicker("dictation-language");
    const search = screen.getByTestId("dictation-language-search");

    // The English name, which is what an English UI shows.
    fireEvent.change(search, { target: { value: "germ" } });
    await waitFor(() =>
      expect(
        [...panel.querySelectorAll('[role="option"]')].map((o) =>
          o.getAttribute("data-value"),
        ),
      ).toEqual(["de"]),
    );

    // The language's OWN name — the entry point for someone who cannot read
    // the interface language they were handed.
    fireEvent.change(search, { target: { value: "Deutsch" } });
    await waitFor(() =>
      expect(
        [...panel.querySelectorAll('[role="option"]')].map((o) =>
          o.getAttribute("data-value"),
        ),
      ).toEqual(["de"]),
    );

    // And the bare code, for anyone who already knows it.
    fireEvent.change(search, { target: { value: "es" } });
    await waitFor(() =>
      expect(
        [...panel.querySelectorAll('[role="option"]')].map((o) =>
          o.getAttribute("data-value"),
        ),
      ).toContain("es"),
    );

    fireEvent.change(search, { target: { value: "zzzz" } });
    await waitFor(() =>
      expect(screen.queryByTestId("dictation-language-empty")).toBeTruthy(),
    );
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

    const { options } = await openPicker("dictation-language");
    fireEvent.click(options.find((o) => o.getAttribute("data-value") === "es")!);

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
    // Picking closes the panel and the trigger carries the new value.
    expect(screen.queryByTestId("dictation-language-panel")).toBeNull();
    await waitFor(() =>
      expect(
        screen.getByTestId("dictation-language").getAttribute("data-value"),
      ).toBe("es"),
    );
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

describe("LanguageTab — the wording pass", () => {
  it("names the trade: structure is rewritten, meaning and the raw text are kept", async () => {
    installFetchMock(routes());
    render(<LanguageTab hideHeader />);

    const description = await waitFor(() =>
      screen.getByTestId("dictation-polish-description"),
    );
    const text = (description.textContent ?? "").toLowerCase();
    expect(text).toContain("rewrites the structure");
    expect(text).toContain("meaning");
    expect(text).toContain("history");
  });

  it("persists the switch through the dictation settings endpoint", async () => {
    const calls = installFetchMock(
      routes({
        "PUT /api/dictation/settings": () => ({
          body: { settings: { ...SETTINGS, polish: false }, persisted: true },
        }),
      }),
    );
    render(<LanguageTab hideHeader />);

    const toggle = await waitFor(() =>
      screen.getByTestId("dictation-polish-toggle"),
    );
    expect(toggle.getAttribute("aria-checked")).toBe("true");
    fireEvent.click(toggle);

    await waitFor(() => {
      const put = calls.find(
        (c) => c.method === "PUT" && c.url === "/api/dictation/settings",
      );
      expect(put).toBeTruthy();
      expect(JSON.parse(put?.body ?? "{}")).toMatchObject({
        polish: false,
        persist: true,
      });
    });
  });

  it("pins a provider family from the backend's own list", async () => {
    const calls = installFetchMock(
      routes({
        "PUT /api/dictation/settings": () => ({
          body: {
            settings: { ...SETTINGS, polish_provider: "gemini" },
            persisted: true,
          },
        }),
      }),
    );
    render(<LanguageTab hideHeader />);

    const { options } = await openPicker("dictation-polish-provider");
    // The list comes over the wire — a hand-mirrored copy here would be the
    // AP-4 drift trap this hook deliberately avoids.
    expect(options.map((o) => o.getAttribute("data-value"))).toEqual([
      "auto",
      "groq",
      "gemini",
      "openrouter",
    ]);
    fireEvent.click(
      options.find((o) => o.getAttribute("data-value") === "gemini")!,
    );

    await waitFor(() => {
      const put = calls.find(
        (c) => c.method === "PUT" && c.url === "/api/dictation/settings",
      );
      expect(JSON.parse(put?.body ?? "{}")).toMatchObject({
        polish_provider: "gemini",
        persist: true,
      });
    });
  });

  it("shows the sample before and after when the test runs", async () => {
    installFetchMock(
      routes({ "POST /api/dictation/polish/test": () => ({ body: POLISH_TEST }) }),
    );
    render(<LanguageTab hideHeader />);

    fireEvent.click(
      await waitFor(() => screen.getByTestId("dictation-polish-test")),
    );

    await waitFor(() =>
      expect(screen.queryByTestId("dictation-polish-test-result")).toBeTruthy(),
    );
    expect(screen.getByTestId("dictation-polish-sample-in").textContent).toBe(
      POLISH_TEST.sample_in,
    );
    expect(screen.getByTestId("dictation-polish-sample-out").textContent).toBe(
      POLISH_TEST.sample_out,
    );
    // The status is translated, never the raw backend token.
    const result = screen.getByTestId("dictation-polish-test-result");
    expect(result.textContent).toContain("Cleaned up");
    expect(result.textContent).not.toContain("applied");
  });

  it("hides the provider and the test while the pass is switched off", async () => {
    installFetchMock(
      routes({
        "GET /api/dictation/settings": () => ({
          body: { settings: { ...SETTINGS, polish: false }, choices: CHOICES },
        }),
      }),
    );
    render(<LanguageTab hideHeader />);

    await waitFor(() =>
      expect(screen.queryByTestId("dictation-polish-toggle")).toBeTruthy(),
    );
    expect(screen.queryByTestId("dictation-polish-provider")).toBeNull();
    expect(screen.queryByTestId("dictation-polish-test")).toBeNull();
  });
});
