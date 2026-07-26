/**
 * ImportProgress tests — the honest pipeline-state headline.
 *
 * The maintainer's report: on a fresh activation with zero approved sources
 * the strip read "Pipeline running · Captured 0 · …", which sounds like data
 * is already being pulled although nothing was connected. These pin that each
 * of the four backend states renders its own honest wording, that the blanket
 * "Pipeline running" is gone, and that the waiting state offers the way out.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { ImportProgress } from "@/components/ultrawiki/ImportProgress";
import type { UltraWikiCounts, UltraWikiPipeline } from "@/lib/ultrawikiApi";

const NO_COUNTS: Partial<UltraWikiCounts> = {
  captured: 0,
  keyword_indexed: 0,
  embedded: 0,
  distilled: 0,
  failed: 0,
  total: 0,
};

function renderStrip(
  pipeline: UltraWikiPipeline,
  counts: Partial<UltraWikiCounts> = NO_COUNTS,
  onOpenSources?: () => void,
) {
  return render(
    <ImportProgress
      counts={counts}
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
      NO_COUNTS,
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

  it("counts the real backlog while processing", () => {
    renderStrip(
      {
        running: true,
        state: "processing",
        reason: "6 item(s) are queued for processing.",
        processed: { keyword: 2 },
      },
      {
        captured: 1,
        keyword_indexed: 2,
        embedded: 3,
        distilled: 40, // finished work is NOT pending
        failed: 5, // gave up — also not pending
        total: 51,
      },
    );

    const state = screen.getByTestId("ultrawiki-pipeline-state");
    expect(state.getAttribute("data-state")).toBe("processing");
    expect(state.textContent).toContain("Processing (6 pending)");
    expect(screen.getByTestId("ultrawiki-pipeline-running")).toBeDefined();
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
      { captured: 0, keyword_indexed: 12, embedded: 0, distilled: 0, failed: 0 },
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
      { captured: 0, keyword_indexed: 0, embedded: 0, distilled: 9, failed: 0 },
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
});

describe("ImportProgress — retry-failed button", () => {
  const IDLE: UltraWikiPipeline = { running: true, state: "processing", processed: {} };

  it("is absent while nothing has failed", () => {
    renderStrip(IDLE, { ...NO_COUNTS, failed: 0 });
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
        counts={{ ...NO_COUNTS, failed: 32 }}
        pipeline={IDLE}
        jobs={[]}
        onChanged={onChanged}
      />,
    );

    const button = screen.getByTestId("ultrawiki-retry-failed");
    fireEvent.click(button);
    await vi.waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
    expect(
      calls.some((u) => u.includes("/api/ultrawiki/pipeline/requeue-failed")),
    ).toBe(true);
  });
});
