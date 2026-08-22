import { describe, expect, it } from "vitest";
import {
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
