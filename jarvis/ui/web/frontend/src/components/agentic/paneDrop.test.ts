import { afterEach, describe, expect, it, vi } from "vitest";
import {
  WORKSPACE_PATH_TYPE,
  dragCarriesFiles,
  extractPaneDrop,
  extractPasteFiles,
  isEmptyPayload,
  nameClipboardFile,
  setWorkspaceDragPaths,
} from "./paneDrop";

/** Minimal DataTransfer stand-in — jsdom has no real one. */
function dt(opts: {
  uriList?: string;
  text?: string;
  workspacePath?: string;
  files?: File[];
}): DataTransfer {
  const data = new Map<string, string>();
  return {
    setData: (type: string, value: string) => data.set(type, value),
    getData: (type: string) => {
      if (data.has(type)) return data.get(type)!;
      if (type === "text/uri-list") return opts.uriList ?? "";
      if (type === WORKSPACE_PATH_TYPE) return opts.workspacePath ?? "";
      return type === "text/plain" ? opts.text ?? "" : "";
    },
    files: opts.files ?? [],
    items: (opts.files ?? []).map((file) => ({
      kind: "file" as const,
      type: file.type,
      getAsFile: () => file,
    })),
  } as unknown as DataTransfer;
}

function file(name: string, type = "image/png", size = 4): File {
  return new File([new Uint8Array(size)], name, { type });
}

afterEach(() => {
  window.dispatchEvent(new Event("dragend"));
  vi.restoreAllMocks();
});

describe("reading a drop onto a terminal pane", () => {
  it.each([
    "file:///C:/private/secret.txt",
    "FILE:///C:/private/%73ecret.txt",
    "/home/person/private/secret.txt",
    "\\\\nas\\share\\secret.txt",
    "C:\\private\\secret.txt\r\nC:\\private\\other.txt",
  ])("rejects untrusted textual paths in every drag format: %s", (path) => {
    for (const data of [{ uriList: path }, { text: path }, { workspacePath: path }]) {
      expect(isEmptyPayload(extractPaneDrop(dt(data)))).toBe(true);
    }
  });

  it("keeps the bytes when there is no path — a pasted screenshot has none", () => {
    const payload = extractPaneDrop(dt({ files: [file("image.png")] }));
    expect(payload.paths).toEqual([]);
    expect(payload.files.map((f) => f.name)).toEqual(["image.png"]);
  });

  it("keeps granted file bytes and ignores a claimed path with the same name", () => {
    const payload = extractPaneDrop(
      dt({ uriList: "file:///C:/work/shot.png", files: [file("shot.png")] }),
    );
    expect(payload.paths).toEqual([]);
    expect(payload.files.map((f) => f.name)).toEqual(["shot.png"]);
  });

  it("ignores dragged prose, which also arrives as text/plain", () => {
    // Dragging a selected sentence must not have the backend try to read it
    // off the disk.
    const payload = extractPaneDrop(dt({ text: "please look at the wake code" }));
    expect(isEmptyPayload(payload)).toBe(true);
  });

  it("ignores a dropped directory, which arrives as an empty type-less entry", () => {
    const payload = extractPaneDrop(dt({ files: [file("src", "", 0)] }));
    expect(payload.files).toEqual([]);
  });

  it("reads several dropped files at once", () => {
    const payload = extractPaneDrop(
      dt({ files: [file("a.png"), file("b.png")] }),
    );
    expect(payload.files.map((f) => f.name)).toEqual(["a.png", "b.png"]);
  });

  it("survives a drop with nothing in it", () => {
    expect(isEmptyPayload(extractPaneDrop(null))).toBe(true);
    expect(isEmptyPayload(extractPaneDrop(dt({})))).toBe(true);
  });

  it("takes a row dragged out of the app's own explorer verbatim", () => {
    const transfer = dt({});
    setWorkspaceDragPaths(transfer, ["\\\\nas\\share\\project\\docs\\plan.md"]);
    const payload = extractPaneDrop(transfer);
    expect(payload.paths).toEqual(["\\\\nas\\share\\project\\docs\\plan.md"]);
  });

  it("does not attach an explorer row twice when the drag also carries text", () => {
    // The explorer fills text/plain and text/uri-list too, for drop targets
    // outside this page. A pane must still see exactly one file.
    const transfer = dt({
        uriList: "file:///C:/work/project/README.md",
        text: "C:\\work\\project\\README.md",
      });
    setWorkspaceDragPaths(transfer, ["C:\\work\\project\\README.md"]);
    const payload = extractPaneDrop(transfer);
    expect(payload.paths).toEqual(["C:\\work\\project\\README.md"]);
  });

  it("a receipt authorizes only the stored paths, once", () => {
    const transfer = dt({ text: "/private/secret", uriList: "file:///private/secret" });
    const paths = ["/project/selected.txt"];
    setWorkspaceDragPaths(transfer, paths);
    paths[0] = "/private/secret";
    expect(extractPaneDrop(transfer).paths).toEqual(["/project/selected.txt"]);
    expect(isEmptyPayload(extractPaneDrop(transfer))).toBe(true);
  });

  it("rejects a forged receipt without consuming a valid internal drag", () => {
    const transfer = dt({});
    setWorkspaceDragPaths(transfer, ["/project/selected.txt"]);
    const forged = dt({ workspacePath: "forged-receipt", files: [file("offered.png")] });
    expect(extractPaneDrop(forged).paths).toEqual([]);
    expect(extractPaneDrop(forged).files.map((f) => f.name)).toEqual(["offered.png"]);
    expect(extractPaneDrop(transfer).paths).toEqual(["/project/selected.txt"]);
  });

  it("a new explorer drag invalidates the previous receipt", () => {
    const old = dt({});
    const current = dt({});
    setWorkspaceDragPaths(old, ["/project/old.txt"]);
    setWorkspaceDragPaths(current, ["/project/current.txt"]);
    expect(isEmptyPayload(extractPaneDrop(old))).toBe(true);
    expect(extractPaneDrop(current).paths).toEqual(["/project/current.txt"]);
  });

  it("a detached window can consume the receipt exactly once", async () => {
    const transfer = dt({});
    setWorkspaceDragPaths(transfer, ["/project/selected.txt"]);
    vi.resetModules();
    const otherWindow = await import("./paneDrop");
    expect(otherWindow.extractPaneDrop(transfer).paths).toEqual(["/project/selected.txt"]);
    expect(isEmptyPayload(extractPaneDrop(transfer))).toBe(true);
  });

  it("expired and ended drags cannot authorize another read", () => {
    const transfer = dt({});
    setWorkspaceDragPaths(transfer, ["/project/selected.txt"]);
    vi.spyOn(Date, "now").mockReturnValue(Date.now() + 120_001);
    expect(isEmptyPayload(extractPaneDrop(transfer))).toBe(true);
    setWorkspaceDragPaths(transfer, ["/project/selected.txt"]);
    window.dispatchEvent(new Event("dragend"));
    expect(isEmptyPayload(extractPaneDrop(transfer))).toBe(true);
  });

  it("keeps same-page drags working when browser storage is blocked", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    const transfer = dt({});
    setWorkspaceDragPaths(transfer, ["/project/selected.txt"]);
    expect(extractPaneDrop(transfer).paths).toEqual(["/project/selected.txt"]);
    expect(isEmptyPayload(extractPaneDrop(transfer))).toBe(true);
  });

  it.each(["/project/one\n/private/secret", "/project/one\r/private/secret"])(
    "does not split an explorer filename into a second path: %s", (path) => {
      const transfer = dt({});
      expect(setWorkspaceDragPaths(transfer, [path])).toBe(false);
      expect(isEmptyPayload(extractPaneDrop(transfer))).toBe(true);
    },
  );
});

describe("deciding whether a drag in flight is worth offering a pane for", () => {
  /** A drag mid-flight exposes only its TYPES — never the data. */
  const inFlight = (types: string[]) =>
    ({ types }) as unknown as DataTransfer;

  it("recognises a file drag out of Explorer or Finder", () => {
    expect(dragCarriesFiles(inFlight(["Files"]))).toBe(true);
    expect(dragCarriesFiles(inFlight(["Files", "text/plain"]))).toBe(true);
  });

  it("recognises the uri-list some Linux file managers send instead", () => {
    expect(dragCarriesFiles(inFlight(["text/uri-list"]))).toBe(true);
  });

  it("recognises a row lifted out of the app's own explorer", () => {
    expect(dragCarriesFiles(inFlight([WORKSPACE_PATH_TYPE]))).toBe(true);
  });

  it("ignores dragged TEXT — nobody holding a selection is offering a file", () => {
    // BUG-110: brushing over terminal output with the mouse down lifts the
    // selection into a drag, and the pane announced "drop your file here" to a
    // user holding nothing.
    expect(dragCarriesFiles(inFlight(["text/plain"]))).toBe(false);
  });

  it("ignores an internal mission card tossed across the grid", () => {
    expect(dragCarriesFiles(inFlight(["application/x-jarvis-mission"]))).toBe(
      false,
    );
  });

  it("survives a drag with no DataTransfer at all", () => {
    expect(dragCarriesFiles(null)).toBe(false);
    expect(dragCarriesFiles({} as DataTransfer)).toBe(false);
  });
});

describe("reading a paste", () => {
  it("picks up a clipboard image", () => {
    expect(extractPasteFiles(dt({ files: [file("image.png")] })).length).toBe(1);
  });

  it("leaves a text paste to xterm", () => {
    // No files on the clipboard → nothing for us; xterm handles the text.
    expect(extractPasteFiles(dt({ text: "some text" }))).toEqual([]);
  });

  it("renames the generic clipboard name so screenshots stay distinguishable", () => {
    const renamed = nameClipboardFile(file("image.png"), "Kai");
    expect(renamed.name).toMatch(/^kai-paste-\d{8}-\d{6}\.png$/);
  });

  it("keeps a name the user actually chose", () => {
    expect(nameClipboardFile(file("design-review.png"), "Kai").name).toBe(
      "design-review.png",
    );
  });
});
