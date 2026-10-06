import { beforeEach, describe, expect, it } from "vitest";

import { reloadHeld } from "@/lib/reloadHold";
import { fileKeyOf, tabKeyOf, tabsOf, useCodeEditorStore } from "./codeEditor";

const store = () => useCodeEditorStore.getState();
const paths = () => store().tabs.map((tab) => `${tab.path}${tab.preview ? "*" : ""}`);

beforeEach(() => {
  useCodeEditorStore.setState({ tabs: [], active: {}, files: {}, visible: false, quickOpen: false, cursor: null, reveal: null, closed: [] });
});

describe("code editor tabs", () => {
  it("lets a single-click preview tab be replaced by the next one", () => {
    store().openFile("w1", "a.ts");
    store().openFile("w1", "b.ts");
    expect(paths()).toEqual(["b.ts*"]);
    expect(store().files[fileKeyOf("w1", "a.ts")]).toBeUndefined();
    expect(store().visible).toBe(true);
  });

  it("keeps a pinned tab and opens the next one beside it", () => {
    store().openFile("w1", "a.ts", { preview: false });
    store().openFile("w1", "b.ts");
    store().openFile("w1", "c.ts");
    expect(paths()).toEqual(["a.ts", "c.ts*"]);
  });

  it("pins a preview tab once its buffer has unsaved edits", () => {
    store().openFile("w1", "a.ts");
    store().patchFile(fileKeyOf("w1", "a.ts"), { dirty: true });
    store().openFile("w1", "b.ts");
    expect(paths()).toEqual(["a.ts", "b.ts*"]);
    expect(reloadHeld()).toBe(true);
    store().patchFile(fileKeyOf("w1", "a.ts"), { dirty: false });
    expect(reloadHeld()).toBe(false);
  });

  it("shows the right neighbour after closing the active tab, and can reopen it", () => {
    for (const path of ["a.ts", "b.ts", "c.ts"]) store().openFile("w1", path, { preview: false });
    store().activate(tabKeyOf("edit", fileKeyOf("w1", "a.ts")));
    store().closeTab(tabKeyOf("edit", fileKeyOf("w1", "a.ts")));
    expect(store().active.w1).toBe(tabKeyOf("edit", fileKeyOf("w1", "b.ts")));
    store().closeTabs([tabKeyOf("edit", fileKeyOf("w1", "b.ts")), tabKeyOf("edit", fileKeyOf("w1", "c.ts"))]);
    expect(store().active.w1).toBeNull();
    store().reopenClosed("w1");
    expect(paths()).toEqual(["c.ts"]);
  });

  it("keeps an edit tab and a diff tab of the same file apart", () => {
    store().openFile("w1", "a.ts", { preview: false });
    store().openFile("w1", "a.ts", { mode: "diff", preview: false });
    expect(store().tabs.map((tab) => tab.mode)).toEqual(["edit", "diff"]);
    expect(Object.keys(store().files)).toEqual([fileKeyOf("w1", "a.ts")]);
  });

  it("cycles through one workspace's tabs only", () => {
    store().openFile("w1", "a.ts", { preview: false });
    store().openFile("w2", "x.ts", { preview: false });
    store().openFile("w1", "b.ts", { preview: false });
    store().cycle("w1", 1);
    expect(store().active.w1).toBe(tabKeyOf("edit", fileKeyOf("w1", "a.ts")));
    expect(tabsOf(store(), "w2").map((tab) => tab.path)).toEqual(["x.ts"]);
  });

  it("moves open files along with a folder rename and marks deleted ones", () => {
    store().openFile("w1", "src/a.ts", { preview: false });
    store().openFile("w1", "srcx/b.ts", { preview: false });
    store().renamePath("w1", "src", "lib");
    expect(paths()).toEqual(["lib/a.ts", "srcx/b.ts"]);
    expect(store().active.w1).toBe(tabKeyOf("edit", fileKeyOf("w1", "srcx/b.ts")));
    store().markDeleted("w1", "lib");
    expect(store().files[fileKeyOf("w1", "lib/a.ts")]).toMatchObject({ deleted: true, version: null });
    expect(store().files[fileKeyOf("w1", "srcx/b.ts")].deleted).toBe(false);
  });

  it("restores last run's tabs without bringing the editor forward", () => {
    store().openFile("w1", "open.ts", { preview: false });
    useCodeEditorStore.setState({ visible: false });
    store().restoreTabs(
      "w1",
      [
        { path: "open.ts", mode: "edit", preview: false },
        { path: "b.md", mode: "diff", preview: true },
      ],
      "b.md",
    );
    expect(paths()).toEqual(["open.ts", "b.md*"]);
    expect(store().visible).toBe(false);
    // A tab that was already active stays active.
    expect(store().active.w1).toBe(tabKeyOf("edit", fileKeyOf("w1", "open.ts")));

    store().restoreTabs("w2", [{ path: "x.ts", mode: "edit", preview: false }], "x.ts");
    expect(store().active.w2).toBe(tabKeyOf("edit", fileKeyOf("w2", "x.ts")));
  });

  it("asks the editor to reveal a line", () => {
    store().openFile("w1", "a.ts", { line: 42, column: 3 });
    expect(store().reveal).toMatchObject({ fileKey: fileKeyOf("w1", "a.ts"), line: 42, column: 3 });
  });
});
