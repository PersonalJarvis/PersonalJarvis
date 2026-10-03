import { describe, expect, it } from "vitest";
import {
  collectDroppedFiles,
  collectPickedFiles,
  folderToDraft,
  frontmatterValues,
  MAX_SKILL_MD_BYTES,
  mcpServersMissingType,
} from "./packageFolder";

function namedFile(path: string, text: string) {
  return { path, file: new File([text], path.split("/").pop() ?? path) };
}

const PLUGIN_JSON = JSON.stringify({
  name: "todo-fox",
  version: "1.2.0",
  description: "Tasks and reminders from TodoFox",
});

const SKILL_MD = `---
name: todo-triage
description: Sort an inbox of tasks.
version: 1.0.0
---

Group open tasks by due date.
`;

function droppedTree() {
  let reads = 0;
  let directoryReads = 0;
  const file = (name: string, text = "unused") => ({
    name, isFile: true, isDirectory: false,
    file: (resolve: (value: File) => void) => {
      reads++;
      resolve(new File([text], name));
    },
  }) as unknown as FileSystemEntry;
  const directory = (name: string, children: FileSystemEntry[]) => ({
    name, isFile: false, isDirectory: true,
    createReader: () => {
      let offset = 0;
      return { readEntries: (resolve: (entries: FileSystemEntry[]) => void) => {
        directoryReads++;
        const batch = children.slice(offset, offset + 100);
        offset += 100;
        resolve(batch);
      } };
    },
  }) as unknown as FileSystemEntry;
  const drop = (...roots: FileSystemEntry[]) => roots.map((root) => ({
    webkitGetAsEntry: () => root,
  })) as unknown as DataTransferItemList;
  return { file, directory, drop, counts: () => ({ reads, directoryReads }) };
}

describe("folder import workload", () => {
  it("finds the package after dependency and unrelated files without opening them", async () => {
    const tree = droppedTree();
    const dependency = tree.directory("node_modules", Array.from({ length: 600 }, (_, i) => tree.file(`${i}.js`)));
    const root = tree.directory("todo-fox", [dependency,
      ...Array.from({ length: 600 }, (_, i) => tree.file(`${i}.txt`)),
      tree.file("plugin.json", PLUGIN_JSON),
      tree.directory("skills", [tree.directory("todo-triage", [tree.file("SKILL.md", SKILL_MD)])]),
    ]);
    const files = await collectDroppedFiles(tree.drop(root));
    const draft = await folderToDraft(files);
    expect(draft.kind).toBe("plugin");
    expect(draft.skills).toHaveLength(1);
    expect(tree.counts().reads).toBe(2);
    expect(tree.counts().directoryReads).toBeLessThan(15);
  });

  it("preserves a standalone Markdown file with a custom name", async () => {
    const tree = droppedTree();
    const files = await collectDroppedFiles(tree.drop(tree.file("Todo Triage.md", SKILL_MD)));
    expect((await folderToDraft(files)).name).toBe("todo-triage");
  });

  it("applies the same dependency filter to picked folders", async () => {
    const inputs = [
      namedFile("todo-fox/node_modules/dependency/plugin.json", '{"name":"wrong"}'),
      namedFile("todo-fox/plugin.json", PLUGIN_JSON),
      namedFile("todo-fox/notes.txt", "unused"),
    ];
    for (const input of inputs) Object.defineProperty(input.file, "webkitRelativePath", { value: input.path });
    const files = collectPickedFiles(inputs.map((input) => input.file) as unknown as FileList);
    expect(files).toHaveLength(1);
    expect((await folderToDraft(files)).name).toBe("todo-fox");
  });

  it("stops excessive nesting with an actionable error", async () => {
    const tree = droppedTree();
    let root = tree.file("SKILL.md", SKILL_MD);
    for (let i = 0; i < 35; i++) root = tree.directory(`level-${i}`, [root]);
    await expect(collectDroppedFiles(tree.drop(root))).rejects.toThrow(/deeply nested/i);
  });

  it("rejects oversized package selections without silently losing files", async () => {
    const tree = droppedTree();
    const root = tree.directory("many", Array.from({ length: 401 }, (_, i) =>
      tree.directory(`skill-${i}`, [tree.file("SKILL.md", SKILL_MD)])));
    await expect(collectDroppedFiles(tree.drop(root))).rejects.toThrow(/too many/i);
  });
});

describe("folderToDraft — the folder is the classification (publishing-plan.md §2)", () => {
  it("classifies a directory with plugin.json as a plugin, bundling its skills", async () => {
    const draft = await folderToDraft([
      namedFile("plugin.json", PLUGIN_JSON),
      namedFile(
        "mcp.json",
        '{"mcpServers":{"todo-fox":{"type":"streamable-http","url":"https://mcp.todofox.example/mcp"}}}',
      ),
      namedFile("skills/todo-triage/SKILL.md", SKILL_MD),
    ]);

    expect(draft.kind).toBe("plugin");
    expect(draft.name).toBe("todo-fox");
    expect(draft.version).toBe("1.2.0");
    expect(draft.skills).toEqual([{ name: "todo-triage", skill_md: SKILL_MD }]);
    expect(draft.warnings).toEqual([]);
  });

  it('warns when an mcp.json server has no "type" — the store requires it', async () => {
    const draft = await folderToDraft([
      namedFile("plugin.json", PLUGIN_JSON),
      namedFile("mcp.json", '{"mcpServers":{"todo-fox":{"url":"https://mcp.todofox.example/mcp"}}}'),
    ]);
    expect(draft.kind).toBe("plugin");
    expect(draft.warnings.some((w) => /missing "type"/.test(w))).toBe(true);
  });

  it("classifies a directory with only a SKILL.md as a skill", async () => {
    const draft = await folderToDraft([namedFile("todo-triage/SKILL.md", SKILL_MD)]);

    expect(draft.kind).toBe("skill");
    expect(draft.name).toBe("todo-triage");
    expect(draft.description).toBe("Sort an inbox of tasks.");
    expect(draft.skill_md).toBe(SKILL_MD);
  });

  it("rejects a folder with neither plugin.json nor SKILL.md instead of silently returning an empty draft", async () => {
    await expect(folderToDraft([namedFile("README.md", "hello")])).rejects.toThrow(
      /no plugin\.json and no skill\.md/i,
    );
  });

  it("reads a lone SKILL.md file as the skill — no wrapping folder required", async () => {
    const draft = await folderToDraft([namedFile("SKILL.md", SKILL_MD)]);
    expect(draft.kind).toBe("skill");
    expect(draft.name).toBe("todo-triage"); // from the frontmatter, not "SKILL"
    expect(draft.skill_md).toBe(SKILL_MD);
  });

  it("accepts a lone Markdown file under any name once it opens with frontmatter, naming it from the file", async () => {
    const noName = SKILL_MD.replace("name: todo-triage\n", "");
    const draft = await folderToDraft([namedFile("Todo Triage.md", noName)]);
    expect(draft.kind).toBe("skill");
    expect(draft.name).toBe("todo-triage");
    expect(draft.title).toBe("Todo Triage");
  });

  it("does not mistake a lone README.md without frontmatter for a skill", async () => {
    await expect(folderToDraft([namedFile("notes.md", "# just notes")])).rejects.toThrow(
      /named SKILL\.md or starts with YAML frontmatter/,
    );
  });

  it("warns instead of dropping bundled skills once the 10-skill cap is exceeded", async () => {
    const files = [namedFile("plugin.json", PLUGIN_JSON)];
    for (let i = 0; i < 11; i++) {
      files.push(namedFile(`skills/skill-${i}/SKILL.md`, SKILL_MD));
    }
    const draft = await folderToDraft(files);
    expect(draft.skills).toHaveLength(10);
    expect(draft.warnings.some((w) => /more than 10/i.test(w))).toBe(true);
  });

  it("warns when plugin.json is present but not valid JSON", async () => {
    const draft = await folderToDraft([namedFile("plugin.json", "{not json")]);
    expect(draft.kind).toBe("plugin");
    expect(draft.warnings.some((w) => /not valid json/i.test(w))).toBe(true);
  });
});

describe("frontmatterValues", () => {
  it("reads top-level string pairs from a SKILL.md header", () => {
    expect(frontmatterValues(SKILL_MD)).toMatchObject({
      name: "todo-triage",
      description: "Sort an inbox of tasks.",
      version: "1.0.0",
    });
  });
});

describe("MAX_SKILL_MD_BYTES", () => {
  it("mirrors the store's per-SKILL.md cap (jarvis/marketplace/publish.py MAX_SKILL_BYTES)", () => {
    expect(MAX_SKILL_MD_BYTES).toBe(64 * 1024);
  });
});

describe("mcpServersMissingType", () => {
  it("names every server missing a \"type\" key", () => {
    const text = JSON.stringify({
      mcpServers: {
        "todo-fox": { url: "https://mcp.todofox.example/mcp" },
        "todo-fox-local": { type: "stdio", command: "npx" },
      },
    });
    expect(mcpServersMissingType(text)).toEqual(["todo-fox"]);
  });

  it("returns nothing once every server declares its type", () => {
    const text = JSON.stringify({
      mcpServers: { "todo-fox": { type: "streamable-http", url: "https://mcp.todofox.example/mcp" } },
    });
    expect(mcpServersMissingType(text)).toEqual([]);
  });

  it("returns nothing for invalid JSON — the real error is reported elsewhere", () => {
    expect(mcpServersMissingType("{not json")).toEqual([]);
  });
});
