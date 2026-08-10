import { describe, expect, it } from "vitest";
import {
  WORKSPACE_PATH_TYPE,
  dragCarriesFiles,
  extractPaneDrop,
  extractPasteFiles,
  isEmptyPayload,
  nameClipboardFile,
} from "./paneDrop";

/** Minimal DataTransfer stand-in — jsdom has no real one. */
function dt(opts: {
  uriList?: string;
  text?: string;
  workspacePath?: string;
  files?: File[];
}): DataTransfer {
  return {
    getData: (type: string) => {
      if (type === "text/uri-list") return opts.uriList ?? "";
      if (type === WORKSPACE_PATH_TYPE) return opts.workspacePath ?? "";
      return opts.text ?? "";
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

describe("reading a drop onto a terminal pane", () => {
  it("takes the real path Explorer and Finder hand over", () => {
    const payload = extractPaneDrop(dt({ uriList: "file:///C:/work/shot.png\r\n" }));
    expect(payload.paths).toEqual(["C:/work/shot.png"]);
  });

  it("takes a POSIX path the same way", () => {
    expect(extractPaneDrop(dt({ text: "/home/ruben/shot.png" })).paths).toEqual([
      "/home/ruben/shot.png",
    ]);
  });

  it("takes a UNC path", () => {
    expect(extractPaneDrop(dt({ text: "\\\\nas\\share\\shot.png" })).paths).toEqual([
      "\\\\nas\\share\\shot.png",
    ]);
  });

  it("keeps the bytes when there is no path — a pasted screenshot has none", () => {
    const payload = extractPaneDrop(dt({ files: [file("image.png")] }));
    expect(payload.paths).toEqual([]);
    expect(payload.files.map((f) => f.name)).toEqual(["image.png"]);
  });

  it("does not send the same file twice when both a path and bytes arrive", () => {
    // A normal Explorer drag produces exactly this: a path AND a File object.
    const payload = extractPaneDrop(
      dt({ uriList: "file:///C:/work/shot.png", files: [file("shot.png")] }),
    );
    expect(payload.paths).toEqual(["C:/work/shot.png"]);
    expect(payload.files).toEqual([]);
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
      dt({ uriList: "file:///C:/a.png\r\nfile:///C:/b.png" }),
    );
    expect(payload.paths).toEqual(["C:/a.png", "C:/b.png"]);
  });

  it("survives a drop with nothing in it", () => {
    expect(isEmptyPayload(extractPaneDrop(null))).toBe(true);
    expect(isEmptyPayload(extractPaneDrop(dt({})))).toBe(true);
  });

  it("takes a row dragged out of the app's own explorer verbatim", () => {
    // A UNC path is the case the URI round-trip cannot survive, which is why
    // the in-app drag carries the path under its own type.
    const payload = extractPaneDrop(
      dt({ workspacePath: "\\\\nas\\share\\project\\docs\\plan.md" }),
    );
    expect(payload.paths).toEqual(["\\\\nas\\share\\project\\docs\\plan.md"]);
  });

  it("does not attach an explorer row twice when the drag also carries text", () => {
    // The explorer fills text/plain and text/uri-list too, for drop targets
    // outside this page. A pane must still see exactly one file.
    const payload = extractPaneDrop(
      dt({
        workspacePath: "C:\\work\\project\\README.md",
        uriList: "file:///C:/work/project/README.md",
        text: "C:\\work\\project\\README.md",
      }),
    );
    expect(payload.paths).toEqual(["C:\\work\\project\\README.md"]);
  });
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
