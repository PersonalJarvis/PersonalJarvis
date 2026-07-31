/**
 * ExportPreview tests — "what does 'import everything' actually mean?".
 *
 * The two properties this control exists for: the file can get onto the
 * machine at all (upload, which then fills the path), and the user can see
 * what is inside it BEFORE creating a source. The honesty half is pinned too:
 * a lower-bound count says so, a failed read shows the backend's own sentence
 * instead of a generic error, and formats that share a label are merged
 * rather than rendered as "3,214 mails - 1 mails".
 */
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { ExportPreview, formatBytes } from "@/components/ultrawiki/ExportPreview";

function Harness({ initial = "" }: { initial?: string }): JSX.Element {
  const [path, setPath] = useState(initial);
  return (
    <>
      <ExportPreview path={path} onPathChange={setPath} />
      <span data-testid="harness-path">{path}</span>
    </>
  );
}

const REPORT = {
  path: "/drop/takeout.zip",
  exists: true,
  is_dir: false,
  formats: {
    mbox: { files: 2, items_estimate: 3214, exact: true },
    // A loose .eml shares the "mails" label — the two must be merged.
    eml: { files: 1, items_estimate: 4, exact: true },
    ics: { files: 1, items_estimate: 122, exact: true },
    whatsapp: { files: 8, items_estimate: 96, exact: true },
  },
  unknown: [{ extension: ".dll", files: 41 }],
  unreadable: [{ path: "scan.pdf", reason: "the PDF is password protected" }],
  archives: { files: 1, entries: 12, max_depth: 2, budget_exhausted: false },
  total_bytes: 1024 * 1024 * 3,
  files_seen: 55,
  items_estimate: 3436,
  unknown_files: 41,
  truncated: false,
  notes: [],
};

function installFetchMock(
  routes: Record<string, (init?: RequestInit) => unknown>,
  failing: string[] = [],
) {
  const fetchMock = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      for (const prefix of failing) {
        if (url.startsWith(prefix)) {
          return {
            ok: false,
            status: 404,
            statusText: "Not Found",
            json: async () => ({ detail: "nothing exists at '/nope'" }),
          } as Response;
        }
      }
      const prefixes = Object.keys(routes).sort((a, b) => b.length - a.length);
      for (const prefix of prefixes) {
        if (url.startsWith(prefix)) {
          return {
            ok: true,
            status: 200,
            statusText: "OK",
            json: async () => routes[prefix](init),
          } as Response;
        }
      }
      throw new Error(`unexpected fetch ${url}`);
    },
  );
  (globalThis as unknown as { fetch: typeof fetch }).fetch =
    fetchMock as unknown as typeof fetch;
  return fetchMock;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("ExportPreview — see what is in it before importing", () => {
  it("reports the formats it found in one readable line", async () => {
    const fetchMock = installFetchMock({
      "/api/ultrawiki/export/preview": () => REPORT,
    });
    render(<Harness initial="/drop/takeout.zip" />);

    fireEvent.click(screen.getByTestId("ultrawiki-export-preview"));

    await waitFor(() => {
      expect(screen.getByTestId("ultrawiki-export-report")).toBeDefined();
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/ultrawiki/export/preview",
      expect.objectContaining({ method: "POST" }),
    );
    const report = screen.getByTestId("ultrawiki-export-report");
    // Two mail formats, ONE "mails" segment carrying their sum.
    expect(report.textContent).toContain("3,218 mails");
    expect(screen.queryByTestId("uw-export-count-eml")).toBeNull();
    expect(report.textContent).toContain("122 calendar events");
    expect(report.textContent).toContain("96 chat days");
    // The skipped half is stated, not hidden.
    expect(report.textContent).toContain("41 unrecognised files skipped");
    expect(report.textContent).toContain("1 files could not be read");
    expect(report.textContent).toContain("1 archives (12 entries)");
    expect(report.textContent).toContain("3 MB in total");
  });

  it("says so when the numbers are only a lower bound", async () => {
    installFetchMock({
      "/api/ultrawiki/export/preview": () => ({
        ...REPORT,
        truncated: true,
        formats: { mbox: { files: 1, items_estimate: 5000, exact: false } },
        notes: ["More than 5000 files were found; the preview stopped there."],
      }),
    });
    render(<Harness initial="/drop/huge" />);

    fireEvent.click(screen.getByTestId("ultrawiki-export-preview"));

    await waitFor(() => {
      expect(screen.getByTestId("ultrawiki-export-truncated")).toBeDefined();
    });
    // A "~" in front of an inexact count, and the backend's own sentence.
    expect(screen.getByTestId("ultrawiki-export-report").textContent).toContain(
      "~5,000 mails",
    );
    expect(screen.getByTestId("ultrawiki-export-report").textContent).toContain(
      "the preview stopped there",
    );
  });

  it("shows the backend's reason when the path cannot be read", async () => {
    installFetchMock({}, ["/api/ultrawiki/export/preview"]);
    render(<Harness initial="/nope" />);

    fireEvent.click(screen.getByTestId("ultrawiki-export-preview"));

    await waitFor(() => {
      expect(screen.getByTestId("ultrawiki-export-error")).toBeDefined();
    });
    expect(screen.getByTestId("ultrawiki-export-error").textContent).toContain(
      "nothing exists at '/nope'",
    );
    expect(screen.queryByTestId("ultrawiki-export-report")).toBeNull();
  });

  it("cannot preview an empty path", () => {
    installFetchMock({});
    render(<Harness />);
    expect(
      screen.getByTestId("ultrawiki-export-preview").hasAttribute("disabled"),
    ).toBe(true);
  });

  it("drops a stale report when the path is edited", async () => {
    installFetchMock({ "/api/ultrawiki/export/preview": () => REPORT });
    render(<Harness initial="/drop/takeout.zip" />);

    fireEvent.click(screen.getByTestId("ultrawiki-export-preview"));
    await waitFor(() => {
      expect(screen.getByTestId("ultrawiki-export-report")).toBeDefined();
    });

    fireEvent.change(screen.getByTestId("ultrawiki-export-path-input"), {
      target: { value: "/drop/something-else.zip" },
    });

    // A report about the previous path would be a confident wrong answer.
    expect(screen.queryByTestId("ultrawiki-export-report")).toBeNull();
  });
});

describe("ExportPreview — getting the file here in the first place", () => {
  it("uploads a file and fills the path with where it landed", async () => {
    const fetchMock = installFetchMock({
      "/api/ultrawiki/export/upload": () => ({
        path: "/data/ultrawiki/uploads/abc123/takeout.zip",
        name: "takeout.zip",
        size: 4096,
        detail: "The file is on this machine.",
      }),
    });
    render(<Harness />);

    const file = new File(["archive bytes"], "takeout.zip", {
      type: "application/zip",
    });
    fireEvent.change(screen.getByTestId("ultrawiki-export-upload-input"), {
      target: { files: [file] },
    });

    await waitFor(() => {
      expect(screen.getByTestId("harness-path").textContent).toBe(
        "/data/ultrawiki/uploads/abc123/takeout.zip",
      );
    });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/ultrawiki/export/upload");
    expect(init.method).toBe("POST");
    // No Content-Type header: the browser has to set the multipart boundary.
    expect(init.headers).toBeUndefined();
    expect(init.body).toBeInstanceOf(FormData);
    expect(screen.getByTestId("ultrawiki-export-uploaded").textContent).toContain(
      "takeout.zip",
    );
    // The path is now previewable.
    expect(
      screen.getByTestId("ultrawiki-export-preview").hasAttribute("disabled"),
    ).toBe(false);
  });

  it("reports a refused upload instead of leaving a dead field", async () => {
    installFetchMock({}, ["/api/ultrawiki/export/upload"]);
    render(<Harness />);

    fireEvent.change(screen.getByTestId("ultrawiki-export-upload-input"), {
      target: { files: [new File(["x"], "../escape.zip")] },
    });

    await waitFor(() => {
      expect(screen.getByTestId("ultrawiki-export-error")).toBeDefined();
    });
    expect(screen.getByTestId("harness-path").textContent).toBe("");
  });
});

describe("formatBytes", () => {
  it("renders a size a person can read", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(2048)).toBe("2 KB");
    expect(formatBytes(1024 * 1024 * 3)).toBe("3 MB");
    expect(formatBytes(1024 * 1024 * 1024 * 2.5)).toBe("2.5 GB");
  });
});
