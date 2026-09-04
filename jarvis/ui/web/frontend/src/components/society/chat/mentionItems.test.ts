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
        kind: "plugin",
        group: "plugins",
        pinIds: ["plugin:gmail"],
        detail: false,
      }),
    ]);
  });

  it("collapses an MCP server to one browse row and keeps the tools as detail", () => {
    const items = buildMentionCatalog(
      [],
      [
        cap({ id: "mcp:github/create_issue", label: "create_issue", one_liner: "Open an issue." }),
        cap({ id: "mcp:github/list_issues", label: "list_issues" }),
        cap({ id: "mcp:sentry/list_issues", label: "list_issues", tool_name: "sentry/list_issues" }),
      ],
    );
    const browse = items.filter((i) => !i.detail);
    expect(browse.map((i) => i.value).sort()).toEqual(["github", "sentry"]);
    const github = items.find((i) => i.key === "mcp-server:github")!;
    expect(github.pinIds).toEqual(["mcp:github/create_issue", "mcp:github/list_issues"]);
    expect(items.filter((i) => i.detail).map((i) => i.value)).toEqual([
      "github/create_issue",
      "github/list_issues",
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
      cap({ id: "core:search-web", label: "search-web", one_liner: "Search the web." }),
      cap({ id: "cli:gh", label: "gh" }),
    ],
  );

  it("on a bare @ lists connected browse rows, not disconnected plugins or MCP tools", () => {
    const values = filterMentions(items, "").map((i) => i.value);
    expect(values).toEqual(["Scout", "gmail", "search-web", "gh", "github"]);
    expect(values).not.toContain("notion");
    expect(values).not.toContain("github/create_issue");
  });

  it("finds Gmail by @gmail, @mail and the catalog id", () => {
    expect(filterMentions(items, "gmail").map((i) => i.value)).toEqual(["gmail"]);
    expect(filterMentions(items, "mail").map((i) => i.value)).toEqual(["gmail"]);
    expect(filterMentions(items, "plugin:gmail").map((i) => i.value)).toEqual(["gmail"]);
  });

  it("unfolds MCP tools when the query names them, and still matches the server", () => {
    const create = filterMentions(items, "create");
    expect(create.map((i) => i.value)).toContain("github/create_issue");
    expect(filterMentions(items, "github").map((i) => i.value)[0]).toBe("github");
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
      "mcp",
    ]);
  });

  it("pins @gmail to the plugin id and @github to every GitHub MCP tool", () => {
    const named = mentionsInText("please check @gmail and ping @github", items);
    expect(named.pinIds).toEqual([
      "plugin:gmail",
      "mcp:github/create_issue",
      "mcp:github/list_issues",
    ]);
    expect(named.agents).toEqual([]);
  });

  it("a longer MCP tag is not also the server tag", () => {
    const named = mentionsInText("open @github/create_issue", items);
    expect(named.pinIds).toEqual(["mcp:github/create_issue"]);
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
