import { describe, expect, it } from "vitest";
import { withLeadName, type RosterData, type SocietyAgent } from "./data";

const roster = (): RosterData => ({
  sample: false,
  agents: [
    { agentId: "jarvis", name: "Jarvis", tier: "lead" } as SocietyAgent,
    { agentId: "gmail", name: "Mailer", tier: "specialist" } as SocietyAgent,
  ],
});

describe("withLeadName", () => {
  it("names the lead after the wake phrase and leaves others alone", () => {
    const names = withLeadName(roster(), "George").agents.map((a) => a.name);
    expect(names).toEqual(["George", "Mailer"]);
  });

  it("keeps the row name while only the neutral fallback is known", () => {
    const data = roster();
    expect(withLeadName(data, "Assistant")).toBe(data);
    expect(withLeadName(data, "  ")).toBe(data);
  });
});
