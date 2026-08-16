import { describe, expect, it } from "vitest";

import { collectDroppedFiles, filesFromInput, formatBytes } from "@/lib/filePicking";

/**
 * Getting a *folder* out of a browser is the awkward part of this feature, and
 * the two roads in behave nothing alike: a file input hands over a flat list
 * with paths tucked into a non-standard property, while a drop hands over an
 * entry tree behind a callback API — one that returns at most 100 children per
 * call and signals the end with an empty batch.
 *
 * Both have to arrive as the same `PickedFile[]`, or the server sees a folder
 * as a pile of loose files.
 */

function fakeFile(name: string, relativePath?: string): File {
  const file = new File(["x"], name);
  if (relativePath !== undefined) {
    Object.defineProperty(file, "webkitRelativePath", { value: relativePath });
  }
  return file;
}

function fileList(files: File[]): FileList {
  return files as unknown as FileList;
}

// ----------------------------------------------------------------------
// A file input
// ----------------------------------------------------------------------

describe("filesFromInput", () => {
  it("uses the relative path a directory pick provides", () => {
    const picked = filesFromInput(
      fileList([
        fakeFile("SKILL.md", "my-skill/SKILL.md"),
        fakeFile("guide.md", "my-skill/references/guide.md"),
      ]),
    );

    expect(picked.map((entry) => entry.path)).toEqual([
      "my-skill/SKILL.md",
      "my-skill/references/guide.md",
    ]);
  });

  it("falls back to the bare name for a single file", () => {
    const picked = filesFromInput(fileList([fakeFile("bundle.zip")]));
    expect(picked).toHaveLength(1);
    expect(picked[0].path).toBe("bundle.zip");
  });

  it("returns nothing when the pick was cancelled", () => {
    expect(filesFromInput(null)).toEqual([]);
  });
});

// ----------------------------------------------------------------------
// A drop
// ----------------------------------------------------------------------

type EntryStub = {
  isFile: boolean;
  isDirectory: boolean;
  name: string;
  fullPath?: string;
  file?: (onOk: (file: File) => void) => void;
  createReader?: () => {
    readEntries: (onOk: (entries: EntryStub[]) => void) => void;
  };
};

function fileEntry(name: string, fullPath: string): EntryStub {
  return {
    isFile: true,
    isDirectory: false,
    name,
    fullPath,
    file: (onOk) => onOk(fakeFile(name)),
  };
}

/** A directory that hands out its children in batches, like the real API. */
function dirEntry(name: string, fullPath: string, batches: EntryStub[][]): EntryStub {
  let call = 0;
  return {
    isFile: false,
    isDirectory: true,
    name,
    fullPath,
    createReader: () => ({
      readEntries: (onOk) => {
        const batch = batches[call] ?? [];
        call += 1;
        onOk(batch);
      },
    }),
  };
}

function dataTransfer(entries: EntryStub[], files: File[] = []): DataTransfer {
  return {
    items: entries.map((entry) => ({
      webkitGetAsEntry: () => entry,
      getAsFile: () => null,
    })),
    files,
  } as unknown as DataTransfer;
}

describe("collectDroppedFiles", () => {
  it("walks a dropped folder down to the leaves", async () => {
    const tree = dirEntry("my-skill", "/my-skill", [
      [
        fileEntry("SKILL.md", "/my-skill/SKILL.md"),
        dirEntry("references", "/my-skill/references", [
          [fileEntry("guide.md", "/my-skill/references/guide.md")],
          [],
        ]),
      ],
      [],
    ]);

    const picked = await collectDroppedFiles(dataTransfer([tree]));

    expect(picked.map((entry) => entry.path)).toEqual([
      "my-skill/SKILL.md",
      "my-skill/references/guide.md",
    ]);
  });

  it("keeps reading until a batch comes back empty", async () => {
    // The real readEntries caps a batch at 100 children. A single call would
    // silently truncate a large folder — the kind of loss nobody notices
    // until a skill installs without half its files.
    const tree = dirEntry("big", "/big", [
      [fileEntry("a.md", "/big/a.md")],
      [fileEntry("b.md", "/big/b.md")],
      [],
    ]);

    const picked = await collectDroppedFiles(dataTransfer([tree]));

    expect(picked.map((entry) => entry.path)).toEqual(["big/a.md", "big/b.md"]);
  });

  it("takes plain files when the browser exposes no entries", async () => {
    const transfer = {
      items: [{ webkitGetAsEntry: () => null, getAsFile: () => fakeFile("bundle.zip") }],
      files: [],
    } as unknown as DataTransfer;

    const picked = await collectDroppedFiles(transfer);

    expect(picked).toHaveLength(1);
    expect(picked[0].path).toBe("bundle.zip");
  });

  it("returns nothing for an empty drop", async () => {
    expect(await collectDroppedFiles(null)).toEqual([]);
  });
});

describe("formatBytes", () => {
  it("speaks in the units a person reads", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(2048)).toBe("2.0 KB");
    expect(formatBytes(5 * 1024 * 1024)).toBe("5.0 MB");
  });
});
