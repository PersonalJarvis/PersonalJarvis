/**
 * The overview, tested against the screen that caused it.
 *
 * The anchor is `SCREENSHOT`: the live corpus of 4 712 items where 3 237 were
 * queued for distillation, the strip said "Processing (3237 pending)" and the
 * checklist directly below said "Everything is processed. No backlog." Any
 * change that lets this screen claim it is finished should fail here.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
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
  UltraWikiThroughput,
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

/**
 * The 2026-07-27 state: a corpus fifty times bigger, moving at 0.65 items a
 * second, under a headline that said "you do not have to wait".
 */
const HUGE_BACKLOG: UltraWikiProgress = {
  state: "working",
  total: 236_131,
  searchable: 137_625,
  summarised: 216,
  waiting: 235_915,
  failed: 0,
  next_step: "embedding",
  waiting_by_bucket: { captured: 98_506, keyword_indexed: 133_657, embedded: 3_752 },
  buckets: {
    captured: 98_506,
    keyword_indexed: 133_657,
    embedded: 3_752,
    distilled: 216,
    failed: 0,
  },
  milestones: [
    { id: "stored", reached: 236_131, share: 1 },
    { id: "searchable", reached: 137_625, share: 0.583 },
    { id: "summarised", reached: 216, share: 0.0009 },
  ],
};

/** The measured lane behind that backlog: ~2 340 items an hour, ~4 days left. */
const MEASURED: UltraWikiThroughput = {
  embed: {
    rate_per_hour: 2340,
    backlog: 232_163,
    eta_seconds: 232_163 / 0.65,
    measured_s: 900,
    measured_items: 585,
    stalled: false,
    paused_reason: "",
  },
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

  it("does not call a closed store empty", () => {
    // Found live: while the app boots, /status answers with zeroed counts, and
    // the screen announced "Nothing stored yet." over a store holding 4 712
    // items. A store that is not open cannot report its contents.
    const zeroed: UltraWikiProgress = { ...SCREENSHOT, state: "empty", total: 0, searchable: 0, summarised: 0, waiting: 0, next_step: null };
    expect(verdictToneOf(zeroed, "paused", false, false)).toBe("starting");

    renderWithQuery(
      <VerdictCard
        progress={zeroed}
        pipeline={{ running: false, state: "paused", processed: {} }}
        usable={false}
        started={false}
      />,
    );
    const card = screen.getByTestId("ultrawiki-verdict");
    expect(card.getAttribute("data-tone")).toBe("starting");
    expect(card.textContent).not.toContain("Nothing stored yet");
    // And no bar, because there is nothing measured to draw.
    expect(screen.queryByTestId("ultrawiki-intake-bar")).toBeNull();
  });
});

describe("VerdictCard — how long the backlog takes", () => {
  it("states the measured duration instead of dismissing the wait", () => {
    // The defect verbatim: 235 915 queued items described as something you
    // do not have to wait for, over a queue measuring four days.
    renderWithQuery(
      <VerdictCard
        progress={HUGE_BACKLOG}
        pipeline={PROCESSING}
        usable
        throughput={MEASURED}
      />,
    );
    const detail = screen.getByTestId("ultrawiki-verdict-detail").textContent ?? "";
    expect(detail).toContain("days");
    expect(detail.toLowerCase()).not.toContain("you do not have to wait");
  });

  it("says it is measuring rather than inventing a number", () => {
    renderWithQuery(
      <VerdictCard progress={HUGE_BACKLOG} pipeline={PROCESSING} usable />,
    );
    const detail = screen.getByTestId("ultrawiki-verdict-detail").textContent ?? "";
    expect(detail).toContain("still being measured");
    expect(detail).not.toContain("days");
  });

  it("reports a standing queue as standing, with no completion time", () => {
    // "Never at this rate" is not a duration; rendering one implies progress.
    renderWithQuery(
      <VerdictCard
        progress={HUGE_BACKLOG}
        pipeline={PROCESSING}
        usable
        throughput={{
          embed: {
            rate_per_hour: 0,
            backlog: 232_163,
            eta_seconds: null,
            measured_s: 900,
            measured_items: 0,
            stalled: true,
            paused_reason: "",
          },
        }}
      />,
    );
    const detail = screen.getByTestId("ultrawiki-verdict-detail").textContent ?? "";
    expect(detail).toContain("standing still");
    expect(detail).not.toContain("days");
  });

  it("explains a summary lane that is parked on purpose", () => {
    // 216 summaries frozen for hours with no reason anywhere on screen.
    renderWithQuery(
      <VerdictCard
        progress={HUGE_BACKLOG}
        pipeline={PROCESSING}
        usable
        throughput={{
          ...MEASURED,
          distill: {
            rate_per_hour: null,
            backlog: 235_915,
            eta_seconds: null,
            measured_s: 900,
            measured_items: 0,
            stalled: false,
            paused_reason: "summaries are paused while the search index is rebuilt",
          },
        }}
      />,
    );
    expect(
      screen.getByTestId("ultrawiki-verdict-summary-pause").textContent,
    ).toContain("rebuilt");
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

  it("offers the retry the failed-items row promises", async () => {
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: RequestInfo | URL) => {
        calls.push(String(url));
        return new Response(
          JSON.stringify({ ok: true, requeued: 140, source_id: "", detail: "" }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }),
    );
    const onChanged = vi.fn();
    const failed: UltraWikiHealthCheck = {
      id: "processing",
      title: "140 item(s) could not be processed",
      state: "attention",
      detail: "Retrying usually clears them.",
      action: { kind: "retry_failed" },
      facts: {},
    };
    renderWithQuery(
      <ProblemList
        checks={[failed]}
        handlers={{ onOpenSources: () => {}, onOpenSettings: () => {}, onChanged }}
      />,
    );

    fireEvent.click(screen.getByTestId("ultrawiki-problem-action-processing"));
    await vi.waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
    expect(
      calls.some((u) => u.includes("/api/ultrawiki/pipeline/requeue-failed")),
    ).toBe(true);
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
