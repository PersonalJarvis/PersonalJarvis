/**
 * The Artifacts section as the home of EVERY run — what the Outputs section
 * used to guarantee, now pinned here (the Outputs section folded into
 * Artifacts on 2026-08-23).
 *
 * Contracts:
 * - a run without a page or picture shows as a run row; its stage opens on
 *   Preview as a composed output page (the answer, the reason it ended, its
 *   files in place), with Files one tab away carrying the notes above the
 *   reader; the empty state appears only when there are no runs at all,
 * - the rail narrows to artifacts or outputs and back, remembering the pick,
 * - Continue / Restart gating per status, the needs-review card, the live
 *   continuation chip that jumps to the child, hold-to-abort while running,
 * - Files: clean paths with primary files first, no direct download, the
 *   opener model (Open / Change how this opens / Reveal), the Browser pick
 *   going through the open-external bridge, an HTML file rendered as a page
 *   with its source behind the toggle,
 * - every rail row can be dragged onto the dock with the mission payload,
 * - the retired "outputs" id still resolves to this section.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { VisualizationView } from "@/views/VisualizationView";
import { MISSION_DND_MIME } from "@/lib/missionDnd";
import { initialSectionFromSearch, resolveSectionId } from "@/store/events";
import type { ArtifactSummary, OutputSummary } from "@/hooks/useOutputs";

// ViewHeader pulls in ChatsView, which subscribes to a WS client; null keeps
// that effect a deterministic no-op in jsdom.
vi.mock("@/hooks/useWebSocket", () => ({
  getWSClient: () => null,
}));

// The "Browser" opener must reach the user's real browser via the open-external
// bridge (WebView2 drops a bare window.open) — spy on it, not on window.open.
vi.mock("@/lib/openExternal", () => ({
  openExternalUrl: vi.fn(async () => {}),
}));
import { openExternalUrl } from "@/lib/openExternal";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

function installFetchMock(
  runs: OutputSummary[],
  artifactsBySlug: Record<string, ArtifactSummary[]> = {},
  options: {
    openers?: Array<{ id: string; label: string }>;
    preferredOpener?: string;
    rawBySlug?: Record<string, string>;
  } = {},
) {
  const openers = options.openers ?? [
    { id: "default", label: "System default app" },
    { id: "code", label: "VS Code" },
  ];
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/capabilities")) {
      return {
        ok: true,
        status: 200,
        json: async () => ({ native_file_actions: true, platform: "win32" }),
      };
    }
    if (url.includes("/openers")) {
      return { ok: true, status: 200, json: async () => ({ openers }) };
    }
    if (url.includes("/preferred-opener")) {
      return {
        ok: true,
        status: 200,
        json: async () => ({ opener: options.preferredOpener ?? "" }),
      };
    }
    const raw = /\/api\/outputs\/([^/]+)\/files\/.+\/raw/.exec(url);
    if (raw) {
      const slug = decodeURIComponent(raw[1]);
      return {
        ok: true,
        status: 200,
        json: async () => ({
          path: "x",
          size: 1,
          text: options.rawBySlug?.[slug] ?? "",
          truncated: false,
        }),
      };
    }
    const plan = /\/api\/outputs\/([^/]+)\/plan/.exec(url);
    if (plan) {
      return { ok: true, status: 200, json: async () => ({ plan: null, steps: [] }) };
    }
    const artifacts = /\/api\/outputs\/([^/]+)\/artifacts/.exec(url);
    if (artifacts) {
      const slug = decodeURIComponent(artifacts[1]);
      return {
        ok: true,
        status: 200,
        json: async () => ({ files: artifactsBySlug[slug] ?? [] }),
      };
    }
    if (url.includes("/api/outputs")) {
      return { ok: true, status: 200, json: async () => ({ sessions: runs }) };
    }
    return { ok: true, status: 200, json: async () => ({}) };
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderView() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <VisualizationView />
    </QueryClientProvider>,
  );
}

function run(over: Partial<OutputSummary>): OutputSummary {
  return {
    slug: "20260615T120000__task__abcdef123456",
    utterance: "Some task",
    status: "unknown",
    mission_id: "mission-1",
    started_at: 1_750_000_000,
    ...over,
  };
}

function file(path: string, over: Partial<ArtifactSummary> = {}): ArtifactSummary {
  return { path, size: 10, mtime: 1_750_000_000, is_text: true, preview: "# x", ...over };
}

const REPORT = file("tasks/019edf/artifacts/files/report.md", {
  size: 34_700,
  preview: "# Report",
});

/** The stage opens on Preview; the Files reader is one click away. */
async function openFiles() {
  fireEvent.click(await screen.findByTestId("visualization-tab-files"));
}

describe("VisualizationView — runs without an artifact", () => {
  it("lists a page-less run as a run row and opens it on Preview as a page, Files one tab away", async () => {
    installFetchMock(
      [
        run({
          slug: "notes-run",
          utterance: "Write notes",
          status: "success",
          summary: "Three notes, saved.",
        }),
      ],
      { "notes-run": [file("tasks/t1/artifacts/files/notes.md")] },
    );

    renderView();

    const rows = await screen.findAllByTestId("visualization-run-row");
    expect(rows).toHaveLength(1);
    expect(rows[0].textContent).toContain("Write notes");
    expect(rows[0].textContent).toContain("Three notes, saved.");
    expect(screen.queryAllByTestId("visualization-artifact-row")).toHaveLength(0);
    // No empty state: the run IS the content.
    expect(screen.queryByTestId("visualization-empty")).toBeNull();
    // The stage opens on Preview — the same four tabs every run gets …
    const previewTab = await screen.findByTestId("visualization-tab-preview");
    expect(previewTab.getAttribute("aria-selected")).toBe("true");
    expect(screen.getByTestId("visualization-tab-files")).toBeDefined();
    expect(screen.getByTestId("visualization-tab-run")).toBeDefined();
    // … and the page composed from the run: headline, the answer, the file in place.
    const page = await screen.findByTestId("output-preview");
    expect(within(page).getByTestId("output-preview-title").textContent).toBe("Write notes");
    await within(page).findByTestId("output-preview-answer");
    expect(within(page).getAllByText("Three notes, saved.").length).toBeGreaterThan(0);
    await waitFor(() =>
      expect(within(page).getAllByTestId("output-preview-file")).toHaveLength(1),
    );
    // Files: the notes above the reader, the file itself in it.
    await openFiles();
    await screen.findByTestId("run-notes");
    await waitFor(() => expect(screen.getByTestId("artifact-path").textContent).toBe("notes.md"));
    // The run status sits in the toolbar, one badge.
    expect(screen.getByTestId("run-status-badge").textContent).toContain("success");
  });

  it("narrows the rail to artifacts or outputs and remembers the pick", async () => {
    window.localStorage.removeItem("jarvis.artifacts.rail-filter");
    installFetchMock(
      [
        run({ slug: "page-run", utterance: "Draw a dashboard", status: "success" }),
        run({ slug: "notes-run", utterance: "Write notes", status: "success" }),
      ],
      {
        "page-run": [
          file("tasks/t1/artifacts/files/dashboard.html", {
            preview: "<title>Sales</title>",
          }),
        ],
        "notes-run": [file("tasks/t2/artifacts/files/notes.md")],
      },
    );

    renderView();

    await screen.findAllByTestId("visualization-artifact-row");
    await screen.findAllByTestId("visualization-run-row");
    const filter = screen.getByTestId("visualization-filter");
    expect(within(filter).getByTestId("visualization-filter-all").textContent).toContain("2");

    fireEvent.click(within(filter).getByTestId("visualization-filter-artifacts"));
    await waitFor(() => expect(screen.queryAllByTestId("visualization-run-row")).toHaveLength(0));
    expect(screen.getAllByTestId("visualization-artifact-row")).toHaveLength(1);
    expect(window.localStorage.getItem("jarvis.artifacts.rail-filter")).toBe("artifacts");

    fireEvent.click(within(filter).getByTestId("visualization-filter-outputs"));
    await waitFor(() =>
      expect(screen.queryAllByTestId("visualization-artifact-row")).toHaveLength(0),
    );
    expect(screen.getAllByTestId("visualization-run-row")).toHaveLength(1);
    // The stage followed the filter: the output is on stage, not the page.
    await screen.findByTestId("output-preview");

    fireEvent.click(within(filter).getByTestId("visualization-filter-all"));
    await waitFor(() => expect(screen.getAllByTestId("visualization-run-row")).toHaveLength(1));
    expect(screen.getAllByTestId("visualization-artifact-row")).toHaveLength(1);
    window.localStorage.removeItem("jarvis.artifacts.rail-filter");
  });

  it("says so only when there are no runs at all", async () => {
    installFetchMock([], {});
    renderView();
    await screen.findByTestId("visualization-empty");
    expect(screen.queryAllByTestId("visualization-run-row")).toHaveLength(0);
  });

  it("shows Continue (and no Restart) for a cancelled run", async () => {
    installFetchMock([run({ slug: "cancelled-slug", status: "cancelled", mission_id: "m-c" })]);
    renderView();
    await waitFor(() =>
      expect(screen.getAllByRole("button", { name: "Continue" }).length).toBeGreaterThan(0),
    );
    expect(screen.queryByRole("button", { name: "Restart" })).toBeNull();
  });

  it("shows Restart (and no Continue) for a failed run", async () => {
    installFetchMock([run({ slug: "error-slug", status: "error", mission_id: "m-e" })]);
    renderView();
    await waitFor(() =>
      expect(screen.getAllByRole("button", { name: "Restart" }).length).toBeGreaterThan(0),
    );
    expect(screen.queryByRole("button", { name: "Continue" })).toBeNull();
  });

  it("shows retained output as needs-review instead of a generic error", async () => {
    installFetchMock([
      run({
        slug: "review-slug",
        status: "error",
        mission_id: "m-review",
        has_partial_output: true,
        needs_review: true,
        artifact_count: 1,
        terminal_reason: "critic_loop_exhausted",
        error: "critic_loop_exhausted",
      }),
    ]);

    renderView();

    // The output page leads with why the run ended …
    const outcome = await screen.findByTestId("output-preview-outcome");
    expect(within(outcome).getAllByText("Needs review").length).toBeGreaterThan(0);
    expect(within(outcome).getByText("critic_loop_exhausted")).toBeDefined();
    // … and Files carries the same verdict above the reader.
    await openFiles();
    await waitFor(() => expect(screen.getByTestId("output-needs-review")).toBeDefined());
    expect(screen.getAllByText("Needs review").length).toBeGreaterThan(0);
    expect(screen.getAllByText("critic_loop_exhausted").length).toBeGreaterThan(0);
    expect(screen.queryByTestId("output-terminal-reason")).toBeNull();
  });

  it("keeps a worker failure red when it merely retained a partial file", async () => {
    installFetchMock([
      run({
        slug: "failed-partial",
        status: "error",
        mission_id: "m-failed",
        has_partial_output: true,
        needs_review: false,
        artifact_count: 1,
        terminal_reason: "task_error",
        error: "task_error",
      }),
    ]);

    renderView();

    const outcome = await screen.findByTestId("output-preview-outcome");
    expect(within(outcome).getByText("task_error")).toBeDefined();
    expect(within(outcome).queryByText("Needs review")).toBeNull();
    await openFiles();
    await waitFor(() => expect(screen.getByTestId("output-terminal-reason")).toBeDefined());
    expect(screen.queryByTestId("output-needs-review")).toBeNull();
    expect(screen.getByTestId("run-status-badge").textContent).toContain("error");
  });

  it("replaces Continue with a live continuation chip and jumps to the child", async () => {
    // Forensic 2026-06-28: a cancelled mission that was already "continued"
    // kept showing a Continue button next to its own running child — the two
    // looked identical, so the user could not tell whether it was running.
    // With a live child the run shows a "running" chip instead, and clicking
    // it jumps to the child.
    installFetchMock([
      run({
        slug: "mission_019f0fa6-4ff6",
        utterance: "Parent task",
        status: "cancelled",
        mission_id: "m-parent",
        active_child_id: "019f0fac-26a3-7c59",
        active_child_slug: "mission_019f0fac-26a3",
      }),
      run({
        slug: "mission_019f0fac-26a3",
        utterance: "Child task",
        status: "running",
        mission_id: "m-child",
      }),
    ]);
    renderView();

    // The running child sorts first and is on stage; pick the parent.
    const rows = await screen.findAllByTestId("visualization-run-row");
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toContain("Child task");
    fireEvent.click(rows[1]);

    await waitFor(() => expect(screen.getByTestId("continuation-chip")).toBeDefined());
    // The redundant Continue button is gone while the continuation is live.
    expect(screen.queryByRole("button", { name: "Continue" })).toBeNull();
    // Clicking the chip selects the running child.
    fireEvent.click(screen.getByTestId("continuation-chip"));
    await waitFor(() =>
      expect(screen.getByTestId("visualization-title").textContent).toBe("Child task"),
    );
  });

  it("offers hold-to-abort for a running run and nothing to re-run for a finished one", async () => {
    installFetchMock([
      run({ slug: "run-slug", status: "running", mission_id: "m-r" }),
      run({ slug: "ok-slug", status: "success", mission_id: "m-ok" }),
    ]);
    renderView();
    await waitFor(() =>
      expect(screen.getAllByRole("button", { name: "Hold to abort" }).length).toBeGreaterThan(0),
    );
    expect(screen.queryByRole("button", { name: "Continue" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Restart" })).toBeNull();
  });

  it("carries the mission payload when a rail row is dragged", async () => {
    installFetchMock([
      run({ slug: "drag-slug", utterance: "Draggable task", status: "success", mission_id: "m-d" }),
    ]);
    renderView();
    const row = (await screen.findAllByTestId("visualization-run-row"))[0];
    expect(row.getAttribute("draggable")).toBe("true");

    const setData = vi.fn();
    const dataTransfer = { setData, setDragImage: vi.fn(), effectAllowed: "" };
    fireEvent.dragStart(row, { dataTransfer });
    expect(setData).toHaveBeenCalledTimes(1);
    const [mime, json] = setData.mock.calls[0];
    expect(mime).toBe(MISSION_DND_MIME);
    expect(JSON.parse(json)).toMatchObject({
      slug: "drag-slug",
      utterance: "Draggable task",
      mission_id: "m-d",
    });
  });
});

describe("VisualizationView — the Files tab", () => {
  it("shows clean paths with primary files before nested assets", async () => {
    installFetchMock(
      [run({ slug: "artifact-slug", status: "success", mission_id: "m-a" })],
      {
        "artifact-slug": [
          file("tasks/019edf/artifacts/files/assets/site.css", { preview: "body {}" }),
          file("tasks/019edf/artifacts/files/report10.md", { preview: "# Ten" }),
          file("tasks/019edf/artifacts/files/report2.md", { preview: "# Two" }),
        ],
      },
    );

    renderView();
    await openFiles();

    await waitFor(() => expect(screen.getAllByTestId("artifact-path")).toHaveLength(3));
    expect(screen.getAllByTestId("artifact-path").map((node) => node.textContent)).toEqual([
      "report2.md",
      "report10.md",
      "assets/site.css",
    ]);
  });

  it("does not render a direct download action for saved mission artifacts", async () => {
    installFetchMock([run({ slug: "artifact-slug", status: "success", mission_id: "m-a" })], {
      "artifact-slug": [REPORT],
    });

    renderView();
    await openFiles();

    await waitFor(() => expect(screen.getByTestId("artifact-path").textContent).toBe("report.md"));
    expect(screen.queryByText("tasks/019edf/artifacts/files/report.md")).toBeNull();

    expect(screen.queryByTitle("Download")).toBeNull();
    // The artifact opens in an app of the user's choice (chooser), not a fixed
    // "open in browser" — and the file is already mirrored to Downloads.
    expect(screen.getByTitle("Open")).toBeDefined();
    expect(screen.getByTitle("Change how this opens")).toBeDefined();
    expect(screen.getByTitle("Reveal in folder")).toBeDefined();
    expect(screen.queryByTitle("Open in browser")).toBeNull();
  });

  it("routes the browser chooser option through the open-external bridge", async () => {
    const fetchMock = installFetchMock(
      [run({ slug: "artifact-slug", status: "success", mission_id: "m-a" })],
      { "artifact-slug": [REPORT] },
      {
        openers: [
          { id: "default", label: "System default app" },
          { id: "browser", label: "Browser" },
          { id: "code", label: "VS Code" },
        ],
      },
    );
    const openSpy = vi.spyOn(window, "open").mockImplementation(() => null);

    renderView();
    await openFiles();

    await waitFor(() => expect(screen.getByTestId("artifact-path").textContent).toBe("report.md"));

    fireEvent.click(screen.getByTitle("Change how this opens"));
    fireEvent.click(await screen.findByText("Browser"));

    // Absolute render URL handed to the bridge (open_url needs http(s) absolute),
    // never a bare window.open (WebView2 drops it) and never /open-with.
    await waitFor(() => expect(openExternalUrl).toHaveBeenCalledTimes(1));
    const url = vi.mocked(openExternalUrl).mock.calls[0][0];
    expect(url).toMatch(/^https?:\/\//);
    expect(url).toContain(
      "/api/outputs/artifact-slug/files/tasks/019edf/artifacts/files/report.md/view",
    );
    expect(openSpy).not.toHaveBeenCalled();
    expect(
      fetchMock.mock.calls.some(([input]) => String(input).includes("/open-with")),
    ).toBe(false);
  });

  it("routes a remembered browser preference through the open-external bridge", async () => {
    const fetchMock = installFetchMock(
      [run({ slug: "artifact-slug", status: "success", mission_id: "m-a" })],
      { "artifact-slug": [REPORT] },
      {
        openers: [
          { id: "default", label: "System default app" },
          { id: "browser", label: "Browser" },
        ],
        preferredOpener: "browser",
      },
    );
    const openSpy = vi.spyOn(window, "open").mockImplementation(() => null);

    renderView();
    await openFiles();

    await waitFor(() => expect(screen.getByTestId("artifact-path").textContent).toBe("report.md"));
    // The remembered preference is a query of its own; "Open" asks the chooser
    // until it has resolved, so wait for it before clicking.
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([input]) => String(input).includes("/preferred-opener")),
      ).toBe(true),
    );
    await act(() => new Promise((resolve) => setTimeout(resolve, 20)));
    fireEvent.click(screen.getByTitle("Open"));

    await waitFor(() => expect(openExternalUrl).toHaveBeenCalledTimes(1));
    const url = vi.mocked(openExternalUrl).mock.calls[0][0];
    expect(url).toMatch(/^https?:\/\//);
    expect(url).toContain(
      "/api/outputs/artifact-slug/files/tasks/019edf/artifacts/files/report.md/view",
    );
    expect(openSpy).not.toHaveBeenCalled();
    expect(
      fetchMock.mock.calls.some(([input]) => String(input).includes("/open-with")),
    ).toBe(false);
  });

  it("renders an HTML file in the reader as a sandboxed page, source behind the toggle", async () => {
    // Regression for "HTML files appear as raw source": `.html` counts as a
    // text file server-side, so the old preview dumped the markup into a
    // <pre>. The reader frames the file from `/page` (scripts run, network
    // shut) with `sandbox="allow-scripts"` and no same-origin.
    const HTML_SOURCE = "<!doctype html><html><head><title>HelloBot</title></head><body>hi</body></html>";
    const HTML_PATH = "tasks/019e3288/artifacts/files/HelloBot.html";
    const fetchMock = installFetchMock(
      [run({ slug: "html-slug", status: "success", mission_id: "m-h" })],
      { "html-slug": [file(HTML_PATH, { preview: HTML_SOURCE, size: HTML_SOURCE.length })] },
      { rawBySlug: { "html-slug": HTML_SOURCE } },
    );

    renderView();

    // The page is an artifact: it opens on Preview, the stage's own frame.
    await screen.findByTestId("visualization-frame");
    fireEvent.click(screen.getByTestId("visualization-tab-files"));

    const reader = await screen.findByTestId("run-files");
    await waitFor(() => expect(within(reader).getByText("HelloBot.html")).toBeTruthy());
    const iframe = await waitFor(() => {
      const el = reader.querySelector("iframe");
      expect(el).toBeTruthy();
      return el as HTMLIFrameElement;
    });
    // The reader's frame follows the app's theme through the same `?theme=`
    // query the stage appends.
    expect(iframe.getAttribute("src")).toMatch(
      new RegExp(`^/api/outputs/html-slug/files/${HTML_PATH.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}/page\\?theme=(light|dark)$`),
    );
    expect(iframe.getAttribute("sandbox")).toBe("allow-scripts");
    expect(screen.queryByText(HTML_SOURCE)).toBeNull();
    // Rendered mode never fetches the raw text.
    expect(fetchMock.mock.calls.some(([input]) => String(input).includes("/raw"))).toBe(false);

    fireEvent.click(within(reader).getByRole("button", { name: "Source" }));
    await waitFor(() => expect(screen.getByText(HTML_SOURCE)).toBeTruthy());
    expect(reader.querySelector("iframe")).toBeNull();

    fireEvent.click(within(reader).getByRole("button", { name: "Rendered" }));
    await waitFor(() => expect(reader.querySelector("iframe")).toBeTruthy());
  });
});

describe("the retired outputs section id", () => {
  it("still resolves to Artifacts — deep links, remembered views, voice", () => {
    expect(resolveSectionId("outputs")).toBe("visualization");
    expect(resolveSectionId("visualization")).toBe("visualization");
    expect(resolveSectionId("weather")).toBeNull();
    expect(initialSectionFromSearch("?view=outputs")).toBe("visualization");
  });
});
