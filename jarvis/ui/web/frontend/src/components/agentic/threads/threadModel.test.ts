import { describe, expect, it } from "vitest";
import type { AgentChatSession } from "@/lib/agentChatApi";
import type { IdeProject } from "@/lib/agenticIdeApi";
import { comparablePath, isInside, projectIdFor, shortAge, threadStatus, threadTitle, threadsByProject } from "./threadModel";

function project(id: string, path: string): IdeProject {
  return {
    id, path, name: id, color: null, pinned: false, archived: false, created_at: 0, last_opened_at: 0,
    exists: true, chats: 0, scratch: false, workspaces: [],
  } as IdeProject;
}

function session(id: string, cwd: string, extra: Partial<AgentChatSession> = {}): AgentChatSession {
  return {
    session_id: id, title: `title ${id}`, provider: "claude-api", model: "", effort: "", cwd, permission_mode: "",
    vendor_session: null, created_ms: 0, updated_ms: 0, message_count: 1, preview: "", ...extra,
  };
}

describe("thread paths", () => {
  it("compares Windows paths without case or separator drift", () => {
    expect(comparablePath("C:\\Users\\Me\\Repo\\")).toBe("c:/users/me/repo");
    expect(isInside("c:/users/me/repo/sub", "C:\\Users\\Me\\Repo")).toBe(true);
  });

  it("keeps POSIX paths case-sensitive and never matches a sibling prefix", () => {
    expect(isInside("/home/me/Repo", "/home/me/repo")).toBe(false);
    expect(isInside("/home/me/repo-two", "/home/me/repo")).toBe(false);
    expect(isInside("/home/me/repo", "/home/me/repo/")).toBe(true);
  });
});

describe("projectIdFor", () => {
  const projects = [project("outer", "/code"), project("inner", "/code/app")];

  it("picks the deepest project that holds the thread's folder", () => {
    expect(projectIdFor(session("a", "/code/app/src"), projects, {})).toBe("inner");
    expect(projectIdFor(session("b", "/code/lib"), projects, {})).toBe("outer");
    expect(projectIdFor(session("c", "/elsewhere"), projects, {})).toBeNull();
  });

  it("prefers the project a thread was started in (a worktree lives outside it)", () => {
    expect(projectIdFor(session("w", "/worktrees/app-fix"), projects, { w: "inner" })).toBe("inner");
    // A remembered project that is gone falls back to the folder.
    expect(projectIdFor(session("x", "/code/lib"), projects, { x: "deleted" })).toBe("outer");
  });

  it("groups threads per project, newest first", () => {
    const grouped = threadsByProject([
      session("old", "/code/app", { updated_ms: 1 }),
      session("new", "/code/app", { updated_ms: 5 }),
      session("stray", "/tmp"),
    ], projects, {});
    expect(grouped.get("inner")?.map((row) => row.session_id)).toEqual(["new", "old"]);
    expect([...grouped.keys()]).toEqual(["inner"]);
  });
});

describe("threadStatus", () => {
  it("puts a waiting approval before work and work before news", () => {
    const base = session("s", "/code", { updated_ms: 10 });
    expect(threadStatus({ ...base, running: true, pending_approvals: ["a"] }, {}, null)).toBe("approval");
    expect(threadStatus({ ...base, running: true }, {}, null)).toBe("running");
    expect(threadStatus(base, { s: 5 }, null)).toBe("unseen");
    expect(threadStatus(base, { s: 5 }, "s")).toBe("idle");
    // Never looked at in this browser: not news.
    expect(threadStatus(base, {}, null)).toBe("idle");
  });
});

describe("threadTitle", () => {
  it("uses the CLI's own title first, then the stored one", () => {
    expect(threadTitle({ title: "fix the login test please", cli_title: "Fix login test" })).toBe("Fix login test");
    expect(threadTitle({ title: "fix the login test please" })).toBe("fix the login test please");
    expect(threadTitle({ title: "  " })).toBe("New thread");
    expect(threadTitle(null)).toBe("New thread");
  });
});

describe("shortAge", () => {
  it("says how long ago in a list's shorthand", () => {
    const now = 1_000_000_000;
    expect(shortAge(now - 10_000, now)).toBe("now");
    expect(shortAge(now - 5 * 60_000, now)).toBe("5m");
    expect(shortAge(now - 3 * 3_600_000, now)).toBe("3h");
    expect(shortAge(now - 2 * 86_400_000, now)).toBe("2d");
    expect(shortAge(now - 15 * 86_400_000, now)).toBe("2w");
  });
});
