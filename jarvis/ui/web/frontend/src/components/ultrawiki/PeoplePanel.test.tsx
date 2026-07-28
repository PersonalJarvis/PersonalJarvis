/**
 * PeoplePanel tests — the corpus seen through people.
 *
 * Three properties carry this view, and each of them once had a plausible
 * wrong implementation:
 *
 * 1. A profile is TWO halves. The standing facts (identifiers) and the things
 *    that happened (events) come from different layers and different
 *    requests, so a corpus with no event extraction must still render a
 *    working profile instead of an error page.
 * 2. Typing is instant and local, but a search for an e-mail address cannot
 *    be — identifiers are not in the list payload. The server is asked only
 *    once the local list has genuinely nothing.
 * 3. A merge that still stands can be undone from the profile; one that was
 *    already undone must not offer the button, and a refusal to undo is
 *    printed as the store worded it.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { PeoplePanel } from "@/components/ultrawiki/PeoplePanel";

const COUNTS = {
  entities: 3,
  people: 2,
  identifiers: 5,
  pending_confirmations: 1,
  merges: 1,
};

const PEOPLE = [
  {
    id: 11,
    kind: "person",
    display_name: "Rita Vasquez",
    source_ref: "contacts:rita",
    identifier_count: 4,
    created_at: "2026-07-01T10:00:00Z",
    updated_at: "2026-07-02T10:00:00Z",
  },
  {
    id: 21,
    kind: "person",
    display_name: "Tomas Bauer",
    source_ref: "",
    identifier_count: 1,
    created_at: "2026-07-03T10:00:00Z",
    updated_at: "2026-07-03T10:00:00Z",
  },
];

const PROPOSAL = {
  id: 7,
  status: "pending",
  score: 0.87,
  evidence: [
    { tier: "probable", kind: "name_similar", value: "R. Vasquez", score: 0.87 },
  ],
  created_at: "2026-07-20T10:00:00Z",
  updated_at: "2026-07-20T10:00:00Z",
  decided_at: null,
  decided_by: null,
  left: { id: 11, kind: "person", display_name: "Rita Vasquez" },
  right: { id: 12, kind: "person", display_name: "R. Vasquez" },
};

const RITA = {
  id: 11,
  kind: "person",
  display_name: "Rita Vasquez",
  canonical_key: "rita vasquez",
  merged_into: null,
  source_ref: "contacts:rita",
  profile: {},
  created_at: "2026-07-01T10:00:00Z",
  updated_at: "2026-07-02T10:00:00Z",
  identifiers: [
    {
      id: 1,
      kind: "email",
      value: "rita@example.org",
      display_value: "Rita@example.org",
      source_ref: "contacts:rita",
    },
    {
      id: 2,
      kind: "name",
      value: "rita vasquez",
      display_value: "Rita Vasquez",
      source_ref: "",
    },
  ],
  emails: ["Rita@example.org"],
  phones: [],
  handles: [],
  names: ["Rita Vasquez"],
  contacts: [],
  merged_from: [{ id: 31, display_name: "Rita V." }],
  merges: [
    {
      id: 5,
      winner_id: 11,
      loser_id: 31,
      tier: "deterministic",
      reason: "same e-mail",
      evidence: [
        {
          tier: "deterministic",
          kind: "email_exact",
          value: "rita@example.org",
          score: 1,
        },
      ],
      queue_id: null,
      merged_at: "2026-07-02T10:00:00Z",
      undone_at: null,
    },
    {
      id: 4,
      winner_id: 11,
      loser_id: 41,
      tier: "probable",
      reason: "similar name",
      evidence: [],
      queue_id: 3,
      merged_at: "2026-07-01T12:00:00Z",
      undone_at: "2026-07-01T13:00:00Z",
    },
  ],
  pending_proposals: [PROPOSAL],
  requested_id: 11,
};

const EVENTS = [
  {
    id: 91,
    item_id: 501,
    kind: "meal",
    title: "Dinner with Rita",
    summary: "At the harbour place.",
    occurred_at: "2026-07-04T19:00:00Z",
    occurred_end: "2026-07-04T21:00:00Z",
    occurred_precision: "day",
    time_anchor: "stated",
    recorded_at: "2026-07-04T22:00:00Z",
    date_label: "4 July 2026",
    place: "Hamburg",
    place_entity_id: null,
    confidence: 0.9,
    source_id: "src1",
    permalink: "app://evidence/501",
    item_title: "Chat log",
    participants: [{ entity_id: 11, display_name: "Rita Vasquez" }],
  },
];

interface Call {
  url: string;
  method: string;
}

type Answer = { status?: number; body: unknown };
type Route = Answer | ((url: string) => Answer);

function makeClient(): QueryClient {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: 0 } },
  });
}

/**
 * Route by `METHOD /path`, longest key first — `/identity/people/11` and
 * `/identity/people` are otherwise the same prefix, and a shorter accidental
 * match would answer a profile request with the list.
 */
function installRoutes(overrides: Record<string, Route> = {}): Call[] {
  const routes: Record<string, Route> = {
    "GET /api/ultrawiki/identity/people": {
      body: {
        ok: true,
        people: PEOPLE,
        query: "",
        limit: 500,
        offset: 0,
        counts: COUNTS,
      },
    },
    "GET /api/ultrawiki/identity/queue": {
      body: {
        ok: true,
        proposals: [PROPOSAL],
        status: "pending",
        limit: 100,
        counts: COUNTS,
      },
    },
    "GET /api/ultrawiki/identity/people/11": {
      body: { ok: true, person: RITA, forwarded: false },
    },
    "GET /api/ultrawiki/events": {
      body: { events: EVENTS, total: EVENTS.length, limit: 25, offset: 0 },
    },
    ...overrides,
  };
  const calls: Call[] = [];
  const keys = Object.keys(routes).sort((a, b) => b.length - a.length);
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? "GET").toUpperCase();
      calls.push({ url, method });
      const key = keys.find((candidate) =>
        `${method} ${url}`.startsWith(candidate),
      );
      if (!key) throw new Error(`unrouted fetch: ${method} ${url}`);
      const route = routes[key];
      const answer = typeof route === "function" ? route(url) : route;
      const status = answer.status ?? 200;
      return {
        ok: status < 400,
        status,
        json: async () => answer.body,
      } as unknown as Response;
    }),
  );
  return calls;
}

function renderPanel(onOpenSources = () => {}) {
  return render(
    <QueryClientProvider client={makeClient()}>
      <PeoplePanel onOpenSources={onOpenSources} />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("the list", () => {
  it("lists the people the knowledge base has identified", async () => {
    installRoutes();
    renderPanel();

    expect(await screen.findByTestId("person-row-11")).toBeTruthy();
    expect(screen.getByTestId("person-row-21").textContent).toContain(
      "Tomas Bauer",
    );
  });

  it("filters as the user types, without asking the server", async () => {
    const calls = installRoutes();
    renderPanel();
    await screen.findByTestId("person-row-11");
    const before = calls.length;

    fireEvent.change(screen.getByTestId("people-search"), {
      target: { value: "tom" },
    });

    await waitFor(() => {
      expect(screen.queryByTestId("person-row-11")).toBeNull();
    });
    expect(screen.getByTestId("person-row-21")).toBeTruthy();
    expect(calls.length).toBe(before);
  });

  it("asks the server only when no name matches — that is where e-mails live", async () => {
    const calls = installRoutes({
      "GET /api/ultrawiki/identity/people?q=": (url) => ({
        body: {
          ok: true,
          people: url.includes("rita%40example.org") ? [PEOPLE[0]] : [],
          query: "rita@example.org",
          limit: 200,
          offset: 0,
          counts: COUNTS,
        },
      }),
    });
    renderPanel();
    await screen.findByTestId("person-row-11");

    fireEvent.change(screen.getByTestId("people-search"), {
      target: { value: "rita@example.org" },
    });

    expect(await screen.findByTestId("people-by-identifier")).toBeTruthy();
    expect(screen.getByTestId("person-row-11")).toBeTruthy();
    expect(
      calls.some((call) => call.url.includes("q=rita%40example.org")),
    ).toBe(true);
  });

  it("says so when nothing matches at all", async () => {
    installRoutes({
      "GET /api/ultrawiki/identity/people?q=": {
        body: {
          ok: true,
          people: [],
          query: "zzz",
          limit: 200,
          offset: 0,
          counts: COUNTS,
        },
      },
    });
    renderPanel();
    await screen.findByTestId("person-row-11");

    fireEvent.change(screen.getByTestId("people-search"), {
      target: { value: "zzz" },
    });

    expect(await screen.findByTestId("people-no-search-hits")).toBeTruthy();
  });

  it("reports a dead identity layer instead of an empty shell", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("network down");
      }),
    );
    renderPanel();

    expect(await screen.findByTestId("people-error")).toBeTruthy();
  });
});

describe("the decision queue", () => {
  it("opens on the queue, because that is what goes stale by being ignored", async () => {
    installRoutes();
    renderPanel();

    expect(await screen.findByTestId("people-queue-pane")).toBeTruthy();
    expect(screen.getByTestId("merge-proposal-7")).toBeTruthy();
  });

  it("counts the open questions on the way in", async () => {
    installRoutes();
    renderPanel();

    await waitFor(() => {
      expect(screen.getByTestId("people-open-queue").textContent).toContain("1");
    });
  });

  it("says so when the queue is empty", async () => {
    installRoutes({
      "GET /api/ultrawiki/identity/queue": {
        body: {
          ok: true,
          proposals: [],
          status: "pending",
          limit: 100,
          counts: { ...COUNTS, pending_confirmations: 0 },
        },
      },
    });
    renderPanel();

    expect(await screen.findByTestId("identity-queue-empty")).toBeTruthy();
  });
});

describe("one person", () => {
  it("shows the standing facts grouped by what they are", async () => {
    installRoutes();
    renderPanel();

    fireEvent.click(await screen.findByTestId("person-row-11"));

    const facts = await screen.findByTestId("person-facts");
    expect(facts.textContent).toContain("Rita@example.org");
    expect(screen.getByTestId("person-facts-email")).toBeTruthy();
    expect(screen.getByTestId("person-facts-name")).toBeTruthy();
    // Nothing is invented for a kind the person has none of.
    expect(screen.queryByTestId("person-facts-phone")).toBeNull();
  });

  it("shows what happened, dated the way the source could support it", async () => {
    installRoutes();
    renderPanel();

    fireEvent.click(await screen.findByTestId("person-row-11"));

    const event = await screen.findByTestId("person-event-91");
    expect(event.textContent).toContain("Dinner with Rita");
    expect(event.textContent).toContain("4 July 2026");
    expect(screen.getByTestId("person-event-link-91").getAttribute("href")).toBe(
      "app://evidence/501",
    );
  });

  it("keeps the profile readable when the event layer is dead", async () => {
    // Events are their own layer. A corpus that never ran event extraction —
    // or a store that fails this one query — must not take the identity half
    // of the page down with it.
    installRoutes({
      "GET /api/ultrawiki/events": { status: 500, body: { detail: "no events" } },
    });
    renderPanel();

    fireEvent.click(await screen.findByTestId("person-row-11"));

    expect(await screen.findByTestId("person-events-failed")).toBeTruthy();
    expect(screen.getByTestId("person-facts").textContent).toContain(
      "Rita@example.org",
    );
  });

  it("raises the open questions about that person on their own profile", async () => {
    installRoutes();
    renderPanel();

    fireEvent.click(await screen.findByTestId("person-row-11"));

    expect(await screen.findByTestId("merge-proposal-7")).toBeTruthy();
  });
});

describe("undoing a merge", () => {
  it("offers the undo only for a merge that still stands", async () => {
    installRoutes();
    renderPanel();

    fireEvent.click(await screen.findByTestId("person-row-11"));
    await screen.findByTestId("person-merges");

    expect(screen.getByTestId("person-merge-undo-5")).toBeTruthy();
    // Merge 4 was already undone — the record stays, the button does not.
    expect(screen.queryByTestId("person-merge-undo-4")).toBeNull();
    expect(screen.getByTestId("person-merge-undone-4")).toBeTruthy();
  });

  it("undoes through the audit id, not the entity id", async () => {
    const calls = installRoutes({
      "POST /api/ultrawiki/identity/merges/5/unmerge": {
        body: { ok: true, merge_id: 5, status: "undone" },
      },
    });
    renderPanel();

    fireEvent.click(await screen.findByTestId("person-row-11"));
    fireEvent.click(await screen.findByTestId("person-merge-undo-5"));

    await waitFor(() => {
      expect(
        calls.some(
          (call) =>
            call.method === "POST" &&
            call.url === "/api/ultrawiki/identity/merges/5/unmerge",
        ),
      ).toBe(true);
    });
  });

  it("repeats the store's reason when it refuses to undo", async () => {
    installRoutes({
      "POST /api/ultrawiki/identity/merges/5/unmerge": {
        status: 409,
        body: { detail: "undo merge 9 first" },
      },
    });
    renderPanel();

    fireEvent.click(await screen.findByTestId("person-row-11"));
    fireEvent.click(await screen.findByTestId("person-merge-undo-5"));

    const error = await screen.findByTestId("person-merge-error-5");
    expect(error.textContent).toContain("undo merge 9 first");
  });
});

describe("seeding from the address book", () => {
  it("reports what one pass actually changed", async () => {
    installRoutes({
      "POST /api/ultrawiki/identity/seed": {
        body: {
          ok: true,
          report: {
            created: 3,
            linked: 12,
            identifiers_added: 4,
            merged: 1,
            queued: 2,
            skipped: 0,
          },
          counts: COUNTS,
        },
      },
    });
    renderPanel();

    fireEvent.click(await screen.findByTestId("people-seed"));

    const line = await screen.findByTestId("people-seed-result");
    expect(line.textContent).toContain("3");
    expect(line.textContent).toContain("12");
  });

  it("does not hide a refused import", async () => {
    installRoutes({
      "POST /api/ultrawiki/identity/seed": {
        status: 409,
        body: { detail: "UltraWiki mode is off" },
      },
    });
    renderPanel();

    fireEvent.click(await screen.findByTestId("people-seed"));

    const error = await screen.findByTestId("people-seed-error");
    expect(error.textContent).toContain("UltraWiki mode is off");
  });
});
