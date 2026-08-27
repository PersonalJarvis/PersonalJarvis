/**
 * The output page — a run without a page composed the way an artifact is
 * shown. Pins: the headline comes from the report's own title when there is
 * one, the request is the lead, the final answer is the answer (the run's
 * "Done." summary is not repeated), every file is drawn in place by kind,
 * and "Open in Files" hands the file's path back.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { OutputPreview, markdownTitle } from "@/components/visualization/OutputPreview";
import type { ArtifactSummary, OutputSummary } from "@/hooks/useOutputs";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function installFetchMock(
  files: ArtifactSummary[],
  rawByPath: Record<string, string>,
  finalAnswer: string | null,
) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const raw = /\/files\/(.+)\/raw$/.exec(url);
    if (raw) {
      const path = decodeURIComponent(raw[1]);
      const text = rawByPath[path];
      if (text === undefined) return { ok: false, status: 404, json: async () => ({}) };
      return {
        ok: true,
        status: 200,
        json: async () => ({ path, size: text.length, text, truncated: false }),
      };
    }
    if (url.endsWith("/artifacts")) {
      return { ok: true, status: 200, json: async () => ({ files }) };
    }
    if (url.endsWith("/plan")) {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          plan: finalAnswer ? { plan_id: "p", vision: "", status: "complete" } : null,
          steps: [],
          final_answer: finalAnswer,
        }),
      };
    }
    return { ok: false, status: 404, json: async () => ({}) };
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderPreview(run: OutputSummary, onOpenFile = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <OutputPreview run={run} onOpenFile={onOpenFile} />
    </QueryClientProvider>,
  );
  return onOpenFile;
}

function file(path: string, over: Partial<ArtifactSummary> = {}): ArtifactSummary {
  return { path, size: 120, mtime: 1_750_000_000, is_text: true, preview: null, ...over };
}

const RUN: OutputSummary = {
  slug: "run-1",
  utterance:
    "Do a deep dive on Reddit.\n\nSupporting context from the recent conversation " +
    "(use only to resolve references; the underlying request remains authoritative):\n- x",
  status: "success",
  summary: "Done. File reddit.md is in the folder Jarvis-Outputs.",
  duration_s: 153.2,
  completed_at: 1_755_000_000,
};

describe("markdownTitle", () => {
  it("reads the first heading and ignores inline marks", () => {
    expect(markdownTitle("intro\n\n# Reddit **Open Source** AI\n\ntext")).toBe(
      "Reddit Open Source AI",
    );
    expect(markdownTitle("no heading here")).toBeNull();
    expect(markdownTitle("")).toBeNull();
  });
});

describe("OutputPreview", () => {
  it("composes the page: report title, clean request, final answer, files in place", async () => {
    installFetchMock(
      [
        file("tasks/t/artifacts/files/reddit.md"),
        file("tasks/t/artifacts/files/scripts/tool.py"),
        file("tasks/t/artifacts/files/data.csv"),
      ],
      {
        "tasks/t/artifacts/files/reddit.md": "# Reddit Open Source AI Landscape\n\nA **report**.",
        "tasks/t/artifacts/files/scripts/tool.py": "print('hi')\n",
        "tasks/t/artifacts/files/data.csv": "a,b\n1,2\n",
      },
      "Done — the report is in reddit.md.",
    );
    const onOpenFile = renderPreview(RUN);

    const page = await screen.findByTestId("output-preview");
    await waitFor(() =>
      expect(within(page).getByTestId("output-preview-title").textContent).toBe(
        "Reddit Open Source AI Landscape",
      ),
    );
    // The request, without the builder's supporting-context tail.
    expect(within(page).getByTestId("output-preview-request").textContent).toBe(
      "Do a deep dive on Reddit.",
    );
    // Eyebrow facts: kind, when, duration, status word.
    expect(within(page).getByTestId("output-preview-status").textContent).toContain("Succeeded");
    expect(page.textContent).toContain("2 min 33 s");
    // The answer is the worker's final reply; the "Done." summary is not repeated.
    const answer = await within(page).findByTestId("output-preview-answer");
    expect(answer.textContent).toContain("Done — the report is in reddit.md.");
    expect(answer.textContent).not.toContain("Done. File reddit.md");
    // Every file drawn in place, by kind.
    await waitFor(() =>
      expect(within(page).getAllByTestId("output-preview-file")).toHaveLength(3),
    );
    const sections = within(page).getAllByTestId("output-preview-file");
    expect(sections.map((s) => s.getAttribute("data-kind"))).toEqual(["markdown", "csv", "code"]);
    await within(sections[0]).findByTestId("output-preview-markdown");
    expect(within(sections[0]).getByText("report").tagName).toBe("STRONG");
    await waitFor(() => expect(within(sections[1]).getByText("b")).toBeDefined());
    await waitFor(() => expect(sections[2].textContent).toContain("print"));
    // "Open in Files" hands the archive path back.
    fireEvent.click(within(sections[2]).getByTitle("Open in Files"));
    expect(onOpenFile).toHaveBeenCalledWith("tasks/t/artifacts/files/scripts/tool.py");
  });

  it("draws a document's front matter as a metadata block, not as prose", async () => {
    installFetchMock(
      [file("tasks/t/artifacts/files/SKILL.md")],
      {
        "tasks/t/artifacts/files/SKILL.md":
          '---\nname: gmail-deep-clean\ndescription: "Cleans the inbox"\n---\n# Gmail Deep Clean\n\nBody.',
      },
      null,
    );
    renderPreview({ slug: "run-skill", utterance: "Write a skill", status: "success" });
    const page = await screen.findByTestId("output-preview");
    await waitFor(() =>
      expect(within(page).getByTestId("output-preview-title").textContent).toBe(
        "Gmail Deep Clean",
      ),
    );
    const doc = await within(page).findByTestId("output-preview-markdown");
    const meta = within(doc).getByTestId("markdown-front-matter");
    expect(meta.textContent).toContain("name: gmail-deep-clean");
    // The body no longer starts with the run-on metadata paragraph.
    expect(within(doc).getByRole("heading", { level: 1 }).textContent).toBe("Gmail Deep Clean");
    expect(within(doc).queryByText(/^name: gmail-deep-clean/)).toBeNull();
  });

  it("falls back to the request as headline and the summary as answer", async () => {
    installFetchMock([], {}, null);
    renderPreview({
      slug: "run-2",
      utterance: "What is the capital of Australia?",
      status: "success",
      summary: "Canberra — not Sydney.",
    });
    const page = await screen.findByTestId("output-preview");
    expect(within(page).getByTestId("output-preview-title").textContent).toBe(
      "What is the capital of Australia?",
    );
    // The headline IS the request: it is not repeated as a lead.
    expect(within(page).queryByTestId("output-preview-request")).toBeNull();
    const answer = await within(page).findByTestId("output-preview-answer");
    expect(answer.textContent).toContain("Canberra — not Sydney.");
    expect(within(page).queryByTestId("output-preview-files")).toBeNull();
  });

  it("leads with why a run ended early, and says when nothing is left behind", async () => {
    installFetchMock([], {}, null);
    renderPreview({
      slug: "run-3",
      utterance: "Build it",
      status: "cancelled",
      terminal_reason: "ui_cancel",
    });
    const page = await screen.findByTestId("output-preview");
    const outcome = await within(page).findByTestId("output-preview-outcome");
    expect(outcome.textContent).toContain("ui_cancel");
    expect(within(page).getByTestId("output-preview-status").textContent).toContain("Cancelled");
    expect(within(page).queryByTestId("output-preview-nothing")).toBeNull();
  });

  it("says so when a finished run left neither an answer nor a file", async () => {
    installFetchMock([], {}, null);
    renderPreview({ slug: "run-4", utterance: "Nothing", status: "success" });
    const page = await screen.findByTestId("output-preview");
    await within(page).findByTestId("output-preview-nothing");
  });
});
