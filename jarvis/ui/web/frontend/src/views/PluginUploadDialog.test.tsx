import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setUiLanguage } from "@/i18n";
import { PluginUploadDialog } from "@/views/PluginUploadDialog";

/**
 * A plugin the owner drops in has no publisher and no review behind it. The
 * dialog's job is to say that out loud while the decision is still open —
 * afterwards the card carries the mark, but by then the yes has happened.
 */

const READY_REPORT = {
  ready: true,
  problems: [],
  plugin: {
    id: "todo-fox",
    display_name: "TodoFox",
    description: "Tasks and reminders from TodoFox",
    category: "Lists & Tasks",
    auth_mode: "pat_paste",
    longevity: "permanent",
  },
  has_mcp: true,
  files: ["plugin.json", "mcp.json"],
  ignored: [],
  stripped_root: "todo-fox",
  total_bytes: 900,
  limits: { max_file_bytes: 10485760, max_total_bytes: 52428800, max_file_count: 2000 },
};

function jsonResponse(body: unknown, ok = true, status = 200): Response {
  return { ok, status, json: async () => body } as Response;
}

let fetchMock: ReturnType<typeof vi.fn>;

function stubFetch(responses: Record<string, unknown>) {
  fetchMock = vi.fn(async (url: string) => {
    const key = Object.keys(responses).find((candidate) => url.includes(candidate));
    if (!key) return jsonResponse({ detail: "unexpected call" }, false, 404);
    return jsonResponse(responses[key]);
  });
  (globalThis as unknown as { fetch: typeof fetch }).fetch =
    fetchMock as unknown as typeof fetch;
}

function withClient(node: ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{node}</QueryClientProvider>;
}

function pickFolder(files: File[]) {
  const input = screen.getByTestId("plugin-folder-input") as HTMLInputElement;
  Object.defineProperty(input, "files", { value: files, configurable: true });
  fireEvent.change(input);
}

function manifestFile(relativePath: string): File {
  const file = new File(["{}"], relativePath.split("/").pop()!);
  Object.defineProperty(file, "webkitRelativePath", { value: relativePath });
  return file;
}

beforeEach(() => {
  setUiLanguage("en");
  stubFetch({ "/upload/inspect": READY_REPORT });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("PluginUploadDialog", () => {
  it("asks for the manifest before anything else", () => {
    render(withClient(<PluginUploadDialog open onClose={() => {}} />));

    expect(screen.getByText("Drop a plugin folder here")).toBeTruthy();
    expect(screen.getByText("Install").closest("button")?.disabled).toBe(true);
  });

  it("shows the card the manifest will produce, and how it signs in", async () => {
    render(withClient(<PluginUploadDialog open onClose={() => {}} />));

    pickFolder([manifestFile("todo-fox/plugin.json"), manifestFile("todo-fox/mcp.json")]);

    await waitFor(() => expect(screen.getByText("TodoFox")).toBeTruthy());
    // The auth mode is the one thing an owner cannot discover afterwards
    // without connecting, so it belongs on the preview.
    expect(screen.getByText(/pat_paste/)).toBeTruthy();
    expect(screen.getByText(/with an MCP server/)).toBeTruthy();
  });

  it("says nobody reviewed this before the yes, not after", async () => {
    render(withClient(<PluginUploadDialog open onClose={() => {}} />));

    pickFolder([manifestFile("todo-fox/plugin.json")]);

    await waitFor(() => expect(screen.getByText(/nobody reviewed this plugin/)).toBeTruthy());
    expect(screen.getByText("Install").closest("button")?.disabled).toBe(false);
  });

  it("refuses to offer an install the server has blocked", async () => {
    stubFetch({
      "/upload/inspect": {
        ...READY_REPORT,
        ready: false,
        plugin: null,
        problems: ["plugin.json needs the Jarvis auth block"],
      },
    });
    render(withClient(<PluginUploadDialog open onClose={() => {}} />));

    pickFolder([manifestFile("plugin.json")]);

    await waitFor(() =>
      expect(screen.getByText("plugin.json needs the Jarvis auth block")).toBeTruthy(),
    );
    expect(screen.getByText("Install").closest("button")?.disabled).toBe(true);
    // No unreviewed note either: there is nothing to install to warn about.
    expect(screen.queryByText(/nobody reviewed this plugin/)).toBeNull();
  });

  it("installs and reports the id back", async () => {
    stubFetch({
      "/upload/inspect": READY_REPORT,
      "/api/marketplace/plugins/upload": {
        ok: true,
        plugin: { id: "todo-fox", display_name: "TodoFox", source: "local" },
      },
    });
    const onInstalled = vi.fn();
    const onClose = vi.fn();
    render(
      withClient(
        <PluginUploadDialog open onClose={onClose} onInstalled={onInstalled} />,
      ),
    );

    pickFolder([manifestFile("todo-fox/plugin.json")]);
    await waitFor(() => expect(screen.getByText("TodoFox")).toBeTruthy());

    fireEvent.click(screen.getByText("Install"));

    await waitFor(() => expect(onInstalled).toHaveBeenCalledWith("todo-fox"));
    expect(onClose).toHaveBeenCalled();
  });
});
