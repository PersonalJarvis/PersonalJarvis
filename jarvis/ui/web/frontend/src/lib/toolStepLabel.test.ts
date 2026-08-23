import { describe, expect, it } from "vitest";

import { describeToolStep, pickToolDetail } from "@/lib/toolStepLabel";

describe("describeToolStep", () => {
  it("turns Jarvis tools into a family verb plus the call's detail", () => {
    const wiki = describeToolStep("wiki-recall", { query: "Urlaub 2026" });
    expect(wiki.family).toBe("wiki");
    expect(wiki.labelKey).toBe("tool_steps.wiki");
    expect(wiki.detail).toBe("Urlaub 2026");
    expect(wiki.brand).toBeNull();

    expect(describeToolStep("create_artifact", { title: "Sales deck" })).toMatchObject({
      family: "artifact",
      labelKey: "tool_steps.artifact",
      detail: "Sales deck",
    });
    expect(describeToolStep("run-skill", { name: "daily-brief" })).toMatchObject({
      family: "skill",
      detail: "daily-brief",
    });
    expect(describeToolStep("search_web", { query: "weather" }).family).toBe("web");
    expect(describeToolStep("screenshot").family).toBe("screen");
    expect(describeToolStep("click_element", { selector: "OK" }).family).toBe("control");
    expect(describeToolStep("spawn_worker", { task: "refactor" })).toMatchObject({
      family: "worker",
      detail: "refactor",
    });
    expect(describeToolStep("run_shell", { command: "ls -la" })).toMatchObject({
      family: "shell",
      detail: "ls -la",
    });
  });

  it("treats 'server/tool' as an MCP call named after the server", () => {
    const github = describeToolStep("github/create_issue", { title: "Bug" });
    expect(github.family).toBe("mcp");
    expect(github.labelKey).toBeNull();
    expect(github.brand?.logoUrl).toBeTruthy();
    expect(github.label).toBe("GitHub · Create Issue");
    expect(github.detail).toBe("Bug");

    const custom = describeToolStep("my-server/do_thing");
    expect(custom.family).toBe("mcp");
    expect(custom.brand?.logoUrl).toBeFalsy();
    expect(custom.label).toBe("My Server · Do Thing");
  });

  it("lets a branded service tool speak through its logo and label", () => {
    const gmail = describeToolStep("gmail_search", { query: "invoices" });
    expect(gmail.family).toBe("service");
    expect(gmail.labelKey).toBeNull();
    expect(gmail.brand?.logoUrl).toBeTruthy();
    expect(gmail.label).toBe("Gmail · search");
    expect(gmail.detail).toBe("invoices");
  });

  it("never leaves an unknown tool nameless", () => {
    const odd = describeToolStep("frobnicate_widgets", { target: "x" });
    expect(odd.family).toBe("other");
    expect(odd.label).toBe("frobnicate widgets");
    expect(describeToolStep("").label).toBe("?");
  });
});

describe("pickToolDetail", () => {
  it("prefers the family's keys, clips long text, and falls back to any short string", () => {
    expect(pickToolDetail("web", { limit: 3, query: "x".repeat(100) })).toHaveLength(72);
    expect(pickToolDetail("web", { foo: "bar" })).toBe("bar");
    expect(pickToolDetail("web", { n: 2 })).toBe("");
    expect(pickToolDetail("web", null)).toBe("");
  });
});
