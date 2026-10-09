import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { extractPaneDrop, WORKSPACE_PATH_TYPE } from "@/components/agentic/paneDrop";
import { loadLocaleChunk } from "@/i18n";
import type { AppshotLibraryItem } from "@/lib/appshotApi";
import { useAppshotEditor } from "@/store/appshotEditor";
import { useEventStore } from "@/store/events";
import { AppshotLibrary } from "@/views/AppshotLibrary";

const ORIGINAL: AppshotLibraryItem = {
  id: "a1",
  variant: "original",
  path: "C:\\Jarvis\\data\\appshots\\a1\\appshot-20260921-120000.png",
  mime: "image/png",
  width: 1280,
  height: 720,
  label: "active window",
  app_name: "Editor",
  trigger: "hotkey",
  taken_at: 1_790_000_000,
  edited_at: 0,
  has_edit: true,
};
const EDITED: AppshotLibraryItem = {
  ...ORIGINAL,
  variant: "edited",
  path: "C:\\Jarvis\\data\\appshots\\a1\\appshot-20260921-120000-edited.png",
  edited_at: 1_790_000_100,
  has_edit: false,
};
const OTHER: AppshotLibraryItem = { ...ORIGINAL, id: "b2", has_edit: false, app_name: "Browser" };
const RECORDING: AppshotLibraryItem = {
  ...ORIGINAL, id: `recording_${"a".repeat(32)}`, mime: "video/mp4", has_edit: false,
  path: `C:\\Jarvis\\appshot-recordings\\${"a".repeat(32)}.mp4`,
  app_name: "", label: "Screen recording", duration_s: 61,
};

function json(body: unknown, status = 200): Response {
  return { ok: status < 400, status, json: async () => body } as Response;
}

class FakeDataTransfer {
  data = new Map<string, string>();
  effectAllowed = "";
  dropEffect = "";
  files = [] as unknown as FileList;
  get types() {
    return Array.from(this.data.keys());
  }
  setData(type: string, value: string) {
    this.data.set(type, value);
  }
  getData(type: string) {
    return this.data.get(type) ?? "";
  }
  setDragImage() {}
}

describe("AppshotLibrary", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  let items: AppshotLibraryItem[];

  beforeEach(async () => {
    await loadLocaleChunk("appshot_editor");
    useEventStore.setState({ events: [], toasts: [], assistantName: "Jarvis" });
    useAppshotEditor.setState({ openId: null, revision: 0 });
    items = [EDITED, ORIGINAL, OTHER];
    fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/api/appshot/library" && init?.method === "DELETE") {
        const removed = new Set(items.map((i) => i.id)).size;
        items = [];
        return json({ ok: true, removed });
      }
      if (url === "/api/appshot/library") return json({ items, max_entries: 500 });
      if (url.startsWith("/api/appshot/library/a1?variant=edited") && init?.method === "DELETE") {
        items = items.filter((i) => i !== EDITED);
        return json({ ok: true });
      }
      if (url === "/api/appshot/library/b2/open") return json({ id: "b2", window: false });
      if (url === `/api/appshot/library/${RECORDING.id}?variant=original` && init?.method === "DELETE") {
        items = items.filter((item) => item !== RECORDING);
        return json({ ok: true });
      }
      return json({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("shows two full rows first, then more and less on request", async () => {
    // Four columns fit: two rows are eight tiles.
    const realStyle = window.getComputedStyle.bind(window);
    vi.spyOn(window, "getComputedStyle").mockImplementation((element, pseudo) => {
      const style = realStyle(element, pseudo);
      if (element.tagName !== "UL") return style;
      return Object.create(style, { gridTemplateColumns: { value: "100px 100px 100px 100px" } });
    });
    items = Array.from({ length: 30 }, (_, index) => ({ ...ORIGINAL, id: `p${index}`, has_edit: false }));
    render(<AppshotLibrary enabled refreshKey="" />);
    await waitFor(() => expect(screen.getAllByTestId("appshot-library-tile")).toHaveLength(8));
    expect(screen.queryByTestId("appshot-library-less")).toBeNull();

    fireEvent.click(screen.getByTestId("appshot-library-more"));
    expect(screen.getAllByTestId("appshot-library-tile")).toHaveLength(30);
    expect(screen.queryByTestId("appshot-library-more")).toBeNull();

    fireEvent.click(screen.getByTestId("appshot-library-less"));
    expect(screen.getAllByTestId("appshot-library-tile")).toHaveLength(8);
    vi.restoreAllMocks();
  });

  it("shows every kept picture and filters to the edited ones", async () => {
    render(<AppshotLibrary enabled refreshKey="" />);
    await waitFor(() => expect(screen.getAllByTestId("appshot-library-tile")).toHaveLength(3));
    expect(screen.getByText("Edited", { selector: "span" })).toBeDefined();

    fireEvent.click(screen.getByTestId("appshot-library-filter-edited"));
    const tiles = screen.getAllByTestId("appshot-library-tile");
    expect(tiles).toHaveLength(1);
    expect(tiles[0].getAttribute("data-variant")).toBe("edited");
  });

  it("drags a tile as a real path the chat composer and the panes accept", async () => {
    render(<AppshotLibrary enabled refreshKey="" />);
    const [tile] = await screen.findAllByTestId("appshot-library-tile");
    const dt = new FakeDataTransfer();
    fireEvent.dragStart(tile, { dataTransfer: dt });

    expect(dt.getData(WORKSPACE_PATH_TYPE)).toMatch(/^[a-f0-9]{64}$/);
    expect(dt.getData(WORKSPACE_PATH_TYPE)).not.toContain(EDITED.path);
    expect(dt.getData("DownloadURL")).toMatch(/^image\/png:appshot-.*-edited\.png:http/);
    // The drop side (paneDrop, shared by panes and the composer) reads it back verbatim.
    expect(extractPaneDrop(dt as unknown as DataTransfer).paths).toEqual([EDITED.path]);
  });

  it("lifts the Settings dialog out of the way while a tile is dragged", async () => {
    render(<AppshotLibrary enabled refreshKey="" />);
    const [tile] = await screen.findAllByTestId("appshot-library-tile");
    fireEvent.dragStart(tile, { dataTransfer: new FakeDataTransfer() });
    await waitFor(() => expect(document.documentElement.dataset.appshotDrag).toBe("1"));
    fireEvent.dragEnd(tile);
    expect(document.documentElement.dataset.appshotDrag).toBeUndefined();
  });

  it("deletes only the edit from an edited tile", async () => {
    render(<AppshotLibrary enabled refreshKey="" />);
    await waitFor(() => expect(screen.getAllByTestId("appshot-library-tile")).toHaveLength(3));
    fireEvent.click(screen.getAllByTestId("appshot-library-delete")[0]);
    await waitFor(() => expect(screen.getAllByTestId("appshot-library-tile")).toHaveLength(2));
    expect(
      fetchMock.mock.calls.some(
        ([url, init]) => url === "/api/appshot/library/a1?variant=edited" && init?.method === "DELETE",
      ),
    ).toBe(true);
  });

  it("asks twice before deleting the whole history", async () => {
    render(<AppshotLibrary enabled refreshKey="" />);
    const clear = await screen.findByTestId("appshot-library-clear");
    fireEvent.click(clear);
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "DELETE")).toBe(false);
    fireEvent.click(screen.getByTestId("appshot-library-clear"));
    await waitFor(() => expect(screen.queryAllByTestId("appshot-library-tile")).toHaveLength(0));
  });

  it("opens a picture in the page's editor when no editor window can open", async () => {
    render(<AppshotLibrary enabled refreshKey="" />);
    const tiles = await screen.findAllByTestId("appshot-library-tile");
    fireEvent.click(tiles[2].querySelector("button")!);
    await waitFor(() => expect(useAppshotEditor.getState().openId).toBe("b2"));
  });

  it("says so when the history is switched off", async () => {
    items = [];
    render(<AppshotLibrary enabled={false} refreshKey="" />);
    await screen.findByText(/History is off/);
  });

  it("filters recordings, shows duration and plays a video without sending it to the image editor", async () => {
    items = [RECORDING, ...items];
    render(<AppshotLibrary enabled refreshKey="" />);
    await waitFor(() => expect(screen.getAllByTestId("appshot-library-tile")).toHaveLength(4));
    expect(screen.getByText("1:01")).toBeDefined();
    fireEvent.click(screen.getByTestId("appshot-library-filter-recordings"));
    const tiles = screen.getAllByTestId("appshot-library-tile");
    expect(tiles).toHaveLength(1);
    expect(tiles[0].getAttribute("data-media")).toBe("video");
    fireEvent.click(tiles[0].querySelector("button")!);
    const player = await screen.findByTestId("appshot-library-player");
    expect(player.getAttribute("src")).toContain(`/library/${RECORDING.id}/image`);
    expect(player.hasAttribute("controls")).toBe(true);
    expect(screen.getByRole("link", { name: "Download video" }).getAttribute("download")).toMatch(/\.mp4$/);
    expect(useAppshotEditor.getState().openId).toBeNull();
    expect(fetchMock.mock.calls.some(([url]) => url.endsWith("/open"))).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByTestId("appshot-library-player")).toBeNull());
  });

  it("shows only recordings, folded to two rows, without filters or delete-all", async () => {
    const more = Array.from({ length: 14 }, (_, n) => ({
      ...RECORDING, id: `recording_${String(n).padStart(32, "0")}`,
    }));
    items = [...more, ...items];
    render(<AppshotLibrary enabled={undefined} refreshKey="" recordingsOnly />);
    await waitFor(() => expect(screen.getAllByTestId("appshot-library-tile")).toHaveLength(12));
    expect(screen.getByTestId("appshot-recordings")).toBeDefined();
    for (const tile of screen.getAllByTestId("appshot-library-tile")) {
      expect(tile.getAttribute("data-media")).toBe("video");
    }
    expect(screen.queryByTestId("appshot-library-filter-all")).toBeNull();
    expect(screen.queryByTestId("appshot-library-clear")).toBeNull();
    fireEvent.click(screen.getByTestId("appshot-library-more"));
    expect(screen.getAllByTestId("appshot-library-tile")).toHaveLength(14);
  });

  it("drags a recording as an MP4 and deletes only that video", async () => {
    items = [RECORDING, ORIGINAL];
    render(<AppshotLibrary enabled refreshKey="" />);
    const [tile] = await screen.findAllByTestId("appshot-library-tile");
    const transfer = new FakeDataTransfer();
    fireEvent.dragStart(tile, { dataTransfer: transfer });
    expect(transfer.getData("DownloadURL")).toMatch(/^video\/mp4:.*\.mp4:http/);
    expect(transfer.getData(WORKSPACE_PATH_TYPE)).toMatch(/^[a-f0-9]{64}$/);
    expect(extractPaneDrop(transfer as unknown as DataTransfer).paths).toEqual([RECORDING.path]);
    fireEvent.dragEnd(tile);
    fireEvent.click(screen.getAllByTestId("appshot-library-delete")[0]);
    await waitFor(() => expect(screen.getAllByTestId("appshot-library-tile")).toHaveLength(1));
    expect(screen.getAllByTestId("appshot-library-tile")[0].getAttribute("data-media")).toBe("image");
  });
});
