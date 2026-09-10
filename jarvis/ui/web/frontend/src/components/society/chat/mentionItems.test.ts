import { describe, expect, it } from "vitest";

import type { Capability, SocietyAgent } from "@/components/society/data";

import {
  buildMentionCatalog,
  codingMentionsInText,
  filterMentions,
  groupMentions,
  mentionToken,
  mentionsInText,
} from "./mentionItems";

it("uses short coding tags, retains legacy tags and lists unavailable CLIs honestly", () => {
  const coding = [
    { name: "codex", display_name: "Codex", installed: true, version: null, install_command: null },
    { name: "custom", display_name: "Custom CLI", installed: false, version: null, install_command: null },
  ];
  const catalog = buildMentionCatalog([], [], coding);
  expect(catalog[0].value).toBe("codex");
  expect(filterMentions(catalog, "").map((item) => item.label)).toEqual(["Codex", "Custom CLI"]);
  expect(filterMentions(catalog, "coding/").map((item) => item.label)).toEqual(["Codex", "Custom CLI"]);
  expect(codingMentionsInText("@codex fix this", catalog)).toHaveLength(1);
  expect(codingMentionsInText("@coding/codex fix this", catalog)).toHaveLength(1);
  expect(mentionsInText("@coding/codex fix this", catalog).pinIds).toEqual(["core:coding-session"]);
  const collision = buildMentionCatalog([agent({ agentId: "test-agent", name: "Codex" })], [], coding);
  expect(collision.find((item) => item.kind === "coding")?.value).toBe("coding/codex");
  expect(codingMentionsInText("@Codex hello", collision)).toHaveLength(0);
});

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
  it("keeps installed but disconnected community plugins searchable", () => {
    const catalog = buildMentionCatalog([], [], [], [{ id: "todo_fox", display_name: "Todo Fox", description: "Tasks" }]);
    expect(filterMentions(catalog, "")).toEqual([]);
    expect(filterMentions(catalog, "todo")[0]).toMatchObject({ connected: false, pinIds: [], label: "Todo Fox" });
  });
  it("loads a newly installed community plugin from the live catalog", () => {
    const items = buildMentionCatalog([], [cap({ id: "plugin:todo_fox_tool" })], [], [
      { id: "todo_fox", display_name: "Todo Fox", description: "Tasks", native_tool: "todo_fox_tool" },
    ]);
    expect(filterMentions(items, "")).toEqual([expect.objectContaining({
      key: "plugin:todo_fox", label: "Todo Fox", pinIds: ["plugin:todo_fox_tool"],
    })]);
  });

  it("does not hide independent skills or CLIs whose names mention a plugin", () => {
    const items = buildMentionCatalog([], [
      cap({ id: "skill:github-review" }), cap({ id: "cli:github" }),
      cap({ id: "mcp:research/github_search" }),
    ]);
    expect(items.map((item) => item.kind)).toEqual(["skill", "cli", "mcp"]);
  });

  it("allocates distinct tags even when both preferred tags are already taken", () => {
    const items = buildMentionCatalog([
      agent({ agentId: "one", name: "gmail" }),
      agent({ agentId: "two", name: "plugin:gmail" }),
      agent({ agentId: "three", name: "Research Assistant" }),
    ], [cap({ id: "plugin:gmail" })]);
    expect(new Set(items.map((item) => item.value.toLowerCase())).size).toBe(items.length);
    expect(items.every((item) => !/\s/.test(item.value))).toBe(true);
  });
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
  it("keeps all browse and search results in large catalogs", () => {
    const items = buildMentionCatalog([], Array.from({ length: 125 }, (_, i) => cap({ id: `skill:daily-${i}` })));
    expect(filterMentions(items, "")).toHaveLength(125);
    expect(filterMentions(items, "daily")).toHaveLength(125);
  });

  it("returns the same order the grouped picker displays for keyboard selection", () => {
    const items = buildMentionCatalog([], [cap({ id: "core:search-web" }), cap({ id: "cli:gh" }),
      cap({ id: "mcp:sentry/search" }), cap({ id: "skill:brief" })]);
    const matches = filterMentions(items, "");
    expect(matches.map((item) => item.key)).toEqual(groupMentions(matches).flatMap((group) => group.items.map((item) => item.key)));
  });
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
    expect(values).toEqual(["Scout", "gmail", "github", "sentry", "gh", "search-web"]);
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
