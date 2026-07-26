/**
 * ImportProgress tests — the honest pipeline-state headline.
 *
 * The maintainer's report: on a fresh activation with zero approved sources
 * the strip read "Pipeline running · Captured 0 · …", which sounds like data
 * is already being pulled although nothing was connected. These pin that each
 * of the four backend states renders its own honest wording, that the blanket
 * "Pipeline running" is gone, and that the waiting state offers the way out.
 *
 * The strip now quotes the shared progress model instead of printing the five
 * raw buckets, so these tests hand it that model — exactly as the backend
 * sends it — rather than counts the component would have to add up itself.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { ImportProgress } from "@/components/ultrawiki/ImportProgress";
import type { UltraWikiPipeline, UltraWikiProgress } from "@/lib/ultrawikiApi";

/** A progress payload shaped like jarvis/ultrawiki/progress.py's output. */
function progressOf(
  partial: Partial<UltraWikiProgress> = {},
): UltraWikiProgress {
  return {
    state: "empty",
    total: 0,
    searchable: 0,
    summarised: 0,
    waiting: 0,
    failed: 0,
    next_step: null,
    waiting_by_bucket: {},
    buckets: {},
    milestones: [],
    ...partial,
  };
}

const EMPTY = progressOf();

function renderStrip(
  pipeline: UltraWikiPipeline,
  progress: UltraWikiProgress | null = EMPTY,
  onOpenSources?: () => void,
) {
  return render(
    <ImportProgress
      progress={progress}
      pipeline={pipeline}
      jobs={[]}
      onChanged={() => {}}
      onOpenSources={onOpenSources}
    />,
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("ImportProgress — honest pipeline state", () => {
  it("says it is waiting for sources instead of 'running' on a fresh activation", () => {
    const onOpenSources = vi.fn();
    renderStrip(
      {
        running: true, // the worker loop IS alive — that is why it used to lie
        state: "waiting_for_sources",
        reason:
          "No source is approved yet, so nothing is being read. Approve a source under Sources.",
        processed: {},
      },
      EMPTY,
      onOpenSources,
    );

    const state = screen.getByTestId("ultrawiki-pipeline-state");
    expect(state.getAttribute("data-state")).toBe("waiting_for_sources");
    expect(state.textContent).toContain("Waiting for approved sources");
    expect(state.textContent).not.toContain("Pipeline running");
    expect(screen.queryByTestId("ultrawiki-pipeline-running")).toBeNull();

    // The honest reason is shown verbatim, and the way out is one click.
    expect(screen.getByTestId("ultrawiki-pipeline-reason").textContent).toContain(
      "No source is approved yet",
    );
    fireEvent.click(screen.getByTestId("ultrawiki-open-sources-link"));
    expect(onOpenSources).toHaveBeenCalledTimes(1);
  });

  it("quotes the backlog the backend computed, and never re-adds buckets", () => {
    renderStrip(
      {
        running: true,
        state: "processing",
        reason: "6 item(s) are queued for processing.",
        processed: { keyword: 2 },
      },
      progressOf({
        state: "working",
        total: 51,
        searchable: 45,
        summarised: 40,
        waiting: 6,
        failed: 5,
        next_step: "summarising",
      }),
    );

    const state = screen.getByTestId("ultrawiki-pipeline-state");
    expect(state.getAttribute("data-state")).toBe("processing");
    expect(screen.getByTestId("ultrawiki-pipeline-running")).toBeDefined();
    // The backlog is stated ONCE, in the summary. The state label used to
    // carry it too, next to a backend reason that carried it a third time.
    expect(state.textContent).not.toContain("6");

    // The summary reports the corpus in cumulative terms — the reading that
    // "Keyword-searchable 0 · Embedded 3237" got wrong.
    const summary = screen.getByTestId("ultrawiki-progress-summary").textContent;
    expect(summary).toContain("6");
    expect(summary).toContain("51");
    expect(summary).toContain("45");
    expect(summary).toContain("being summarised");
  });

  it("shows the blocking reason when a slot pauses the pipeline", () => {
    renderStrip(
      {
        running: true,
        state: "paused",
        reason:
          "12 item(s) are keyword-searchable and waiting for the embedding stage: no Gemini API key is configured",
        processed: {},
      },
      progressOf({
        state: "working",
        total: 12,
        searchable: 12,
        waiting: 12,
        next_step: "embedding",
      }),
    );

    const state = screen.getByTestId("ultrawiki-pipeline-state");
    expect(state.getAttribute("data-state")).toBe("paused");
    expect(state.textContent).toContain("Paused");
    expect(screen.getByTestId("ultrawiki-pipeline-reason").textContent).toContain(
      "no Gemini API key is configured",
    );
  });

  it("reports idle when there is nothing left to do", () => {
    renderStrip(
      {
        running: true,
        state: "idle",
        reason: "Everything ingested so far is fully processed.",
        processed: { keyword: 9 },
      },
      progressOf({ state: "done", total: 9, searchable: 9, summarised: 9 }),
    );

    const state = screen.getByTestId("ultrawiki-pipeline-state");
    expect(state.getAttribute("data-state")).toBe("idle");
    expect(state.textContent).toContain("Idle");
    expect(screen.queryByTestId("ultrawiki-pipeline-running")).toBeNull();
  });

  it("falls back to the running flag when the backend sends no state", () => {
    // An older backend (or a status from before the field existed) must still
    // render something sane rather than crash the strip.
    renderStrip({ running: false, processed: {} });
    expect(
      screen.getByTestId("ultrawiki-pipeline-state").getAttribute("data-state"),
    ).toBe("idle");
  });

  it("renders no counts at all when the backend has not answered yet", () => {
    // Zeros the user could mistake for measurements are worse than silence.
    renderStrip({ running: false, state: "idle", processed: {} }, null);
    expect(screen.queryByTestId("ultrawiki-progress-summary")).toBeNull();
  });
});

describe("ImportProgress — retry-failed button", () => {
  const IDLE: UltraWikiPipeline = { running: true, state: "processing", processed: {} };

  it("is absent while nothing has failed", () => {
    renderStrip(IDLE, progressOf({ failed: 0 }));
    expect(screen.queryByTestId("ultrawiki-retry-failed")).toBeNull();
  });

  it("appears with failed items and posts the requeue, then refreshes", async () => {
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: RequestInfo | URL) => {
        calls.push(String(url));
        return new Response(
          JSON.stringify({ ok: true, requeued: 32, source_id: "", detail: "" }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }),
    );
    const onChanged = vi.fn();
    render(
      <ImportProgress
        progress={progressOf({ total: 32, failed: 32 })}
        pipeline={IDLE}
        jobs={[]}
        onChanged={onChanged}
      />,
    );

    const button = screen.getByTestId("ultrawiki-retry-failed");
    expect(button.textContent).toContain("32");
    fireEvent.click(button);
    await vi.waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
    expect(
      calls.some((u) => u.includes("/api/ultrawiki/pipeline/requeue-failed")),
    ).toBe(true);
  });
});
