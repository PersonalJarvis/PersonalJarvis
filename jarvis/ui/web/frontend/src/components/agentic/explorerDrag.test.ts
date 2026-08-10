import { describe, expect, it } from "vitest";

import {
  absoluteWorkspacePath,
  setWorkspaceEntryDrag,
  workspaceFileUri,
} from "./explorerDrag";
import { WORKSPACE_PATH_TYPE, extractPaneDrop } from "./paneDrop";

/** Minimal DataTransfer stand-in — jsdom has no real one. */
function dataTransfer(): DataTransfer {
  const store = new Map<string, string>();
  return {
    setData: (type: string, value: string) => store.set(type, value),
    getData: (type: string) => store.get(type) ?? "",
    get types() {
      return [...store.keys()];
    },
    files: [],
  } as unknown as DataTransfer;
}

describe("joining a workspace-relative path onto its root", () => {
  it("uses backslashes for a Windows workspace", () => {
    expect(
      absoluteWorkspacePath("C:\\Users\\me\\Personal Jarvis", "docs/plans/x.md"),
    ).toBe("C:\\Users\\me\\Personal Jarvis\\docs\\plans\\x.md");
  });

  it("uses slashes for a POSIX workspace", () => {
    expect(absoluteWorkspacePath("/home/me/project", "docs/plans/x.md")).toBe(
      "/home/me/project/docs/plans/x.md",
    );
  });

  it("reads the separator off the SERVER's root, not this browser", () => {
    // The desktop app and a phone pointed at the same server render the same
    // page; only the root the server sent says which platform the files are on.
    expect(absoluteWorkspacePath("\\\\nas\\share\\project", "src/main.ts")).toBe(
      "\\\\nas\\share\\project\\src\\main.ts",
    );
  });

  it("does not double the separator when the root carries a trailing one", () => {
    expect(absoluteWorkspacePath("/home/me/project/", "README.md")).toBe(
      "/home/me/project/README.md",
    );
  });

  it("yields nothing when the root is not known yet", () => {
    expect(absoluteWorkspacePath("", "README.md")).toBe("");
  });
});

describe("offering the same path to applications outside this page", () => {
  it("keeps a Windows drive letter readable", () => {
    expect(workspaceFileUri("C:\\work\\Personal Jarvis\\a b.md")).toBe(
      "file:///C:/work/Personal%20Jarvis/a%20b.md",
    );
  });

  it("encodes a POSIX path", () => {
    expect(workspaceFileUri("/home/me/a b.md")).toBe("file:///home/me/a%20b.md");
  });

  it("keeps a UNC host in the authority position", () => {
    // `file:///nas/share/...` would name a local directory called "nas".
    expect(workspaceFileUri("\\\\nas\\share\\a.md")).toBe("file://nas/share/a.md");
  });
});

describe("lifting one explorer row into a drag", () => {
  it("arrives at a pane as exactly the path the row stands for", () => {
    const dt = dataTransfer();
    expect(
      setWorkspaceEntryDrag(dt, {
        root: "C:\\work\\Personal Jarvis",
        path: "docs/plans/x.md",
      }),
    ).toBe(true);
    // The pane's own reader is the assertion that matters — a format the drop
    // side does not understand is the same as no drag at all.
    expect(extractPaneDrop(dt).paths).toEqual([
      "C:\\work\\Personal Jarvis\\docs\\plans\\x.md",
    ]);
  });

  it("also speaks the two formats other applications understand", () => {
    const dt = dataTransfer();
    setWorkspaceEntryDrag(dt, { root: "/home/me/project", path: "src" });
    expect(dt.getData("text/uri-list")).toBe("file:///home/me/project/src");
    expect(dt.getData("text/plain")).toBe("/home/me/project/src");
    expect(dt.getData(WORKSPACE_PATH_TYPE)).toBe("/home/me/project/src");
  });

  it("carries a FOLDER as readily as a file", () => {
    const dt = dataTransfer();
    setWorkspaceEntryDrag(dt, { root: "/home/me/project", path: "docs/plans" });
    expect(extractPaneDrop(dt).paths).toEqual(["/home/me/project/docs/plans"]);
  });

  it("refuses the gesture when there is no root to join onto", () => {
    expect(setWorkspaceEntryDrag(dataTransfer(), { root: "", path: "a.md" })).toBe(
      false,
    );
  });
});
