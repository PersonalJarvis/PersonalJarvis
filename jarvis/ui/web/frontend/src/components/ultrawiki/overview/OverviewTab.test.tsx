/**
 * The overview, tested against the screen that caused it.
 *
 * The anchor is `SCREENSHOT`: the live corpus of 4 712 items where 3 237 were
 * queued for distillation, the strip said "Processing (3237 pending)" and the
 * checklist directly below said "Everything is processed. No backlog." Any
 * change that lets this screen claim it is finished should fail here.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { VerdictCard, verdictToneOf } from "@/components/ultrawiki/overview/VerdictCard";
import { bandsOf } from "@/components/ultrawiki/overview/IntakeBar";
import { SourceRoster, rowStateOf } from "@/components/ultrawiki/overview/SourceRoster";
import {
  ActivityFeed,
  activityEntriesOf,
} from "@/components/ultrawiki/overview/ActivityFeed";
import { ProblemList, problemsOf } from "@/components/ultrawiki/overview/ProblemList";
import type {
  UltraWikiHealthCheck,
  UltraWikiJob,
  UltraWikiPipeline,
  UltraWikiProgress,
  UltraWikiSource,
} from "@/lib/ultrawikiApi";

/** The exact live state from the 2026-07-26 screenshot. */
const SCREENSHOT: UltraWikiProgress = {
  state: "working",
  total: 4712,
  searchable: 4712,
  summarised: 1475,
  waiting: 3237,
  failed: 0,
  next_step: "summarising",
  waiting_by_bucket: { embedded: 3237 },
  buckets: { captured: 0, keyword_indexed: 0, embedded: 3237, distilled: 1475, failed: 0 },
  milestones: [
    { id: "stored", reached: 4712, share: 1 },
    { id: "searchable", reached: 4712, share: 1 },
    { id: "summarised", reached: 1475, share: 0.313 },
  ],
};

const PROCESSING: UltraWikiPipeline = {
  running: true,
  state: "processing",
  reason: "3237 item(s) are queued for processing.",
  processed: {},
};

function renderWithQuery(ui: JSX.Element) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("VerdictCard — the sentence that used to be wrong", () => {
  it("never says the corpus is finished while items are queued", () => {
    renderWithQuery(
      <VerdictCard progress={SCREENSHOT} pipeline={PROCESSING} usable />,
    );
    const card = screen.getByTestId("ultrawiki-verdict");
    expect(card.getAttribute("data-tone")).toBe("working");
    expect(card.textContent).not.toContain("Everything is processed");
    expect(screen.getByTestId("ultrawiki-verdict-detail").textContent).toContain(
      "3 237",
    );
  });

  it("names what the queue is waiting for, not the stage it finished", () => {
    renderWithQuery(
      <VerdictCard progress={SCREENSHOT} pipeline={PROCESSING} usable />,
    );
    const detail = screen.getByTestId("ultrawiki-verdict-detail").textContent;
    expect(detail).toContain("being summarised");
    expect(detail).not.toContain("embedded");
  });

  it("still calls a half-processed store usable, because it is", () => {
    // A backlog must not read as a fault: the processed part answers now.
    expect(verdictToneOf(SCREENSHOT, "processing", true)).toBe("working");
  });

  it("distinguishes a stalled queue from a busy one", () => {
    expect(verdictToneOf(SCREENSHOT, "paused", true)).toBe("stalled");
    renderWithQuery(
      <VerdictCard
        progress={SCREENSHOT}
        pipeline={{ ...PROCESSING, state: "paused", reason: "no key configured" }}
        usable
      />,
    );
    expect(screen.getByTestId("ultrawiki-verdict-reason").textContent).toContain(
      "no key configured",
    );
  });

  it("reports an empty store as empty, not as broken", () => {
    const empty: UltraWikiProgress = { ...SCREENSHOT, state: "empty", total: 0, searchable: 0, summarised: 0, waiting: 0, next_step: null };
    expect(verdictToneOf(empty, "idle", false)).toBe("empty");
  });
});

describe("IntakeBar — the corpus at true scale", () => {
  it("splits the corpus into disjoint bands that add back up to it", () => {
    const bands = bandsOf(SCREENSHOT);
    const sum = bands.reduce((acc, b) => acc + b.count, 0);
    expect(sum).toBe(SCREENSHOT.total);
  });

  it("draws the unfinished share as unfinished, not as done", () => {
    const bands = bandsOf(SCREENSHOT);
    const summarised = bands.find((b) => b.key === "summarised");
    const usable = bands.find((b) => b.key === "usable");
    expect(summarised?.count).toBe(1475);
    expect(usable?.count).toBe(3237);
    expect(usable?.working).toBe(true);
  });

  it("keeps failed items out of the finished bands", () => {
    const bands = bandsOf({
      ...SCREENSHOT,
      total: 100,
      searchable: 80,
      summarised: 70,
      waiting: 10,
      failed: 20,
    });
    expect(bands.find((b) => b.key === "failed")?.count).toBe(20);
    expect(bands.reduce((a, b) => a + b.count, 0)).toBe(100);
  });
});

describe("SourceRoster — did this source actually deliver anything?", () => {
  const base: UltraWikiSource = {
    id: "s1",
    connector: "obsidian-vault",
    label: "Built-in Wiki",
    consent: "approved",
    enabled: true,
    areas: [],
    counts: { total: 60 },
    sync_state: null,
    last_sync_at: "2026-07-26T08:00:00Z",
    last_error: null,
  };

  it("calls out an approved source that has never been read", () => {
    expect(
      rowStateOf({ ...base, last_sync_at: null, last_outcome: null }),
    ).toBe("never");
  });

  it("calls out a source that ran and delivered nothing", () => {
    // The 2026-07-25 forensic in one row: success, zero items.
    expect(rowStateOf({ ...base, counts: { total: 0 } })).toBe("empty");
  });

  it("shows the item count each source contributed", () => {
    renderWithQuery(
      <SourceRoster sources={[base]} onChanged={() => {}} onOpenSources={() => {}} />,
    );
    const row = screen.getByTestId("ultrawiki-roster-row-s1");
    expect(within(row).getByText("60")).toBeDefined();
  });

  it("invites the first source instead of showing an empty box", () => {
    renderWithQuery(
      <SourceRoster sources={[]} onChanged={() => {}} onOpenSources={() => {}} />,
    );
    expect(screen.getByTestId("ultrawiki-roster-empty-action")).toBeDefined();
  });
});

describe("ActivityFeed — what happened", () => {
  const job: UltraWikiJob = {
    job_id: "j1",
    source_id: "s1",
    mode: "incremental",
    status: "done",
    started_at: 1_800_000_000,
    ended_at: 1_800_000_060,
    chunks: 1,
    new: 12,
    changed: 3,
    unchanged: 4511,
    tombstoned: 0,
    error: "",
  };
  const source: UltraWikiSource = {
    id: "s1",
    connector: "x",
    label: "Jarvis Conversations",
    consent: "approved",
    enabled: true,
    areas: [],
    counts: { total: 4530 },
    sync_state: null,
    last_sync_at: "2026-07-26T08:00:00Z",
    last_error: null,
    last_outcome: {
      finished_at: "2026-07-26T08:00:00Z",
      status: "done",
      mode: "incremental",
      new: 1,
      changed: 0,
      unchanged: 0,
      tombstoned: 0,
    },
  };

  it("reports one import once, even though two records describe it", () => {
    const entries = activityEntriesOf([job], [source]);
    expect(entries).toHaveLength(1);
    expect(entries[0].fromMemory).toBe(false);
  });

  it("still lists a source whose last run predates the app restart", () => {
    const entries = activityEntriesOf([], [source]);
    expect(entries).toHaveLength(1);
    expect(entries[0].fromMemory).toBe(true);
  });

  it("says out loud that the persisted part is not a full log", () => {
    renderWithQuery(<ActivityFeed jobs={[]} sources={[source]} />);
    expect(screen.getByTestId("ultrawiki-activity-note")).toBeDefined();
  });

  it("shows what an import actually brought in", () => {
    renderWithQuery(<ActivityFeed jobs={[job]} sources={[source]} />);
    const row = screen.getByTestId("ultrawiki-activity-row-s1");
    expect(row.textContent).toContain("+12");
    expect(row.textContent).toContain("4 511");
  });
});

describe("ProblemList — only what is not fine", () => {
  const check = (
    id: string,
    state: UltraWikiHealthCheck["state"],
  ): UltraWikiHealthCheck => ({
    id,
    title: `${id} title`,
    state,
    detail: `${id} detail`,
    action: state === "attention" ? { kind: "sync_all" } : null,
    facts: {},
  });

  it("hides the green rows and keeps the ones needing a decision", () => {
    const problems = problemsOf([
      check("mode", "ok"),
      check("sources", "attention"),
      check("integrations", "blocked"),
      check("processing", "working"),
    ]);
    expect(problems.map((p) => p.id)).toEqual(["sources", "integrations"]);
  });

  it("does not dress a draining backlog up as a problem", () => {
    // "working" is progress. Listing it here is how a list stops being read.
    expect(problemsOf([check("processing", "working")])).toHaveLength(0);
  });

  it("collapses an all-clear to a single line", () => {
    renderWithQuery(
      <ProblemList
        checks={[check("mode", "ok")]}
        handlers={{ onOpenSources: () => {}, onOpenSettings: () => {}, onChanged: () => {} }}
      />,
    );
    expect(
      screen.getByTestId("ultrawiki-problems").getAttribute("data-count"),
    ).toBe("0");
  });
});
