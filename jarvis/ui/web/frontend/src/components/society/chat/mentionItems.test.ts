import { describe, expect, it } from "vitest";

import type { Capability, SocietyAgent } from "@/components/society/data";

import {
  buildMentionCatalog,
  filterMentions,
  groupMentions,
  mentionToken,
  mentionsInText,
} from "./mentionItems";

function agent(over: Partial<SocietyAgent> & Pick<SocietyAgent, "agentId" | "name">): SocietyAgent {
  return {
    title: "",
    description: "",
    tier: "specialist",
    provider: "",
    providerLabel: "",
    model: "",
    effort: "",
    figure: null,
    palette: { primary: "#000", secondary: "#000", accent: "#000" },
    grantMode: "all",
    toolGrants: [],
    focus: [],
    denies: [],
    approvalRules: { requireApproval: [], alwaysAllow: [] },
    permissionCeiling: "ask",
    dailyBudgetUsd: 0,
    checkpoint: "idle",
    state: "idle",
    lifecycle: "active",
    createdMs: 0,
    maxConcurrentRuns: 1,
    workspaceDir: "",
    wikiNamespace: "",
    chatSessionId: null,
    routines: [],
    stats: { runs: 0, totalCostUsd: 0, spentTodayUsd: 0, lastActiveMs: null },
    ...over,
  };
}

function cap(over: Partial<Capability> & Pick<Capability, "id">): Capability {
  const kind = (over.id.split(":")[0] || "plugin") as Capability["kind"];
  return {
    kind,
    label: over.id.replace(/^[^:]+:/, ""),
    one_liner: "",
    risk_tier: "monitor",
    connected: true,
    tool_name: over.id.replace(/^[^:]+:/, ""),
    ...over,
  };
}

describe("buildMentionCatalog", () => {
  it("tags a plugin as @gmail, not @plugin:gmail", () => {
    const items = buildMentionCatalog([], [cap({ id: "plugin:gmail", label: "gmail" })]);
    expect(items).toEqual([
      expect.objectContaining({
        value: "gmail",
        label: "Gmail",
        kind: "plugin",
        group: "plugins",
        pinIds: ["plugin:gmail"],
        detail: false,
      }),
    ]);
  });

  it("folds a connector's MCP tools and bundled skill into one plugin row", () => {
    const items = buildMentionCatalog(
      [],
      [
        cap({ id: "mcp:github/create_issue", label: "create_issue", one_liner: "Open an issue." }),
        cap({ id: "mcp:github/list_issues", label: "list_issues" }),
        cap({ id: "skill:plugin-github", label: "plugin-github", kind: "skill" }),
        cap({
          id: "plugin:agentic-ide-close-agent-terminals",
          label: "agentic-ide-close-agent-terminals",
          one_liner: "Stop a coding terminal.",
        }),
      ],
    );
    expect(items.map((i) => i.value)).toEqual(["github"]);
    const github = items.find((i) => i.key === "plugin:github")!;
    expect(github.label).toBe("GitHub");
    expect(github.group).toBe("plugins");
    expect(github.pinIds).toEqual([
      "mcp:github/create_issue",
      "mcp:github/list_issues",
      "skill:plugin-github",
    ]);
    expect(items.some((i) => i.kind === "skill")).toBe(false);
    expect(items.some((i) => i.value.includes("agentic-ide"))).toBe(false);
  });

  it("collapses a non-marketplace MCP server to one browse row and keeps the tools as detail", () => {
    const items = buildMentionCatalog(
      [],
      [
        cap({ id: "mcp:sentry/list_issues", label: "list_issues", one_liner: "List issues." }),
        cap({ id: "mcp:sentry/create_issue", label: "create_issue" }),
      ],
    );
    const browse = items.filter((i) => !i.detail);
    expect(browse.map((i) => i.value)).toEqual(["sentry"]);
    const sentry = items.find((i) => i.key === "mcp-server:sentry")!;
    expect(sentry.pinIds).toEqual(["mcp:sentry/list_issues", "mcp:sentry/create_issue"]);
    expect(items.filter((i) => i.detail).map((i) => i.value)).toEqual([
      "sentry/list_issues",
      "sentry/create_issue",
    ]);
  });

  it("shows YouTube Music once, with its skill folded in", () => {
    const items = buildMentionCatalog(
      [],
      [
        cap({
          id: "plugin:youtube_music",
          label: "youtube_music",
          one_liner: "Play a song.",
        }),
        cap({
          id: "skill:plugin-youtube_music",
          kind: "skill",
          label: "plugin-youtube_music",
          one_liner: "Control YouTube Music.",
        }),
      ],
    );
    expect(items).toEqual([
      expect.objectContaining({
        key: "plugin:youtube_music",
        value: "youtube-music",
        label: "YouTube Music",
        group: "plugins",
        pinIds: ["plugin:youtube_music", "skill:plugin-youtube_music"],
      }),
    ]);
  });

  it("keeps an agent's name even when a plugin would want the same tag", () => {
    const items = buildMentionCatalog(
      [agent({ agentId: "mail-bot", name: "gmail", title: "Inbox" })],
      [cap({ id: "plugin:gmail", label: "gmail" })],
    );
    expect(items.find((i) => i.kind === "agent")?.value).toBe("gmail");
    expect(items.find((i) => i.kind === "plugin")?.value).toBe("plugin:gmail");
  });
});

describe("filterMentions", () => {
  const items = buildMentionCatalog(
    [agent({ agentId: "scout", name: "Scout", title: "Research" })],
    [
      cap({ id: "plugin:gmail", label: "gmail", one_liner: "Read and send mail.", aliases: ["mail"] }),
      cap({ id: "plugin:notion", label: "notion", connected: false }),
      cap({ id: "mcp:github/create_issue", label: "create_issue" }),
      cap({ id: "mcp:github/list_issues", label: "list_issues" }),
      cap({ id: "mcp:sentry/create_issue", label: "create_issue" }),
      cap({ id: "mcp:sentry/list_issues", label: "list_issues" }),
      cap({ id: "core:search-web", label: "search-web", one_liner: "Search the web." }),
      cap({ id: "cli:gh", label: "gh" }),
    ],
  );

  it("on a bare @ lists connected browse rows, not disconnected plugins or MCP tools", () => {
    const values = filterMentions(items, "").map((i) => i.value);
    expect(values).toEqual(["Scout", "gmail", "github", "search-web", "gh", "sentry"]);
    expect(values).not.toContain("notion");
    expect(values).not.toContain("github/create_issue");
    expect(values).not.toContain("sentry/create_issue");
  });

  it("finds Gmail by @gmail, @mail and the catalog id", () => {
    expect(filterMentions(items, "gmail").map((i) => i.value)).toEqual(["gmail"]);
    expect(filterMentions(items, "mail").map((i) => i.value)).toEqual(["gmail"]);
    expect(filterMentions(items, "plugin:gmail").map((i) => i.value)).toEqual(["gmail"]);
  });

  it("finds a folded plugin by a tool name, and unfolds leftover MCP tools", () => {
    expect(filterMentions(items, "create_issue").map((i) => i.value)).toContain("github");
    expect(filterMentions(items, "github").map((i) => i.value)[0]).toBe("github");
    const sentry = filterMentions(items, "create");
    expect(sentry.map((i) => i.value)).toContain("sentry/create_issue");
    expect(filterMentions(items, "sentry").map((i) => i.value)[0]).toBe("sentry");
  });

  it("a disconnected plugin still appears once it is searched for", () => {
    expect(filterMentions(items, "notion").map((i) => i.value)).toEqual(["notion"]);
  });
});

describe("groupMentions / mentionsInText / mentionToken", () => {
  const items = buildMentionCatalog(
    [agent({ agentId: "scout", name: "Scout", title: "Research" })],
    [
      cap({ id: "plugin:gmail", label: "gmail" }),
      cap({ id: "mcp:github/create_issue" }),
      cap({ id: "mcp:github/list_issues" }),
    ],
  );

  it("groups in the picker order", () => {
    expect(groupMentions(filterMentions(items, "")).map((g) => g.group)).toEqual([
      "agents",
      "plugins",
    ]);
  });

  it("pins @gmail to the plugin id and @github to every GitHub capability", () => {
    const named = mentionsInText("please check @gmail and ping @github", items);
    expect(named.pinIds).toEqual([
      "plugin:gmail",
      "mcp:github/create_issue",
      "mcp:github/list_issues",
    ]);
    expect(named.agents).toEqual([]);
  });

  it("a longer leftover MCP tag is not also the server tag", () => {
    const catalog = buildMentionCatalog(
      [],
      [cap({ id: "mcp:sentry/create_issue" }), cap({ id: "mcp:sentry/list_issues" })],
    );
    const named = mentionsInText("open @sentry/create_issue", catalog);
    expect(named.pinIds).toEqual(["mcp:sentry/create_issue"]);
  });

  it("names an agent for delegation", () => {
    const named = mentionsInText("hand this to @Scout", items);
    expect(named.agents.map((a) => a.agentId)).toEqual(["scout"]);
  });

  it("opens on the @ token under the caret", () => {
    expect(mentionToken("see @gm", 7)).toEqual({ query: "gm", start: 4 });
    expect(mentionToken("see @gm next", 12)).toBeNull();
    expect(mentionToken("mail a@b.c", 10)).toBeNull();
  });
});
