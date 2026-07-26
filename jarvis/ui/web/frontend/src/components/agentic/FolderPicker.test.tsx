import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/agenticIdeApi", () => ({
  fetchFolders: vi.fn(),
  searchFolders: vi.fn(),
  fetchRecents: vi.fn(),
  forgetRecent: vi.fn(),
  resolveDroppedFolder: vi.fn(),
  fetchNativePickerSupport: vi.fn(),
  openNativePicker: vi.fn(),
}));

import {
  FolderPicker,
  extractDropPayload,
  joinPath,
  separatorOf,
  splitTypedPath,
} from "./FolderPicker";
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
  // The default is "this machine cannot show a folder window", which is what a
  // headless install and a remote browser both report — so every existing test
  // keeps asserting against the path that must work everywhere.
  vi.mocked(api.fetchNativePickerSupport).mockResolvedValue({ available: false });
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
    expect(screen.getByText("Recent folders")).toBeTruthy();
    expect(screen.getByTestId("recent-folder-star")).toBeTruthy();
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

describe("the system folder window", () => {
  it("is not offered where the machine cannot show one", async () => {
    // The default from the shared setup: a headless server, or a browser on a
    // different machine than the backend. Offering a button that cannot work
    // is worse than not offering one.
    render(<FolderPicker selected={null} onSelect={vi.fn()} />);
    await screen.findByText("webshop");
    expect(screen.queryByTestId("native-browse")).toBeNull();
  });

  it("hands the chosen folder straight to the wizard", async () => {
    vi.mocked(api.fetchNativePickerSupport).mockResolvedValue({
      available: true,
      backend: "powershell",
    });
    vi.mocked(api.openNativePicker).mockResolvedValue({ path: "C:\\work\\shop" });
    const onSelect = vi.fn();
    render(<FolderPicker selected={null} onSelect={onSelect} />);

    fireEvent.click(await screen.findByTestId("native-browse"));

    await waitFor(() => expect(onSelect).toHaveBeenCalledWith("C:\\work\\shop"));
    // And the in-page browser follows it there, so both views agree.
    expect(api.fetchFolders).toHaveBeenCalledWith("C:\\work\\shop");
  });

  it("says a window is waiting, because it can open behind the app", async () => {
    vi.mocked(api.fetchNativePickerSupport).mockResolvedValue({ available: true });
    let release: (value: { path: string }) => void = () => {};
    vi.mocked(api.openNativePicker).mockReturnValue(
      new Promise((resolve) => {
        release = resolve;
      }),
    );
    render(<FolderPicker selected={null} onSelect={vi.fn()} />);

    fireEvent.click(await screen.findByTestId("native-browse"));

    expect(await screen.findByText(/folder window is open/i)).toBeTruthy();
    release({ path: "/home/ruben/webshop" });
  });

  it("changes nothing when the window is closed without choosing", async () => {
    vi.mocked(api.fetchNativePickerSupport).mockResolvedValue({ available: true });
    vi.mocked(api.openNativePicker).mockResolvedValue({ cancelled: true });
    const onSelect = vi.fn();
    render(<FolderPicker selected="/already/here" onSelect={onSelect} />);

    fireEvent.click(await screen.findByTestId("native-browse"));

    await waitFor(() => expect(api.openNativePicker).toHaveBeenCalled());
    expect(onSelect).not.toHaveBeenCalled();
    // Cancelling is a decision, not a failure — nothing is reported.
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("opens the window at the folder already in view", async () => {
    vi.mocked(api.fetchNativePickerSupport).mockResolvedValue({ available: true });
    vi.mocked(api.openNativePicker).mockResolvedValue({ cancelled: true });
    render(<FolderPicker selected="/home/ruben/webshop" onSelect={vi.fn()} />);

    fireEvent.click(await screen.findByTestId("native-browse"));

    await waitFor(() =>
      expect(api.openNativePicker).toHaveBeenCalledWith("/home/ruben/webshop"),
    );
  });
});

describe("typing a path", () => {
  it("splits what was typed into a folder and what to match", () => {
    expect(splitTypedPath("/home/ruben/web")).toEqual({
      dir: "/home/ruben/",
      leaf: "web",
    });
    expect(splitTypedPath("C:\\work\\sh")).toEqual({ dir: "C:\\work\\", leaf: "sh" });
    // A bare name has no folder part — that is what makes `cd notes` work.
    expect(splitTypedPath("notes")).toEqual({ dir: "", leaf: "notes" });
  });

  it("reads the separator off a real path instead of guessing", () => {
    // The UI may well be open on a different machine than the backend, so
    // asking the browser which OS this is would answer about the wrong one.
    expect(separatorOf("C:\\work\\shop")).toBe("\\");
    expect(separatorOf("/home/ruben")).toBe("/");
    expect(joinPath("C:\\work", "shop")).toBe("C:\\work\\shop");
    expect(joinPath("/home/ruben/", "webshop")).toBe("/home/ruben/webshop");
  });

  it("completes against the folder on screen, the way cd does", async () => {
    vi.mocked(api.fetchFolders).mockResolvedValue({
      ...LISTING,
      path: "/home/ruben",
      parent: "/home",
    });
    render(<FolderPicker selected={null} onSelect={vi.fn()} />);
    await screen.findByText("webshop");

    fireEvent.change(screen.getByTestId("folder-path-input"), {
      target: { value: "web" },
    });

    const list = await screen.findByTestId("path-suggestions");
    await waitFor(() => expect(list.textContent).toContain("webshop"));
    // Only what matches: `notes` does not start with "web".
    expect(list.textContent).not.toContain("notes");
  });

  it("Tab completes and leaves the next segment ready to type", async () => {
    vi.mocked(api.fetchFolders).mockResolvedValue({
      ...LISTING,
      path: "/home/ruben",
      parent: "/home",
    });
    render(<FolderPicker selected={null} onSelect={vi.fn()} />);
    await screen.findByText("webshop");

    const input = screen.getByTestId("folder-path-input") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "web" } });
    await screen.findByTestId("path-suggestions");
    fireEvent.keyDown(input, { key: "Tab" });

    // The trailing separator is the point: typing continues into the folder.
    await waitFor(() => expect(input.value).toBe("/home/ruben/webshop/"));
  });

  it("treats a bare name as relative to the folder on screen", async () => {
    vi.mocked(api.fetchFolders).mockResolvedValue({
      ...LISTING,
      path: "/home/ruben",
      parent: "/home",
    });
    const onSelect = vi.fn();
    render(<FolderPicker selected={null} onSelect={onSelect} />);
    await screen.findByText("webshop");

    const input = screen.getByTestId("folder-path-input");
    fireEvent.change(input, { target: { value: "notes" } });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() => expect(onSelect).toHaveBeenCalledWith("/home/ruben/notes"));
  });

  it("uses a full path exactly as typed", async () => {
    const onSelect = vi.fn();
    render(<FolderPicker selected={null} onSelect={onSelect} />);
    await screen.findByText("webshop");

    const input = screen.getByTestId("folder-path-input");
    fireEvent.change(input, { target: { value: "/srv/deploy" } });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() => expect(onSelect).toHaveBeenCalledWith("/srv/deploy"));
  });
});
