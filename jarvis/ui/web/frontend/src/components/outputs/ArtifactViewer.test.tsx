/**
 * ArtifactViewer — a deliverable is SHOWN, not dumped.
 *
 * A Markdown report reads as a typeset document (headings, lists), an image
 * is an image, and the selected file's source stays one click away. The rail
 * lists every file; picking one swaps the pane.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ArtifactViewer, resolveSiblingPath } from "@/components/outputs/ArtifactViewer";
import type { ArtifactSummary } from "@/hooks/useOutputs";

vi.mock("@/lib/openExternal", () => ({
  openExternalUrl: vi.fn(async () => {}),
}));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const SLUG = "mission_0199aaaa-bbbb";
const MD_PATH = "tasks/t1/artifacts/files/report.md";
const MD_TEXT = "# Quarterly summary\n\nSome **bold** prose.\n\n- first\n- second\n";
const PNG_PATH = "tasks/t1/artifacts/files/chart.png";

function installFetchMock(texts: Record<string, string>) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/raw")) {
      const hit = Object.entries(texts).find(([p]) => url.includes(p));
      return {
        ok: true,
        status: 200,
        json: async () => ({
          path: hit?.[0] ?? "",
          size: hit?.[1].length ?? 0,
          text: hit?.[1] ?? "",
          truncated: false,
        }),
      };
    }
    if (url.includes("/openers")) {
      return { ok: true, status: 200, json: async () => ({ openers: [] }) };
    }
    if (url.includes("/preferred-opener")) {
      return { ok: true, status: 200, json: async () => ({ opener: "" }) };
    }
    return { ok: true, status: 200, json: async () => ({}) };
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderViewer(files: ArtifactSummary[]) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ArtifactViewer slug={SLUG} files={files} nativeActions={false} />
    </QueryClientProvider>,
  );
}

const MD_FILE: ArtifactSummary = {
  path: MD_PATH,
  size: MD_TEXT.length,
  mtime: 1,
  is_text: true,
  preview: MD_TEXT,
};
const PNG_FILE: ArtifactSummary = {
  path: PNG_PATH,
  size: 4096,
  mtime: 2,
  is_text: false,
  preview: null,
};

describe("ArtifactViewer", () => {
  it("renders a Markdown deliverable as a document, with the source behind a toggle", async () => {
    installFetchMock({ [MD_PATH]: MD_TEXT });
    renderViewer([MD_FILE]);

    // Typeset: a real heading and list items, not the raw "# " markup.
    const heading = await screen.findByRole("heading", { name: "Quarterly summary" });
    expect(heading.tagName).toBe("H1");
    expect(screen.getByText("first")).toBeTruthy();
    expect(screen.queryByText(MD_TEXT)).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Source" }));
    await waitFor(() =>
      expect(screen.queryByRole("heading", { name: "Quarterly summary" })).toBeNull(),
    );
    expect(screen.getByRole("button", { name: "Rendered" }).getAttribute("aria-pressed")).toBe(
      "false",
    );
  });

  it("lists every file in the rail and swaps the pane on selection", async () => {
    installFetchMock({ [MD_PATH]: MD_TEXT });
    renderViewer([MD_FILE, PNG_FILE]);

    const rail = screen.getAllByTestId("artifact-path");
    expect(rail.map((n) => n.textContent)).toEqual(["report.md", "chart.png"]);
    await screen.findByRole("heading", { name: "Quarterly summary" });

    fireEvent.click(screen.getByText("chart.png"));
    const img = await waitFor(() => {
      const el = document.querySelector("img");
      expect(el).toBeTruthy();
      return el as HTMLImageElement;
    });
    expect(img.getAttribute("src")).toBe(
      `/api/outputs/${SLUG}/files/${PNG_PATH}/download?disposition=inline`,
    );
    expect(screen.queryByRole("heading", { name: "Quarterly summary" })).toBeNull();
  });

  it("opens and closes the full-window reader", async () => {
    installFetchMock({ [MD_PATH]: MD_TEXT });
    renderViewer([MD_FILE]);
    await screen.findByRole("heading", { name: "Quarterly summary" });

    fireEvent.click(screen.getByRole("button", { name: "Open as full-window reader" }));
    expect(screen.getByTestId("artifact-reader-fullscreen")).toBeTruthy();
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() =>
      expect(screen.queryByTestId("artifact-reader-fullscreen")).toBeNull(),
    );
  });
});

const HTML_PATH = "tasks/t1/artifacts/files/dash.html";
const HTML_FILE: ArtifactSummary = {
  path: HTML_PATH,
  size: 64,
  mtime: 3,
  is_text: true,
  preview: "<script>run()</script>",
};

function installHeadProbeMock(headStatus: number) {
  const base = installFetchMock({});
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (init?.method === "HEAD") {
      return { ok: headStatus >= 200 && headStatus < 300, status: headStatus };
    }
    return base(input);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("ArtifactViewer HTML page probe", () => {
  it("frames the sandboxed /page when the backend answers HEAD with 405 (route present)", async () => {
    installHeadProbeMock(405);
    renderViewer([HTML_FILE]);
    const frame = await waitFor(() => screen.getByTitle("dash.html") as HTMLIFrameElement);
    expect(frame.getAttribute("src")).toContain("/page");
    expect(frame.getAttribute("sandbox")).toBe("allow-scripts");
  });

  it("falls back to the no-script inline download when the route is missing (404)", async () => {
    installHeadProbeMock(404);
    renderViewer([HTML_FILE]);
    const frame = await waitFor(() => screen.getByTitle("dash.html") as HTMLIFrameElement);
    expect(frame.getAttribute("src")).toContain("/download?disposition=inline");
  });
});

describe("resolveSiblingPath", () => {
  it("resolves relative links inside the archive and rejects escapes", () => {
    expect(resolveSiblingPath("tasks/t/artifacts/files/report.md", "chart.png")).toBe(
      "tasks/t/artifacts/files/chart.png",
    );
    expect(resolveSiblingPath("tasks/t/artifacts/files/a/b.md", "../c.md")).toBe(
      "tasks/t/artifacts/files/c.md",
    );
    expect(resolveSiblingPath("x.md", "https://example.com/a.png")).toBeNull();
    expect(resolveSiblingPath("x.md", "#section")).toBeNull();
    expect(resolveSiblingPath("x.md", "../../escape.md")).toBeNull();
  });
});
