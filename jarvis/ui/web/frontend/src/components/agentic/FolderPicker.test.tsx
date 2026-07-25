import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/agenticIdeApi", () => ({
  fetchFolders: vi.fn(),
  searchFolders: vi.fn(),
  fetchRecents: vi.fn(),
  forgetRecent: vi.fn(),
  resolveDroppedFolder: vi.fn(),
}));

import { FolderPicker, extractDropPayload } from "./FolderPicker";
import * as api from "@/lib/agenticIdeApi";

const LISTING = {
  path: null,
  parent: null,
  entries: [
    { name: "webshop", path: "/home/ruben/webshop", is_project: true, is_repo: true },
    { name: "notes", path: "/home/ruben/notes", is_project: false, is_repo: false },
  ],
  device_name: "Rubens MacBook",
};

beforeEach(() => {
  vi.mocked(api.fetchFolders).mockResolvedValue(LISTING);
  vi.mocked(api.fetchRecents).mockResolvedValue({
    device_name: "Rubens MacBook",
    recents: [],
  });
  vi.mocked(api.searchFolders).mockResolvedValue({
    query: "",
    entries: [],
    truncated: false,
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

/** Minimal DataTransfer stand-in — jsdom has no real one. */
function dataTransfer(opts: {
  text?: string;
  uriList?: string;
  directoryName?: string;
  relativePath?: string;
}): DataTransfer {
  const items: unknown[] = [];
  if (opts.directoryName) {
    items.push({
      kind: "file",
      type: "",
      webkitGetAsEntry: () => ({ isDirectory: true, name: opts.directoryName }),
    });
  }
  const files = opts.relativePath
    ? [Object.assign(new File(["x"], "file.txt"), { webkitRelativePath: opts.relativePath })]
    : [];
  return {
    dropEffect: "none",
    getData: (type: string) =>
      type === "text/uri-list" ? (opts.uriList ?? "") : (opts.text ?? ""),
    items,
    files,
    types: ["Files"],
  } as unknown as DataTransfer;
}

describe("extractDropPayload", () => {
  it("prefers a real path from the URI list", () => {
    const payload = extractDropPayload(
      dataTransfer({ uriList: "file:///home/ruben/webshop\r\n" }),
    );
    expect(payload.path).toBe("file:///home/ruben/webshop");
  });

  it("falls back to plain text", () => {
    expect(extractDropPayload(dataTransfer({ text: "C:\\work\\shop" })).path).toBe(
      "C:\\work\\shop",
    );
  });

  it("takes the folder name when no path is offered", () => {
    // This is the normal browser case: a dropped directory exposes its NAME but
    // never its path, so the backend has to search for it.
    const payload = extractDropPayload(dataTransfer({ directoryName: "webshop" }));
    expect(payload.name).toBe("webshop");
    expect(payload.path).toBeUndefined();
  });

  it("derives the folder name from a dropped file inside it", () => {
    const payload = extractDropPayload(
      dataTransfer({ relativePath: "webshop/src/main.ts" }),
    );
    expect(payload.name).toBe("webshop");
  });

  it("returns nothing for an empty drop", () => {
    expect(extractDropPayload(dataTransfer({}))).toEqual({});
    expect(extractDropPayload(null)).toEqual({});
  });
});

describe("FolderPicker", () => {
  it("labels the machine by its own name, not the account folder", async () => {
    render(<FolderPicker selected={null} onSelect={vi.fn()} />);
    expect(await screen.findByText("Rubens MacBook")).toBeTruthy();
  });

  it("filters the open list locally on a single character", async () => {
    render(<FolderPicker selected={null} onSelect={vi.fn()} />);
    await screen.findByText("webshop");
    fireEvent.change(screen.getByTestId("folder-search"), { target: { value: "n" } });
    expect(screen.queryByText("webshop")).toBeNull();
    expect(screen.getByText("notes")).toBeTruthy();
    // Too short to bother the backend.
    expect(api.searchFolders).not.toHaveBeenCalled();
  });

  it("searches the machine once the query is long enough", async () => {
    vi.mocked(api.searchFolders).mockResolvedValue({
      query: "shop",
      entries: [
        { name: "old-webshop", path: "/archive/old-webshop", is_project: true, is_repo: false },
      ],
      truncated: false,
    });
    render(<FolderPicker selected={null} onSelect={vi.fn()} />);
    await screen.findByText("webshop");

    fireEvent.change(screen.getByTestId("folder-search"), { target: { value: "shop" } });
    await waitFor(() => expect(api.searchFolders).toHaveBeenCalledWith("shop"), {
      timeout: 2000,
    });
    // Results show their full path, since they can be anywhere on the machine.
    expect(await screen.findByText("/archive/old-webshop")).toBeTruthy();
  });

  it("offers recent workspaces and replays their layout", async () => {
    vi.mocked(api.fetchRecents).mockResolvedValue({
      device_name: "Rubens MacBook",
      recents: [
        {
          path: "/home/ruben/webshop",
          name: "webshop",
          terminals: 3,
          agents: { claude: 2, codex: 1 },
          last_used: 1,
          exists: true,
        },
      ],
    });
    const onSelect = vi.fn();
    const onSelectRecent = vi.fn();
    render(
      <FolderPicker selected={null} onSelect={onSelect} onSelectRecent={onSelectRecent} />,
    );

    const card = await screen.findByText("/home/ruben/webshop");
    fireEvent.click(card);
    expect(onSelect).toHaveBeenCalledWith("/home/ruben/webshop");
    expect(onSelectRecent).toHaveBeenCalledWith(
      expect.objectContaining({ terminals: 3, agents: { claude: 2, codex: 1 } }),
    );
  });

  it("resolves a dropped folder through the backend", async () => {
    vi.mocked(api.resolveDroppedFolder).mockResolvedValue({
      resolved: "/home/ruben/webshop",
      candidates: [],
      detail: "",
    });
    const onSelect = vi.fn();
    render(<FolderPicker selected={null} onSelect={onSelect} />);
    await screen.findByText("webshop");

    fireEvent.drop(screen.getByTestId("folder-drop-zone"), {
      dataTransfer: dataTransfer({ uriList: "file:///home/ruben/webshop" }),
    });

    await waitFor(() =>
      expect(api.resolveDroppedFolder).toHaveBeenCalledWith({
        path: "file:///home/ruben/webshop",
      }),
    );
    await waitFor(() => expect(onSelect).toHaveBeenCalledWith("/home/ruben/webshop"));
  });

  it("offers a choice when a dropped name matches several folders", async () => {
    vi.mocked(api.resolveDroppedFolder).mockResolvedValue({
      resolved: null,
      candidates: [
        { name: "webshop", path: "/a/webshop", is_project: true, is_repo: true },
        { name: "webshop", path: "/b/webshop", is_project: true, is_repo: false },
      ],
      detail: 'Several folders are called "webshop" — pick the right one.',
    });
    render(<FolderPicker selected={null} onSelect={vi.fn()} />);
    await screen.findByText("webshop");

    fireEvent.drop(screen.getByTestId("folder-drop-zone"), {
      dataTransfer: dataTransfer({ directoryName: "webshop" }),
    });

    expect(await screen.findByText(/pick the right one/i)).toBeTruthy();
    expect(await screen.findByText("/a/webshop")).toBeTruthy();
  });

  it("says so plainly when a drop carried nothing usable", async () => {
    render(<FolderPicker selected={null} onSelect={vi.fn()} />);
    await screen.findByText("webshop");

    fireEvent.drop(screen.getByTestId("folder-drop-zone"), {
      dataTransfer: dataTransfer({}),
    });

    expect(await screen.findByText(/carried no folder/i)).toBeTruthy();
    expect(api.resolveDroppedFolder).not.toHaveBeenCalled();
  });
});
