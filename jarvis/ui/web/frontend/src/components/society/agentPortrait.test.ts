import { describe, expect, test } from "vitest";

import type { SocietyAgentRow } from "@/lib/societyApi";

import { agentPortraitUrl } from "./agentPortrait";
import { defaultFigureFor, rowToAgent } from "./data";

describe("agent portraits", () => {
  test("resolves the three compact built-in faces", () => {
    for (const key of ["bot-creator", "community-support", "morning-briefing"]) {
      expect(agentPortraitUrl(key)).toMatch(/\.webp$/);
    }
  });

  test("accepts bounded WebP data and rejects external or active image URLs", () => {
    expect(agentPortraitUrl("data:image/webp;base64,AAAA")).toBe("data:image/webp;base64,AAAA");
    expect(agentPortraitUrl("https://example.com/portrait.webp")).toBeNull();
    expect(agentPortraitUrl("data:image/svg+xml;base64,AAAA")).toBeNull();
  });

  test("a portrait on an empty avatar keeps the agent's world figure", () => {
    const id = "morning-briefing";
    const row = {
      agent_id: id,
      name: "Morning Briefing",
      tier: "specialist",
      state: "active",
      avatar: { portrait: "morning-briefing" },
    } as unknown as SocietyAgentRow;
    const agent = rowToAgent(row);
    expect(agent.figure?.base).toBe(defaultFigureFor(id, "specialist").base);
    expect(agent.figure?.palette).toEqual(defaultFigureFor(id, "specialist").palette);
    expect(agent.figure?.portrait).toBe("morning-briefing");
  });
});
