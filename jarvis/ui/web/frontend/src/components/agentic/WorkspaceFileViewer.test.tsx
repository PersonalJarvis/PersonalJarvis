import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/i18n", () => ({
  useT: () => (key: string) =>
    ({
      "agentic_grid.viewer.title": "File preview",
      "agentic_grid.viewer.close": "Close file preview",
      "agentic_grid.viewer.loading": "Opening file…",
      "agentic_grid.viewer.failed": "This file could not be displayed.",
      "agentic_grid.viewer.truncated": "The preview is truncated.",
      "agentic_grid.viewer.binary": "Binary preview",
      "agentic_grid.viewer.remote_image": "External image blocked: {0}",
      "agentic_grid.viewer.empty": "This file has no previewable content.",
      "common.retry": "Retry",
    })[key] ?? key,
}));

vi.mock("@/lib/agenticIdeApi", () => ({
  fetchWorkspaceFilePreview: vi.fn(),
  workspaceFileUrl: (workspaceId: string, path: string) =>
    `/api/agentic-ide/workspaces/${workspaceId}/file?path=${encodeURIComponent(path)}`,
}));

import * as api from "@/lib/agenticIdeApi";
import { classifyWorkspaceFile, WorkspaceFileViewer } from "./WorkspaceFileViewer";

describe("WorkspaceFileViewer", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it.each([
    ["README.md", "markdown"],
    ["manual.pdf", "pdf"],
    ["photo.webp", "image"],
    ["voice.mp3", "audio"],
    ["demo.mp4", "video"],
    ["report.docx", "document"],
    ["archive.zip", "binary"],
  ] as const)("classifies %s as %s", (path, kind) => {
    expect(classifyWorkspaceFile(path)).toBe(kind);
  });

  it("renders Markdown safely and opens relative links inside the app", async () => {
    vi.mocked(api.fetchWorkspaceFilePreview).mockResolvedValue({
      workspace_id: "workspace-1",
      path: "docs/README.md",
      name: "README.md",
      size: 24,
      media_type: "text/markdown",
      text: "# Hello\n\n[Guide](guide.md)\n\n![Spy](https://tracker.invalid/pixel.png)",
      truncated: false,
      hex_preview: null,
    });
    const onOpenFile = vi.fn();

    render(
      <WorkspaceFileViewer
        workspaceId="workspace-1"
        path="docs/README.md"
        onClose={vi.fn()}
        onOpenFile={onOpenFile}
      />,
    );

    expect(await screen.findByRole("heading", { name: "Hello" })).toBeTruthy();
    fireEvent.click(screen.getByRole("link", { name: "Guide" }));
    expect(onOpenFile).toHaveBeenCalledWith("docs/guide.md");
    expect(screen.getByText("External image blocked: Spy")).toBeTruthy();
    expect(document.querySelector('img[src^="https://tracker.invalid"]')).toBeNull();
  });

  it("embeds PDFs and closes with Escape", () => {
    const onClose = vi.fn();
    render(
      <WorkspaceFileViewer
        workspaceId="workspace-1"
        path="docs/manual.pdf"
        onClose={onClose}
        onOpenFile={vi.fn()}
      />,
    );

    expect(screen.getByTitle("manual.pdf").getAttribute("src")).toContain(
      "docs%2Fmanual.pdf",
    );
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledOnce();
    expect(api.fetchWorkspaceFilePreview).not.toHaveBeenCalled();
  });

  it("shows a bounded hexadecimal fallback for unknown binary formats", async () => {
    vi.mocked(api.fetchWorkspaceFilePreview).mockResolvedValue({
      workspace_id: "workspace-1",
      path: "archive.bin",
      name: "archive.bin",
      size: 3,
      media_type: "application/octet-stream",
      text: null,
      truncated: false,
      hex_preview: "00 FF 2A",
    });

    render(
      <WorkspaceFileViewer
        workspaceId="workspace-1"
        path="archive.bin"
        onClose={vi.fn()}
        onOpenFile={vi.fn()}
      />,
    );

    expect(await screen.findByText("00 FF 2A")).toBeTruthy();
  });

  it("ignores an obsolete preview failure after switching to a native viewer", async () => {
    let rejectPreview: ((reason?: unknown) => void) | undefined;
    vi.mocked(api.fetchWorkspaceFilePreview).mockImplementationOnce(
      () => new Promise((_resolve, reject) => {
        rejectPreview = reject;
      }),
    );
    const view = render(
      <WorkspaceFileViewer
        workspaceId="workspace-1"
        path="README.md"
        onClose={vi.fn()}
        onOpenFile={vi.fn()}
      />,
    );

    view.rerender(
      <WorkspaceFileViewer
        workspaceId="workspace-1"
        path="manual.pdf"
        onClose={vi.fn()}
        onOpenFile={vi.fn()}
      />,
    );
    await act(async () => rejectPreview?.(new Error("obsolete")));

    expect(screen.getByTitle("manual.pdf")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
