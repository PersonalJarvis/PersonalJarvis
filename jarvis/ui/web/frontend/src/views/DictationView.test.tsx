/**
 * Component tests for DictationView — the history/stats surface of the merged
 * voice section.
 *
 * What is actually pinned here is the behaviour a user would notice if it
 * broke: the stats strip must never label a rolling window "all time", the
 * outcome badge must be a translated phrase rather than the raw server token,
 * the search box must filter on both the delivered and the raw transcript, and
 * the trash icon must discard (recoverable) with the hard delete behind a
 * separate, deliberate step. "Delete all" — the one action with no restore —
 * must sit behind a confirmation.
 *
 * Driven through a mocked fetch, mirroring ContactsView.test.tsx. No jest-dom
 * in this repo — assertions use toBeTruthy()/toBeNull().
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render as rtlRender, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactElement } from "react";

import { useEventStore } from "@/store/events";

const { copyMock } = vi.hoisted(() => ({ copyMock: vi.fn(async () => true) }));
vi.mock("@/lib/clipboard", () => ({ robustCopy: copyMock }));

import { DictationView } from "@/views/DictationView";
import { setUiLanguage } from "@/i18n";

/** The greeting reads the profile name through react-query. */
function render(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return rtlRender(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

interface RouteResult {
  status?: number;
  body: unknown;
}
interface Call {
  url: string;
  method: string;
  /** Parsed request body, or null for a request that carried none. */
  body: unknown;
}

function installFetchMock(routes: Record<string, () => RouteResult>) {
  const calls: Call[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    calls.push({
      url,
      method,
      body: init?.body ? JSON.parse(init.body as string) : null,
    });
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

const STATUS = {
  available: true,
  active: false,
  reason: "",
  hotkey: "ctrl+right_alt+j",
  hotkey_toggle: "ctrl+right_alt+space",
  mode: "hold",
  target: "auto",
  insertion: { can_insert: true, reason: "", detail: "" },
};

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

/**
 * The `custom` block of GET /api/dictation/settings — the token vocabulary the
 * recorder validates against, served by the backend so no copy of it lives in
 * the frontend.
 */
const CUSTOM = {
  paste_chord: {
    allowed: true,
    separator: "+",
    modifiers: ["alt", "cmd", "ctrl", "shift", "win"],
    keys: ["a", "insert", "v", "x"],
    detail: "The paste shortcut of the app you dictate into.",
  },
};

const STATS = {
  source: "lifetime",
  window: { days: 30, max_entries: 200 },
  totals: { dictations: 12, words: 320, seconds: 107.2, wpm: 178.8 },
  today: { dictations: 4, words: 120 },
  streak: { current_days: 6, longest_days: 14 },
  by_day: [{ date: "2026-07-28", dictations: 4, words: 120, seconds: 40.1 }],
};

/** A timestamp `daysAgo` days back, at a fixed hour so it never straddles midnight. */
function stamp(daysAgo: number): string {
  const d = new Date();
  d.setDate(d.getDate() - daysAgo);
  d.setHours(12, 0, 0, 0);
  return d.toISOString();
}

function entry(over: Partial<Record<string, unknown>> = {}) {
  return {
    id: "d-1",
    created_at: stamp(0),
    raw_text: "so uh send the report",
    text: "send the report",
    language: "en",
    duration_s: 4.2,
    outcome: "inserted",
    method: "clipboard",
    removed_words: 2,
    cleanup_reason: "",
    word_count: 3,
    discarded: false,
    audio_available: false,
    error: null,
    ...over,
  };
}

const TODAY_ENTRY = entry();
const YESTERDAY_ENTRY = entry({
  id: "d-2",
  created_at: stamp(1),
  raw_text: "book the flight",
  text: "book the flight",
  outcome: "unavailable",
  removed_words: 0,
});
const DISCARDED_ENTRY = entry({
  id: "d-3",
  created_at: stamp(1),
  raw_text: "",
  text: "",
  outcome: "failed",
  discarded: true,
  audio_available: true,
  error: "provider returned 401",
  removed_words: 0,
});

function defaultRoutes(
  entries: unknown[] = [TODAY_ENTRY, YESTERDAY_ENTRY, DISCARDED_ENTRY],
  extra: Record<string, () => RouteResult> = {},
) {
  return {
    "GET /api/dictation/status": () => ({ body: STATUS }),
    "GET /api/dictation/settings": () => ({
      body: { settings: SETTINGS, choices: CHOICES, custom: CUSTOM },
    }),
    "GET /api/dictation/history": () => ({ body: { entries, count: entries.length } }),
    "GET /api/dictation/stats": () => ({ body: STATS }),
    "PUT /api/settings/ui-language": () => ({ body: { ok: true } }),
    "GET /api/profile": () => ({ body: { user: { name: "Ada" } } }),
    "GET /api/dictionary": () => ({
      body: {
        entries: [
          { id: "w-1", word: "GitHub", misheard: ["git hub"], created_at: "", updated_at: "" },
          { id: "w-2", word: "Claude", misheard: [], created_at: "", updated_at: "" },
        ],
      },
    }),
    ...extra,
  };
}

beforeEach(() => {
  setUiLanguage("en");
  copyMock.mockClear();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("DictationView stats strip", () => {
  it("labels lifetime totals as all time and shows words, speed and streak", async () => {
    installFetchMock(defaultRoutes());
    render(<DictationView />);

    await waitFor(() => expect(screen.queryByTestId("dictation-stats")).toBeTruthy());
    expect(screen.getByTestId("dictation-stats-window").textContent).toBe("All time");
    expect(screen.getByTestId("dictation-stat-words").textContent).toBe("320");
    expect(screen.getByTestId("dictation-stat-wpm").textContent).toBe("179");
    expect(screen.getByTestId("dictation-stat-streak").textContent).toBe("6");
  });

  it("never calls a rolling window 'all time'", async () => {
    installFetchMock(
      defaultRoutes(undefined, {
        "GET /api/dictation/stats": () => ({
          body: { ...STATS, source: "window" },
        }),
      }),
    );
    render(<DictationView />);

    await waitFor(() => expect(screen.queryByTestId("dictation-stats")).toBeTruthy());
    expect(screen.getByTestId("dictation-stats-window").textContent).toBe(
      "Last 30 days",
    );
  });

  it("hides the strip instead of erroring when stats are unavailable", async () => {
    installFetchMock(
      defaultRoutes(undefined, {
        "GET /api/dictation/stats": () => ({
          status: 404,
          body: { detail: "no stats" },
        }),
      }),
    );
    render(<DictationView />);

    await waitFor(() => expect(screen.queryByTestId("dictation-history")).toBeTruthy());
    expect(screen.queryByTestId("dictation-stats")).toBeNull();
    expect(screen.queryByText("no stats")).toBeNull();
  });
});

describe("DictationView history", () => {
  it("groups entries by day with Today and Yesterday headers, newest first", async () => {
    installFetchMock(defaultRoutes());
    render(<DictationView />);

    await waitFor(() => expect(screen.queryByTestId("dictation-history")).toBeTruthy());
    const labels = screen
      .getAllByTestId("dictation-history-group-label")
      .map((el) => el.textContent);
    expect(labels).toEqual(["Today", "Yesterday"]);
  });

  it("renders the outcome through i18n, never the raw server token", async () => {
    installFetchMock(defaultRoutes());
    render(<DictationView />);

    await waitFor(() => expect(screen.queryByTestId("dictation-history")).toBeTruthy());
    const badges = screen
      .getAllByTestId("dictation-outcome-badge")
      .map((el) => el.textContent);
    expect(badges).toContain("Inserted");
    expect(badges).toContain("Could not insert");
    expect(badges).toContain("Failed");
    expect(badges).not.toContain("inserted");
    expect(badges).not.toContain("unavailable");
  });

  it("filters the history case-insensitively over text and raw text", async () => {
    installFetchMock(defaultRoutes());
    render(<DictationView />);

    await waitFor(() => expect(screen.queryByTestId("dictation-history")).toBeTruthy());
    expect(screen.getAllByTestId("dictation-history-row")).toHaveLength(3);

    // "SO UH" only exists in the raw transcript of the first entry.
    fireEvent.change(screen.getByTestId("dictation-search"), {
      target: { value: "SO UH" },
    });
    const rows = screen.getAllByTestId("dictation-history-row");
    expect(rows).toHaveLength(1);
    expect(rows[0].getAttribute("data-entry-id")).toBe("d-1");

    fireEvent.change(screen.getByTestId("dictation-search"), {
      target: { value: "zzz" },
    });
    expect(screen.queryByTestId("dictation-history")).toBeNull();
    expect(screen.queryByTestId("dictation-no-matches")).toBeTruthy();
  });

  it("copies an entry's delivered text to the clipboard", async () => {
    installFetchMock(defaultRoutes([TODAY_ENTRY]));
    render(<DictationView />);

    await waitFor(() => expect(screen.queryByTestId("dictation-history")).toBeTruthy());
    fireEvent.click(screen.getAllByTestId("dictation-copy-entry")[0]);

    await waitFor(() => expect(copyMock).toHaveBeenCalledWith("send the report"));
  });
});

describe("DictationView delete semantics", () => {
  it("discards from the trash icon instead of hard-deleting", async () => {
    const calls = installFetchMock(
      defaultRoutes([TODAY_ENTRY], {
        "POST /api/dictation/history/d-1/discard": () => ({
          body: { ok: true, entry: { ...TODAY_ENTRY, discarded: true } },
        }),
      }),
    );
    render(<DictationView />);

    await waitFor(() => expect(screen.queryByTestId("dictation-history")).toBeTruthy());
    fireEvent.click(screen.getAllByTestId("dictation-discard-entry")[0]);

    await waitFor(() =>
      expect(screen.queryByTestId("dictation-discarded-badge")).toBeTruthy(),
    );
    expect(
      calls.some(
        (c) => c.method === "POST" && c.url.endsWith("/history/d-1/discard"),
      ),
    ).toBe(true);
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    // The row stays listed — a filtered-out row could never be restored.
    expect(screen.getAllByTestId("dictation-history-row")).toHaveLength(1);
  });

  it("puts the hard delete behind a second explicit step", async () => {
    const calls = installFetchMock(
      defaultRoutes([DISCARDED_ENTRY], {
        "DELETE /api/dictation/history/d-3": () => ({ body: { ok: true } }),
      }),
    );
    render(<DictationView />);

    await waitFor(() => expect(screen.queryByTestId("dictation-history")).toBeTruthy());
    const button = screen.getByTestId("dictation-delete-permanently");

    // First click only arms it; nothing has left the disk yet.
    fireEvent.click(button);
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);

    fireEvent.click(screen.getByTestId("dictation-delete-permanently"));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE")).toBe(true));
  });

  it("offers Restore for a discarded entry and un-discards it", async () => {
    const calls = installFetchMock(
      defaultRoutes([DISCARDED_ENTRY], {
        "POST /api/dictation/history/d-3/restore": () => ({
          body: {
            ok: true,
            entry: {
              ...DISCARDED_ENTRY,
              discarded: false,
              text: "call the studio",
              raw_text: "call the studio",
              outcome: "inserted",
              error: null,
            },
            retranscribed: true,
            detail: null,
          },
        }),
      }),
    );
    render(<DictationView />);

    await waitFor(() => expect(screen.queryByTestId("dictation-history")).toBeTruthy());
    fireEvent.click(screen.getByTestId("dictation-restore-entry"));

    await waitFor(() =>
      expect(screen.queryByTestId("dictation-discarded-badge")).toBeNull(),
    );
    expect(
      calls.some(
        (c) => c.method === "POST" && c.url.endsWith("/history/d-3/restore"),
      ),
    ).toBe(true);
    expect(screen.queryByText("call the studio")).toBeTruthy();
  });

  it("does not offer Restore for a plain successful entry", async () => {
    installFetchMock(defaultRoutes([TODAY_ENTRY]));
    render(<DictationView />);

    await waitFor(() => expect(screen.queryByTestId("dictation-history")).toBeTruthy());
    expect(screen.queryByTestId("dictation-restore-entry")).toBeNull();
  });
});

describe("DictationView settings", () => {
  /**
   * The "How dictation behaves" block is gone from this screen on purpose —
   * every one of its six controls shipped a working default, and a wall of
   * dropdowns and switches in front of the feature made a thing that just works
   * look like something to configure first.
   *
   * Pinned as an absence rather than deleted silently: re-adding any of these
   * controls here is a product decision, not a refactor. The `[dictation]`
   * config keys and `PUT /api/dictation/settings` are untouched — an install
   * that needs a different paste shortcut still has one, just not on this
   * screen.
   */
  it("no longer shows the behaviour settings block", async () => {
    installFetchMock(defaultRoutes());
    render(<DictationView />);

    await waitFor(() => expect(screen.queryByTestId("dictation-history")).toBeTruthy());
    expect(screen.queryByText("How dictation behaves")).toBeNull();
    for (const testId of [
      "dictation-paste-chord",
      "dictation-insert-method",
      "dictation-target",
      "dictation-remove-fillers",
      "dictation-restore-clipboard",
      "dictation-history-enabled",
      // Left earlier, for the same reason: the Shortcuts tab is the source of
      // truth for hold vs hands-free.
      "dictation-mode",
    ]) {
      expect(screen.queryByTestId(testId)).toBeNull();
    }
  });

  it("never writes a dictation setting from this screen", async () => {
    const calls = installFetchMock(defaultRoutes());
    render(<DictationView />);

    await waitFor(() => expect(screen.queryByTestId("dictation-history")).toBeTruthy());
    expect(
      calls.some((c) => c.method === "PUT" && c.url === "/api/dictation/settings"),
    ).toBe(false);
  });
});

describe("DictationView header", () => {
  it("stands its header down when embedded in the voice hub", async () => {
    installFetchMock(defaultRoutes());
    const { container } = render(<DictationView hideHeader />);

    await waitFor(() => expect(screen.queryByTestId("dictation-history")).toBeTruthy());
    expect(container.querySelector("header")).toBeNull();
  });
});

describe("DictationView home layout", () => {
  it("greets the person by their profile name", async () => {
    installFetchMock(defaultRoutes());
    render(<DictationView />);

    await waitFor(() =>
      expect(screen.getByTestId("dictation-welcome").textContent).toBe("Welcome back, Ada"),
    );
  });

  it("puts the dictation key in the instruction as keycaps, pretty-printed", async () => {
    installFetchMock(defaultRoutes());
    render(<DictationView />);

    await waitFor(() => expect(screen.queryByTestId("dictation-history")).toBeTruthy());
    const instruction = screen.getByTestId("dictation-instruction");
    expect(instruction.textContent).toContain("to dictate");
    expect(instruction.textContent).not.toContain("ctrl+right_alt+j");
    const caps = [...instruction.querySelectorAll("kbd")].map((el) => el.textContent);
    expect(caps).toHaveLength(3);
    expect(caps[2]).toBe("J");
    expect(screen.getByTestId("dictation-state").textContent).toBe("Ready");
    // The state word is what a screen reader hears change.
    expect(screen.getByTestId("dictation-state").getAttribute("aria-live")).toBe("polite");
  });

  it("names the hands-free combo as a second line", async () => {
    installFetchMock(defaultRoutes());
    render(<DictationView />);

    await waitFor(() => expect(screen.queryByTestId("dictation-hands-free")).toBeTruthy());
    const line = screen.getByTestId("dictation-hands-free");
    expect(line.textContent).toContain("hands-free");
    expect(line.querySelectorAll("kbd")).toHaveLength(3);
  });

  it("draws no stock photograph", async () => {
    installFetchMock(defaultRoutes());
    const { container } = render(<DictationView />);

    await waitFor(() => expect(screen.queryByTestId("dictation-history")).toBeTruthy());
    expect(container.querySelector("img")).toBeNull();
  });

  it("shows the dictionary's size and opens the Dictionary tab", async () => {
    installFetchMock(defaultRoutes());
    render(<DictationView />);

    await waitFor(() =>
      expect(screen.queryByTestId("dictation-vocab-count")?.textContent).toBe("2 entries"),
    );
    fireEvent.click(screen.getByTestId("dictation-open-dictionary"));
    expect(useEventStore.getState().activeSection).toBe("dictionary");
  });

  it("sends Change shortcut to the Shortcuts tab", async () => {
    installFetchMock(defaultRoutes());
    render(<DictationView />);

    await waitFor(() => expect(screen.queryByTestId("dictation-open-shortcuts")).toBeTruthy());
    fireEvent.click(screen.getByTestId("dictation-open-shortcuts"));
    expect(useEventStore.getState().activeSection).toBe("voice-shortcuts");
  });

  it("draws fourteen days of activity", async () => {
    installFetchMock(defaultRoutes());
    render(<DictationView />);

    await waitFor(() => expect(screen.queryByTestId("dictation-activity")).toBeTruthy());
    expect(screen.getByTestId("dictation-activity").children).toHaveLength(14);
  });

  it("keeps what was heard one click away instead of under every row", async () => {
    installFetchMock(defaultRoutes([TODAY_ENTRY]));
    render(<DictationView />);

    await waitFor(() => expect(screen.queryByTestId("dictation-history")).toBeTruthy());
    expect(screen.queryByTestId("dictation-raw-text")).toBeNull();
    fireEvent.click(screen.getByTestId("dictation-toggle-raw"));
    expect(screen.getByTestId("dictation-raw-text").textContent).toContain(
      "so uh send the report",
    );
  });

  it("shows a designed empty surface when nothing was dictated yet", async () => {
    installFetchMock(defaultRoutes([]));
    render(<DictationView />);

    await waitFor(() => expect(screen.queryByTestId("dictation-empty")).toBeTruthy());
    expect(screen.queryByTestId("dictation-history")).toBeNull();
    // Nothing to search or delete yet.
    expect(screen.queryByTestId("dictation-search")).toBeNull();
    expect(screen.queryByTestId("dictation-clear-history")).toBeNull();
  });
});

describe("DictationView control panel", () => {
  it("starts with target auto, then stops, and says it is recording in between", async () => {
    let active = false;
    const calls = installFetchMock(
      defaultRoutes(undefined, {
        "GET /api/dictation/status": () => ({ body: { ...STATUS, active } }),
        "POST /api/dictation/start": () => {
          active = true;
          return { body: { ok: true } };
        },
        "POST /api/dictation/stop": () => {
          active = false;
          return { body: { ok: true } };
        },
      }),
    );
    render(<DictationView />);

    await waitFor(() =>
      expect(screen.getByTestId("dictation-toggle").hasAttribute("disabled")).toBe(false),
    );
    expect(screen.getByTestId("dictation-toggle").textContent).toBe("Start dictating");
    fireEvent.click(screen.getByTestId("dictation-toggle"));

    await waitFor(() =>
      expect(screen.getByTestId("dictation-state").textContent).toBe("Recording"),
    );
    const startCall = calls.find((c) => c.method === "POST" && c.url === "/api/dictation/start");
    expect(startCall?.body).toEqual({ target: "auto" });
    expect(screen.getByTestId("dictation-elapsed").textContent).toBe("0:00");
    await waitFor(() => expect(screen.getByTestId("dictation-toggle").textContent).toBe("Stop"));

    fireEvent.click(screen.getByTestId("dictation-toggle"));
    await waitFor(() => expect(screen.getByTestId("dictation-state").textContent).toBe("Ready"));
    expect(calls.some((c) => c.method === "POST" && c.url === "/api/dictation/stop")).toBe(true);
    expect(screen.queryByTestId("dictation-elapsed")).toBeNull();
  });

  it("disables the button and names the reason when dictation is unavailable", async () => {
    installFetchMock(
      defaultRoutes(undefined, {
        "GET /api/dictation/status": () => ({
          body: { ...STATUS, available: false, reason: "No microphone found." },
        }),
      }),
    );
    render(<DictationView />);

    await waitFor(() =>
      expect(screen.getByTestId("dictation-state").textContent).toBe(
        "Not available on this computer",
      ),
    );
    expect(screen.getByTestId("dictation-toggle").hasAttribute("disabled")).toBe(true);
    expect(screen.queryByText("No microphone found.")).toBeTruthy();
    expect(screen.queryByTestId("dictation-open-shortcuts")).toBeNull();
  });

  it("puts a blocked insertion above the panel and in the state line", async () => {
    installFetchMock(
      defaultRoutes(undefined, {
        "GET /api/dictation/status": () => ({
          body: {
            ...STATUS,
            insertion: { can_insert: false, reason: "wayland", detail: "Wayland blocks typing." },
          },
        }),
      }),
    );
    render(<DictationView />);

    await waitFor(() => expect(screen.queryByTestId("dictation-insert-warning")).toBeTruthy());
    const warning = screen.getByTestId("dictation-insert-warning");
    expect(warning.textContent).toContain("Wayland blocks typing.");
    // Above the fold: the warning precedes the panel in document order.
    expect(
      warning.compareDocumentPosition(screen.getByTestId("dictation-panel")) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(screen.getByTestId("dictation-state").textContent).toBe(
      "Ready — text goes to your clipboard",
    );
  });
});

describe("DictationView stats while loading", () => {
  it("paints skeletons, never zeros, until the numbers arrive", async () => {
    let releaseStats: () => void = () => {};
    const statsGate = new Promise<void>((resolve) => {
      releaseStats = resolve;
    });
    installFetchMock(defaultRoutes());
    const routed = globalThis.fetch;
    (globalThis as unknown as { fetch: typeof fetch }).fetch = (async (
      input: RequestInfo | URL,
      init?: RequestInit,
    ) => {
      if (String(input).startsWith("/api/dictation/stats")) await statsGate;
      return routed(input, init);
    }) as typeof fetch;
    render(<DictationView />);

    expect(screen.queryByTestId("dictation-stats-loading")).toBeTruthy();
    expect(screen.queryByTestId("dictation-stat-words")).toBeNull();

    releaseStats();
    await waitFor(() => expect(screen.queryByTestId("dictation-stats")).toBeTruthy());
    expect(screen.queryByTestId("dictation-stats-loading")).toBeNull();
    expect(screen.getByTestId("dictation-stat-today").textContent).toBe("120");
  });
});

describe("DictationView delete all", () => {
  it("asks before deleting everything, and Cancel keeps the history", async () => {
    const calls = installFetchMock(defaultRoutes());
    render(<DictationView />);

    await waitFor(() => expect(screen.queryByTestId("dictation-history")).toBeTruthy());
    fireEvent.click(screen.getByTestId("dictation-clear-history"));

    const dialog = await screen.findByRole("dialog");
    expect(dialog.textContent).toContain("Delete your whole dictation history?");
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    // Cancel holds the initial focus so a stray Enter keeps the history.
    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByTestId("dictation-clear-cancel")),
    );

    fireEvent.click(screen.getByTestId("dictation-clear-cancel"));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    expect(screen.getAllByTestId("dictation-history-row")).toHaveLength(3);
  });

  it("clears the history once confirmed", async () => {
    const calls = installFetchMock(
      defaultRoutes([TODAY_ENTRY, YESTERDAY_ENTRY], {
        "DELETE /api/dictation/history": () => ({ body: { ok: true } }),
      }),
    );
    render(<DictationView />);

    await waitFor(() => expect(screen.queryByTestId("dictation-history")).toBeTruthy());
    fireEvent.click(screen.getByTestId("dictation-clear-history"));
    fireEvent.click(await screen.findByTestId("dictation-clear-confirm"));

    await waitFor(() => expect(screen.queryByTestId("dictation-empty")).toBeTruthy());
    expect(
      calls.some((c) => c.method === "DELETE" && c.url === "/api/dictation/history"),
    ).toBe(true);
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });
});

describe("DictationView history row", () => {
  it("lets an armed permanent delete be called off", async () => {
    const calls = installFetchMock(defaultRoutes([DISCARDED_ENTRY]));
    render(<DictationView />);

    await waitFor(() => expect(screen.queryByTestId("dictation-history")).toBeTruthy());
    fireEvent.click(screen.getByTestId("dictation-delete-permanently"));
    fireEvent.click(screen.getByTestId("dictation-delete-cancel"));

    expect(screen.getByTestId("dictation-delete-permanently").textContent).toBe(
      "Delete permanently",
    );
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
  });

  it("labels every row action for assistive technology", async () => {
    installFetchMock(defaultRoutes([TODAY_ENTRY]));
    render(<DictationView />);

    await waitFor(() => expect(screen.queryByTestId("dictation-history")).toBeTruthy());
    for (const testId of ["dictation-copy-entry", "dictation-discard-entry"]) {
      const button = screen.getByTestId(testId);
      expect(button.getAttribute("aria-label")).toBeTruthy();
      expect(button.getAttribute("title")).toBeTruthy();
    }
  });

  it("folds a long transcript and unfolds it on request", async () => {
    const long = "word ".repeat(80).trim();
    installFetchMock(defaultRoutes([entry({ text: long, raw_text: long })]));
    render(<DictationView />);

    await waitFor(() => expect(screen.queryByTestId("dictation-history")).toBeTruthy());
    expect(screen.getByTestId("dictation-entry-text").className).toContain("line-clamp-4");
    fireEvent.click(screen.getByTestId("dictation-toggle-more"));
    expect(screen.getByTestId("dictation-entry-text").className).not.toContain("line-clamp-4");
  });
});
