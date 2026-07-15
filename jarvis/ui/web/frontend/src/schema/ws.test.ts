import { describe, expect, it } from "vitest";
import { WSCommand, WSWelcome } from "./ws";

describe("WSCommand mission.inject", () => {
  it("validates a mission.inject command", () => {
    const parsed = WSCommand.parse({
      type: "command",
      action: "mission.inject",
      payload: { slug: "s", utterance: "u", status: "success" },
    });
    expect(parsed.action).toBe("mission.inject");
  });
});

describe("WSWelcome", () => {
  it("does not retain legacy session tokens", () => {
    const parsed = WSWelcome.parse({
      type: "welcome",
      session_id: "session-1",
      version: "1.0.0",
      token: "must-not-reach-the-client",
    });

    expect(parsed).toEqual({
      type: "welcome",
      session_id: "session-1",
      version: "1.0.0",
    });
    expect("token" in parsed).toBe(false);
  });
});
