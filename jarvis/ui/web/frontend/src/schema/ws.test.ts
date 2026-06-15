import { describe, expect, it } from "vitest";
import { WSCommand } from "./ws";

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
