import { afterEach, describe, expect, it, vi } from "vitest";
import { getRepo, parseRepoRef, scanRepo } from "./githubImport";

function jsonResponse(body: unknown, status = 200) {
  return {
    status,
    ok: status >= 200 && status < 300,
    json: vi.fn().mockResolvedValue(body),
  };
}

describe("parseRepoRef", () => {
  it("accepts owner/repo", () => {
    expect(parseRepoRef("todofox/todo-fox")).toEqual({ owner: "todofox", repo: "todo-fox" });
  });

  it("accepts a full GitHub URL, stripping a trailing .git", () => {
    expect(parseRepoRef("https://github.com/todofox/todo-fox.git")).toEqual({
      owner: "todofox",
      repo: "todo-fox",
    });
  });

  it("rejects text that is neither", () => {
    expect(parseRepoRef("not a repo ref")).toBeNull();
  });
});

describe("getRepo", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("resolves the repo's real default branch, not the literal HEAD", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        full_name: "todofox/todo-fox",
        description: "Tasks and reminders",
        default_branch: "trunk",
        pushed_at: "2026-08-01T00:00:00Z",
        stargazers_count: 3,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const repo = await getRepo("todofox", "todo-fox");
    expect(repo.default_branch).toBe("trunk");
    expect(fetchMock).toHaveBeenCalledWith(
      "https://api.github.com/repos/todofox/todo-fox",
      expect.anything(),
    );
  });

  it("reports a readable message on a 404", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({}, 404)));
    await expect(getRepo("nobody", "nothing")).rejects.toThrow(/not found/i);
  });

  it("reports a readable message on a rate limit", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({}, 403)));
    await expect(getRepo("someone", "somewhere")).rejects.toThrow(/rate limit/i);
  });
});

describe("scanRepo", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("finds a plugin package with its bundled skills, and a standalone skill outside it", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({
          truncated: false,
          tree: [
            { path: "plugin.json", type: "blob" },
            { path: "mcp.json", type: "blob" },
            { path: "skills/todo-triage/SKILL.md", type: "blob" },
            { path: "standalone-skill/SKILL.md", type: "blob" },
            { path: "README.md", type: "blob" },
          ],
        }),
      ),
    );

    const { candidates, truncated } = await scanRepo("todofox", "todo-fox", "main");
    expect(truncated).toBe(false);
    const plugin = candidates.find((c) => c.kind === "plugin");
    expect(plugin?.paths).toEqual(
      expect.arrayContaining(["plugin.json", "mcp.json", "skills/todo-triage/SKILL.md"]),
    );
    const skill = candidates.find((c) => c.kind === "skill");
    expect(skill?.dir).toBe("standalone-skill");
  });

  it("surfaces a truncated tree instead of silently under-scanning", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({
          truncated: true,
          tree: [{ path: "README.md", type: "blob" }],
        }),
      ),
    );

    const { candidates, truncated } = await scanRepo("owner", "huge-repo", "main");
    expect(truncated).toBe(true);
    expect(candidates).toEqual([]);
  });
});
