/**
 * Outputs view — Continue/Restart button visibility per mission status.
 *
 * Gating contract:
 * - status "cancelled" + a mission_id → a "Continue" button.
 * - status "error"     + a mission_id → a "Restart" button.
 * - status "running" / "success" / no mission_id → neither.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { OutputsView } from "@/views/OutputsView";
import type { ArtifactSummary, OutputSummary } from "@/hooks/useOutputs";

// ViewHeader pulls in ChatsView, which subscribes to a WS client; null keeps
// that effect a deterministic no-op in jsdom (same pattern as ClisView.test).
vi.mock("@/hooks/useWebSocket", () => ({
  getWSClient: () => null,
}));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function installFetchMock(
  sessions: OutputSummary[],
  artifacts: ArtifactSummary[] = [],
  openers = [
    { id: "default", label: "System default app" },
    { id: "code", label: "VS Code" },
  ],
  preferredOpener = "",
) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/artifacts")) {
      return { ok: true, status: 200, json: async () => ({ files: artifacts }) };
    }
    if (url.includes("/plan")) {
      return {
        ok: true,
        status: 200,
        json: async () => ({ plan: null, steps: [] }),
      };
    }
    if (url.includes("/capabilities")) {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          native_file_actions: true,
          platform: "win32",
        }),
      };
    }
    if (url.includes("/openers")) {
      return {
        ok: true,
        status: 200,
        json: async () => ({ openers }),
      };
    }
    if (url.includes("/preferred-opener")) {
      return {
        ok: true,
        status: 200,
        json: async () => ({ opener: preferredOpener }),
      };
    }
    if (url.includes("/api/outputs")) {
      return { ok: true, status: 200, json: async () => ({ sessions }) };
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
      <OutputsView />
    </QueryClientProvider>,
  );
}

function session(over: Partial<OutputSummary>): OutputSummary {
  return {
    slug: "20260615T120000__task__abcdef123456",
    utterance: "Some task",
    status: "unknown",
    mission_id: "mission-1",
    started_at: 1_750_000_000,
    ...over,
  };
}

describe("OutputsView rerun button gating", () => {
  it("shows Continue (and no Restart) for a cancelled mission", async () => {
    installFetchMock([
      session({ slug: "cancelled-slug", status: "cancelled", mission_id: "m-c" }),
    ]);
    renderView();
    await waitFor(() =>
      expect(
        screen.getAllByRole("button", { name: "Continue" }).length,
      ).toBeGreaterThan(0),
    );
    expect(screen.queryByRole("button", { name: "Restart" })).toBeNull();
  });

  it("shows Restart (and no Continue) for a failed mission", async () => {
    installFetchMock([
      session({ slug: "error-slug", status: "error", mission_id: "m-e" }),
    ]);
    renderView();
    await waitFor(() =>
      expect(
        screen.getAllByRole("button", { name: "Restart" }).length,
      ).toBeGreaterThan(0),
    );
    expect(screen.queryByRole("button", { name: "Continue" })).toBeNull();
  });

  it("shows neither for running or successful missions", async () => {
    installFetchMock([
      session({ slug: "run-slug", status: "running", mission_id: "m-r" }),
      session({ slug: "ok-slug", status: "success", mission_id: "m-ok" }),
    ]);
    renderView();
    // Let the list settle: the running row renders a hold-to-abort control
    // (there may be more than one — the auto-selected detail pane too).
    await waitFor(() =>
      expect(
        screen.getAllByRole("button", { name: "Hold to abort" }).length,
      ).toBeGreaterThan(0),
    );
    expect(screen.queryByRole("button", { name: "Continue" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Restart" })).toBeNull();
  });
});

describe("OutputsView artifact actions", () => {
  it("does not render a direct download action for saved mission artifacts", async () => {
    installFetchMock(
      [session({ slug: "artifact-slug", status: "success", mission_id: "m-a" })],
      [
        {
          path: "tasks/019edf/artifacts/files/report.md",
          size: 34_700,
          mtime: 1_750_000_000,
          is_text: true,
          preview: "# Report",
        },
      ],
    );

    renderView();

    await waitFor(() =>
      expect(
        screen.getByText("tasks/019edf/artifacts/files/report.md"),
      ).toBeDefined(),
    );

    expect(screen.queryByTitle("Download")).toBeNull();
    // The artifact opens in an app of the user's choice (chooser), not a fixed
    // "open in browser" — and the file is already mirrored to Downloads.
    expect(screen.getByTitle("Open")).toBeDefined();
    expect(screen.getByTitle("Change how this opens")).toBeDefined();
    expect(screen.getByTitle("Reveal in folder")).toBeDefined();
    expect(screen.queryByTitle("Open in browser")).toBeNull();
  });

  it("opens the browser chooser option via the rendered artifact URL", async () => {
    const fetchMock = installFetchMock(
      [session({ slug: "artifact-slug", status: "success", mission_id: "m-a" })],
      [
        {
          path: "tasks/019edf/artifacts/files/report.md",
          size: 34_700,
          mtime: 1_750_000_000,
          is_text: true,
          preview: "# Report",
        },
      ],
      [
        { id: "default", label: "System default app" },
        { id: "browser", label: "Browser" },
        { id: "code", label: "VS Code" },
      ],
    );
    const openSpy = vi.spyOn(window, "open").mockImplementation(() => null);

    renderView();

    await waitFor(() =>
      expect(
        screen.getByText("tasks/019edf/artifacts/files/report.md"),
      ).toBeDefined(),
    );

    fireEvent.click(screen.getByTitle("Change how this opens"));
    fireEvent.click(await screen.findByText("Browser"));

    expect(openSpy).toHaveBeenCalledWith(
      "/api/outputs/artifact-slug/files/tasks/019edf/artifacts/files/report.md/view",
      "_blank",
      "noopener,noreferrer",
    );
    expect(
      fetchMock.mock.calls.some(([input]) => String(input).includes("/open-with")),
    ).toBe(false);
  });

  it("opens a remembered browser preference via the rendered artifact URL", async () => {
    const fetchMock = installFetchMock(
      [session({ slug: "artifact-slug", status: "success", mission_id: "m-a" })],
      [
        {
          path: "tasks/019edf/artifacts/files/report.md",
          size: 34_700,
          mtime: 1_750_000_000,
          is_text: true,
          preview: "# Report",
        },
      ],
      [
        { id: "default", label: "System default app" },
        { id: "browser", label: "Browser" },
      ],
      "browser",
    );
    const openSpy = vi.spyOn(window, "open").mockImplementation(() => null);

    renderView();

    await waitFor(() =>
      expect(
        screen.getByText("tasks/019edf/artifacts/files/report.md"),
      ).toBeDefined(),
    );

    fireEvent.click(screen.getByTitle("Open"));

    expect(openSpy).toHaveBeenCalledWith(
      "/api/outputs/artifact-slug/files/tasks/019edf/artifacts/files/report.md/view",
      "_blank",
      "noopener,noreferrer",
    );
    expect(
      fetchMock.mock.calls.some(([input]) => String(input).includes("/open-with")),
    ).toBe(false);
  });
});
