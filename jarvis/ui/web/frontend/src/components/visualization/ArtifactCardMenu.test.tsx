/**
 * The Artifacts card's right-click menu.
 *
 * Contracts worth pinning:
 * - a right-click on a card opens the card's own menu, never the app-wide
 *   Cut/Copy/Paste one (the event stops at the card),
 * - an artifact offers open / open in browser / save a copy / delete; a run
 *   without one offers open / delete; "Show in folder" appears only where
 *   native file actions exist,
 * - delete asks first, then sends DELETE for that file (an artifact) or for
 *   the whole run (a run card), and the list is read again,
 * - a run that is still working cannot be deleted from its card.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { VisualizationView } from "@/views/VisualizationView";
import type { ArtifactSummary, OutputSummary } from "@/hooks/useOutputs";

vi.mock("@/hooks/useWebSocket", () => ({
  getWSClient: () => null,
}));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

const PAGE_PATH = "tasks/t1/artifacts/files/dashboard.html";

function file(path: string, over: Partial<ArtifactSummary> = {}): ArtifactSummary {
  return { path, size: 2048, mtime: 1_700_000_000, is_text: false, preview: null, ...over };
}

function installFetchMock(
  runs: OutputSummary[],
  artifactsBySlug: Record<string, ArtifactSummary[]>,
  { native = false }: { native?: boolean } = {},
) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (init?.method === "DELETE") {
      return { ok: true, status: 200, json: async () => ({ deleted: "run" }) };
    }
    if (url.includes("/capabilities")) {
      return {
        ok: true,
        status: 200,
        json: async () => ({ native_file_actions: native, platform: "linux" }),
      };
    }
    const artifacts = /\/api\/outputs\/([^/]+)\/artifacts/.exec(url);
    if (artifacts) {
      const slug = decodeURIComponent(artifacts[1]);
      return { ok: true, status: 200, json: async () => ({ files: artifactsBySlug[slug] ?? [] }) };
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

function deleteCalls(fetchMock: ReturnType<typeof installFetchMock>): string[] {
  return fetchMock.mock.calls
    .filter(([, init]) => init?.method === "DELETE")
    .map(([input]) => String(input));
}

describe("Artifact card menu", () => {
  it("opens on right-click and keeps the click away from the app-wide menu", async () => {
    installFetchMock([{ slug: "mission_dash", status: "success" }], {
      mission_dash: [file(PAGE_PATH)],
    });
    const documentMenu = vi.fn();
    document.addEventListener("contextmenu", documentMenu);
    renderView();

    const card = await screen.findByTestId("visualization-artifact-row");
    fireEvent.contextMenu(card, { clientX: 120, clientY: 80 });

    expect(screen.getByTestId("artifact-card-menu")).toBeTruthy();
    expect(screen.getByTestId("artifact-menu-open")).toBeTruthy();
    expect(screen.getByTestId("artifact-menu-open-external")).toBeTruthy();
    expect(screen.getByTestId("artifact-menu-download")).toBeTruthy();
    expect(screen.queryByTestId("artifact-menu-reveal")).toBeNull();
    expect(documentMenu).not.toHaveBeenCalled();
    document.removeEventListener("contextmenu", documentMenu);
  });

  it("offers Show in folder only where native file actions exist", async () => {
    installFetchMock(
      [{ slug: "mission_dash", status: "success" }],
      { mission_dash: [file(PAGE_PATH)] },
      { native: true },
    );
    renderView();

    const card = await screen.findByTestId("visualization-artifact-row");
    // The capability answer may land after the card does.
    await waitFor(() => {
      fireEvent.contextMenu(card, { clientX: 10, clientY: 10 });
      expect(screen.getByTestId("artifact-menu-reveal")).toBeTruthy();
    });
  });

  it("deletes one artifact file after asking", async () => {
    const fetchMock = installFetchMock([{ slug: "mission_dash", status: "success" }], {
      mission_dash: [file(PAGE_PATH)],
    });
    renderView();

    const card = await screen.findByTestId("visualization-artifact-row");
    fireEvent.contextMenu(card, { clientX: 10, clientY: 10 });
    fireEvent.click(screen.getByTestId("artifact-menu-delete"));

    // Nothing is deleted before the question is answered.
    expect(screen.getByTestId("artifact-confirm-delete")).toBeTruthy();
    expect(deleteCalls(fetchMock)).toEqual([]);

    fireEvent.click(screen.getByTestId("artifact-confirm-delete-confirm"));
    await waitFor(() =>
      expect(deleteCalls(fetchMock)).toEqual([`/api/outputs/mission_dash/files/${PAGE_PATH}`]),
    );
    await waitFor(() => expect(screen.queryByTestId("artifact-confirm-delete")).toBeNull());
  });

  it("deletes a whole run from a run card", async () => {
    const fetchMock = installFetchMock(
      [{ slug: "run-text", utterance: "Write notes", status: "success" }],
      { "run-text": [file("tasks/t1/artifacts/files/notes.md", { is_text: true })] },
    );
    renderView();

    const card = await screen.findByTestId("visualization-run-row");
    fireEvent.contextMenu(card, { clientX: 10, clientY: 10 });
    expect(screen.queryByTestId("artifact-menu-download")).toBeNull();
    fireEvent.click(screen.getByTestId("artifact-menu-delete"));
    fireEvent.click(screen.getByTestId("artifact-confirm-delete-confirm"));

    await waitFor(() => expect(deleteCalls(fetchMock)).toEqual(["/api/outputs/run-text"]));
  });

  it("does not delete a run that is still working", async () => {
    const fetchMock = installFetchMock(
      [{ slug: "run-busy", utterance: "Crunch numbers", status: "running" }],
      {},
    );
    renderView();

    const card = await screen.findByTestId("visualization-run-row");
    fireEvent.contextMenu(card, { clientX: 10, clientY: 10 });
    const item = screen.getByTestId("artifact-menu-delete") as HTMLButtonElement;
    expect(item.disabled).toBe(true);

    fireEvent.keyDown(card, { key: "Delete" });
    expect(screen.queryByTestId("artifact-confirm-delete")).toBeNull();
    expect(deleteCalls(fetchMock)).toEqual([]);
  });

  it("asks before deleting when the Delete key is pressed on a card", async () => {
    installFetchMock([{ slug: "mission_dash", status: "success" }], {
      mission_dash: [file(PAGE_PATH)],
    });
    renderView();

    const card = await screen.findByTestId("visualization-artifact-row");
    fireEvent.keyDown(card, { key: "Delete" });

    expect(screen.getByTestId("artifact-confirm-delete")).toBeTruthy();
  });
});
