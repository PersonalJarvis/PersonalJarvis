/**
 * The output page draws what a run made, not its source: an HTML deliverable
 * runs in its sandbox, a script is a card that says what it does with the
 * source folded away, a patch lists the files it touches.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { OutputPreview } from "@/components/visualization/OutputPreview";
import type { ArtifactSummary, OutputSummary } from "@/hooks/useOutputs";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function installFetchMock(files: ArtifactSummary[], rawByPath: Record<string, string>) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
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
    if (url.endsWith("/page") && init?.method === "HEAD") {
      return { ok: true, status: 200, json: async () => ({}) };
    }
    if (url.endsWith("/artifacts")) {
      return { ok: true, status: 200, json: async () => ({ files }) };
    }
    if (url.endsWith("/plan")) {
      return {
        ok: true,
        status: 200,
        json: async () => ({ plan: null, steps: [], final_answer: null }),
      };
    }
    return { ok: false, status: 404, json: async () => ({}) };
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderPreview(run: OutputSummary) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <OutputPreview run={run} onOpenFile={vi.fn()} />
    </QueryClientProvider>,
  );
}

function file(path: string, over: Partial<ArtifactSummary> = {}): ArtifactSummary {
  return { path, size: 120, mtime: 1_750_000_000, is_text: true, preview: null, ...over };
}

const RUN: OutputSummary = {
  slug: "run-2",
  utterance: "Build me the thing.",
  status: "success",
  summary: "Done.",
  duration_s: 12,
  completed_at: 1_755_000_000,
};

const LONG_SCRIPT = [
  "#!/usr/bin/env python3",
  '"""Gmail Deep Clean CLI utility.',
  "",
  "Scans the inbox and proposes deletions.",
  '"""',
  "import sys",
  "",
  "class Cleaner:",
  "    pass",
  "",
  "def main() -> int:",
  "    return 0",
  ...Array.from({ length: 30 }, (_, i) => `x${i} = ${i}`),
  "",
].join("\n");

describe("OutputPreview cards", () => {
  it("runs an HTML deliverable in its sandbox on the page instead of pointing at Files", async () => {
    installFetchMock([file("dashboard.html")], {});
    renderPreview(RUN);
    const frame = (await screen.findByTestId("artifact-html-page")) as HTMLIFrameElement;
    expect(frame.getAttribute("sandbox")).toBe("allow-scripts");
    expect(frame.getAttribute("src")).toMatch(/\/files\/dashboard\.html\/page\?theme=(light|dark)$/);
    expect(screen.queryByText(/not drawn on the page/)).toBeNull();
    expect(screen.getByText(/Runs in a sandbox/)).toBeDefined();
  });

  it("draws a long script as a card — description, definitions, source folded", async () => {
    installFetchMock([file("scripts/clean.py", { size: LONG_SCRIPT.length })], {
      "scripts/clean.py": LONG_SCRIPT,
    });
    renderPreview(RUN);
    const card = await screen.findByTestId("output-preview-code-card");
    expect(card.getAttribute("data-open")).toBe("false");
    expect(within(card).getByTestId("output-preview-code-description").textContent).toBe(
      "Gmail Deep Clean CLI utility.",
    );
    const facts = within(card).getByTestId("output-preview-code-facts");
    expect(facts.textContent).toContain("python");
    expect(facts.textContent).toContain("42 lines");
    expect(facts.textContent).toContain("Cleaner");
    expect(facts.textContent).toContain("main()");
    expect(within(card).queryByTestId("output-preview-code-source")).toBeNull();

    fireEvent.click(within(card).getByRole("button", { name: "Show source" }));
    await waitFor(() =>
      expect(within(card).getByTestId("output-preview-code-source").textContent).toContain(
        "def main()",
      ),
    );
    expect(card.getAttribute("data-open")).toBe("true");
    expect(within(card).getByRole("button", { name: "Hide source" })).toBeDefined();
  });

  it("shows a short file whole", async () => {
    installFetchMock([file("run.sh")], { "run.sh": "#!/bin/sh\n# Start it.\nnpm start\n" });
    renderPreview(RUN);
    const card = await screen.findByTestId("output-preview-code-card");
    expect(card.getAttribute("data-open")).toBe("true");
    expect(within(card).getByTestId("output-preview-code-source").textContent).toContain(
      "npm start",
    );
    expect(within(card).getByTestId("output-preview-code-facts").textContent).toContain(
      "3 lines",
    );
  });

  it("lists the files a patch touches with their added and removed lines", async () => {
    const patch = [
      "Subject: [PATCH] fix(rail): keep the newest run on top",
      "",
      "diff --git a/src/rail.ts b/src/rail.ts",
      "--- a/src/rail.ts",
      "+++ b/src/rail.ts",
      "@@ -1,2 +1,3 @@",
      "-old",
      "+new",
      "+newer",
      "",
    ].join("\n");
    installFetchMock([file("0001-fix.patch")], { "0001-fix.patch": patch });
    renderPreview(RUN);
    const card = await screen.findByTestId("output-preview-code-card");
    expect(within(card).getByTestId("output-preview-code-description").textContent).toBe(
      "fix(rail): keep the newest run on top",
    );
    const facts = within(card).getByTestId("output-preview-code-facts");
    expect(facts.textContent).toContain("1 file changed");
    const rows = within(card).getAllByRole("listitem");
    expect(rows).toHaveLength(1);
    expect(rows[0].textContent).toContain("src/rail.ts");
    expect(rows[0].textContent).toContain("+2");
    expect(rows[0].textContent).toContain("−1");
  });

  it("describes a JSON deliverable by its shape", async () => {
    installFetchMock([file("result.json")], {
      "result.json": JSON.stringify({ alpha: 1, beta: [2], gamma: null }, null, 2),
    });
    renderPreview(RUN);
    const card = await screen.findByTestId("output-preview-code-card");
    const facts = within(card).getByTestId("output-preview-code-facts");
    expect(facts.textContent).toContain("Object · 3 keys");
    expect(facts.textContent).toContain("alpha");
    expect(facts.textContent).toContain("gamma");
  });
});
