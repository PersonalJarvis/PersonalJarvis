import { describe, expect, it } from "vitest";
import { deriveSkillDescription, isMarkdownFile, readMarkdownFile, suggestSkillTitle } from "./skillImport";
import { splitSkillFrontmatter, skillIconFor, SKILL_ICON, compactCount } from "./skillVisuals";

const SKILL_MD = "---\nname: pr-review\ndescription: >-\n  Review a pull\n  request.\n---\n# Heading\n\nBody.";

describe("suggesting a skill's title", () => {
  it("prefers the frontmatter name, then the first heading", () => {
    expect(suggestSkillTitle(SKILL_MD)).toBe("pr-review");
    expect(suggestSkillTitle("intro\n\n## Write tests first ##\n")).toBe("Write tests first");
  });

  it("falls back to the file name, never to a bare SKILL.md", () => {
    expect(suggestSkillTitle("plain words", "house_style.md")).toBe("house style");
    expect(suggestSkillTitle("plain words", "SKILL.md")).toBe("plain words");
  });

  it("ignores a byte-order mark in front of the frontmatter", () => {
    expect(suggestSkillTitle(`${String.fromCharCode(0xfeff)}${SKILL_MD}`)).toBe("pr-review");
  });
});

describe("deriving a skill's summary", () => {
  it("reads a folded frontmatter description", () => {
    expect(deriveSkillDescription(SKILL_MD)).toBe("Review a pull request.");
  });

  it("takes the first prose line, skipping headings, fences and tables", () => {
    expect(deriveSkillDescription("# T\n\n```\ncode\n```\n| a |\n> **Lead** `line`\n")).toBe("Lead line");
  });
});

describe("reading an imported file", () => {
  it("accepts Markdown and text, normalises line endings", async () => {
    const file = new File(["# A\r\nB\r\n"], "a.md", { type: "" });
    expect(isMarkdownFile(file)).toBe(true);
    await expect(readMarkdownFile(file)).resolves.toBe("# A\nB\n");
  });

  it("refuses anything that is not text", async () => {
    await expect(readMarkdownFile(new File(["x"], "a.png", { type: "image/png" }))).rejects.toThrow("not a Markdown");
    await expect(readMarkdownFile(new File([`a${String.fromCharCode(0)}b`], "a.md"))).rejects.toThrow("not a text file");
  });
});

describe("how a skill looks", () => {
  it("splits frontmatter fields from the body", () => {
    const { fields, body } = splitSkillFrontmatter(SKILL_MD);
    expect(fields).toEqual([["name", "pr-review"]]);
    expect(body.startsWith("# Heading")).toBe(true);
  });

  it("picks a glyph from the words when left on auto", () => {
    expect(skillIconFor("auto", "Fix the login bug")).toBe(SKILL_ICON.bug);
    expect(skillIconFor("auto", "Something else")).toBe(SKILL_ICON.doc);
    expect(skillIconFor("auto", "Review the auth flow")).toBe(SKILL_ICON.shield);
    expect(skillIconFor("auto", "Plan the migration")).toBe(SKILL_ICON.data);
    expect(skillIconFor("auto", "Speed up the slow query", "Find out why and fix it")).toBe(SKILL_ICON.perf);
    expect(skillIconFor("rocket", "Fix the login bug")).toBe(SKILL_ICON.rocket);
  });

  it("keeps counts short", () => {
    expect(compactCount(999)).toBe("999");
    expect(compactCount(1200)).toBe("1.2k");
    expect(compactCount(12_400)).toBe("12k");
  });
});
