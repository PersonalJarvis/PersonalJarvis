import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import gigiCompanionMark from "@/assets/gigi-companion-avatar.png";

import { AgentSwatch } from "./AgentSwatch";
import { symbolAppearance } from "./AgentSymbol";
import type { SocietyAgent } from "./data";

const agent: Pick<SocietyAgent, "agentId" | "tier" | "figure" | "palette" | "name"> = {
  agentId: "research-worker",
  tier: "specialist",
  name: "Research",
  figure: null,
  palette: { primary: "#111111", secondary: "#222222", accent: "#333333" },
};

describe("agent vector identities", () => {
  it("renders a symbol immediately without a portrait, image request or canvas", () => {
    const { container } = render(<AgentSwatch agent={agent} size={40} />);
    expect(container.querySelector("svg[data-agent-symbol]")).not.toBeNull();
    expect(container.querySelector("img, canvas")).toBeNull();
    expect(container.firstElementChild?.getAttribute("style")).toContain("width: 40px");
    expect(container.firstElementChild?.getAttribute("aria-hidden")).toBe("true");
  });

  it("keeps the same appearance on rename and figure edits", () => {
    const { container, rerender } = render(<AgentSwatch agent={agent} />);
    const before = container.querySelector("svg")?.outerHTML;
    rerender(<AgentSwatch agent={{ ...agent, name: "New name", figure: { contract: 1, archetype: "spirit", base: "gigi", parts: {}, palette: {} } }} />);
    expect(container.querySelector("svg")?.outerHTML).toBe(before);
    expect(container.querySelector("[data-agent-mascot]")).toBeNull();
  });

  it("uses the existing companion mark for the lead, even with an old figure recipe", () => {
    const { container } = render(<AgentSwatch agent={{ ...agent, tier: "lead", name: "Coordinator" }} size={56} />);
    expect(container.querySelector("[data-agent-mascot=gigi]")?.getAttribute("src")).toBe(gigiCompanionMark);
    expect(container.querySelector("[data-agent-symbol]")).toBeNull();
  });

  it("supports historical name-only participants and the legacy Gigi recipe", () => {
    const { container, rerender } = render(<AgentSwatch agent={{ name: "Worker", figure: null, palette: agent.palette }} />);
    expect(container.querySelector("svg")).not.toBeNull();
    rerender(<AgentSwatch agent={{ name: "Jarvis", palette: agent.palette, figure: { contract: 1, archetype: "spirit", base: "gigi", parts: {}, palette: {} } }} />);
    expect(container.querySelector("[data-agent-mascot=gigi]")).not.toBeNull();
  });

  it("distributes a roster across multiple silhouettes and colours deterministically", () => {
    const identities = Array.from({ length: 40 }, (_, i) => `agent-${i}`);
    const first = identities.map(symbolAppearance);
    expect(identities.map(symbolAppearance)).toEqual(first);
    expect(new Set(first.map(a => a.shape)).size).toBe(7);
    expect(new Set(first.map(a => a.color)).size).toBe(8);
  });
});
