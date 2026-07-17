import { describe, expect, it } from "vitest";

import type { SessionListItem } from "./types";
import { resolveSelectedSessionId } from "./sessionSelection";

function session(id: string, ended: boolean): SessionListItem {
  return {
    id,
    started_ms: 1_000,
    ended_ms: ended ? 2_000 : null,
    hangup_reason: ended ? "hotkey" : "",
    turn_count: ended ? 1 : 0,
    total_cost_usd: 0,
    total_tokens_in: 0,
    total_tokens_out: 0,
    providers_used: [],
    language: "en",
    wake_keyword: "hotkey",
    voice_mode: "realtime",
    duration_s: ended ? 1 : null,
    preview: ended ? "Hello" : "",
  };
}

describe("resolveSelectedSessionId", () => {
  it("keeps a selection that remains visible", () => {
    const sessions = [session("running", false), session("finished", true)];

    expect(resolveSelectedSessionId(sessions, "running")).toBe("running");
  });

  it("moves from a filtered empty attempt to the newest finished transcript", () => {
    const sessions = [session("running", false), session("finished", true)];

    expect(resolveSelectedSessionId(sessions, "filtered-empty")).toBe("finished");
  });

  it("falls back to a running session only when no finished transcript exists", () => {
    const sessions = [session("running", false)];

    expect(resolveSelectedSessionId(sessions, null)).toBe("running");
  });

  it("clears selection when no transcripts remain", () => {
    expect(resolveSelectedSessionId([], "filtered-empty")).toBeNull();
  });
});
