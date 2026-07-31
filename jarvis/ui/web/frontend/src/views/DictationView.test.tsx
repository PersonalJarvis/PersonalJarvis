/**
 * Component tests for DictationView — the history/stats surface of the merged
 * voice section.
 *
 * What is actually pinned here is the behaviour a user would notice if it
 * broke: the stats strip must never label a rolling window "all time", the
 * outcome badge must be a translated phrase rather than the raw server token,
 * the search box must filter on both the delivered and the raw transcript, and
 * the trash icon must discard (recoverable) with the hard delete behind a
 * separate, deliberate step.
 *
 * Driven through a mocked fetch, mirroring ContactsView.test.tsx. No jest-dom
 * in this repo — assertions use toBeTruthy()/toBeNull().
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

// ViewHeader lives in ChatsView, which drags in the whole chat surface; the
// view only needs its shape.
vi.mock("@/views/ChatsView", () => ({
  ViewHeader: ({ title }: { title: string }) => <header>{title}</header>,
}));

const { copyMock } = vi.hoisted(() => ({ copyMock: vi.fn(async () => true) }));
vi.mock("@/lib/clipboard", () => ({ robustCopy: copyMock }));

import { DictationView } from "@/views/DictationView";
import { setUiLanguage } from "@/i18n";

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
