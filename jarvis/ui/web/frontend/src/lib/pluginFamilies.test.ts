import { describe, expect, it } from "vitest";

import {
  isPluginOwnedSkill,
  marketplacePluginId,
  pluginFamily,
} from "./pluginFamilies";

describe("marketplacePluginId", () => {
  it("maps the connector, its MCP tools and its bundled skill to one family", () => {
    expect(marketplacePluginId("plugin:gmail")).toBe("gmail");
    expect(marketplacePluginId("gmail")).toBe("gmail");
    expect(marketplacePluginId("plugin-gmail")).toBe("gmail");
    expect(marketplacePluginId("skill:plugin-gmail")).toBe("gmail");
    expect(marketplacePluginId("mcp:github/create_issue")).toBe("github");
    expect(marketplacePluginId("youtube_music/play")).toBe("youtube_music");
    expect(marketplacePluginId("youtube")).toBe("youtube_music");
  });

  it("does not invent a connector for coding commands or lookalike names", () => {
    expect(marketplacePluginId("plugin:agentic-ide-close-agent-terminals")).toBeUndefined();
    expect(marketplacePluginId("core:search-web")).toBeUndefined();
    expect(marketplacePluginId("cli:gh")).toBeUndefined();
    expect(marketplacePluginId("nonlinear_solver")).toBeUndefined();
    expect(marketplacePluginId("daily-brief")).toBeUndefined();
    expect(marketplacePluginId("mcp:sentry/list_issues")).toBeUndefined();
  });

  it("keeps a human name and @ tag per family", () => {
    expect(pluginFamily("youtube_music")).toEqual(
      expect.objectContaining({
        displayName: "YouTube Music",
        tag: "youtube-music",
      }),
    );
    expect(isPluginOwnedSkill("plugin-gmail")).toBe(true);
    expect(isPluginOwnedSkill("daily-brief")).toBe(false);
  });
});
