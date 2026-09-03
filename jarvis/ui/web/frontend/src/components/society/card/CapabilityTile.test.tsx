import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test } from "vitest";

import { CapabilityTile } from "@/components/society/card/CapabilityTile";
import type { AgentPalette, Capability } from "@/components/society/data";

const PALETTE: AgentPalette = { primary: "#2f6f4f", secondary: "#8a5a3b", accent: "#ffd166" };

function capability(over: Partial<Capability>): Capability {
  return {
    id: "plugin:gmail",
    kind: "plugin",
    label: "Gmail",
    one_liner: "Read and send mail.",
    risk_tier: "monitor",
    connected: true,
    tool_name: "gmail",
    ...over,
  };
}

function tile(id: string) {
  return screen.getByTestId(`capability-tile-${id}`);
}

describe("CapabilityTile", () => {
  afterEach(() => cleanup());

  test("shows the service's real mark for a tool that has one", () => {
    render(<CapabilityTile id="plugin:gmail" capability={capability({})} palette={PALETTE} />);
    const img = tile("plugin:gmail").querySelector("img");
    // Vite inlines small SVGs as data URLs, so only "some svg" is stable.
    expect(img?.getAttribute("src")).toMatch(/svg/);
    expect(tile("plugin:gmail").textContent).toContain("Gmail");
  });

  test("a tool with no bundled mark gets its monogram on the agent's own colour, never a glyph", () => {
    render(
      <CapabilityTile
        id="core:run-shell"
        capability={capability({ id: "core:run-shell", kind: "core", label: "Run shell", tool_name: "run_shell" })}
        palette={PALETTE}
      />,
    );
    const el = tile("core:run-shell");
    expect(el.querySelector("img")).toBeNull();
    const mark = el.querySelector(".ac-tile-mark") as HTMLElement;
    expect(mark.style.background).toBeTruthy();
    expect(el.textContent).toContain("RS");
    // The one fallback the brand rules forbid: an emoji or other pictograph.
    expect(el.textContent ?? "").not.toMatch(/\p{Extended_Pictographic}/u);
  });

  test("names a capability the catalog does not know, from its id alone", () => {
    render(<CapabilityTile id="plugin:notion" palette={PALETTE} />);
    expect(tile("plugin:notion").textContent).toContain("Notion");
  });

  test("carries the risk tier, so 'why does this one ask me?' is on the tile", () => {
    render(
      <CapabilityTile id="plugin:gmail" capability={capability({ risk_tier: "ask" })} palette={PALETTE} />,
    );
    expect(tile("plugin:gmail").querySelector(".ac-tile-risk")?.getAttribute("data-risk")).toBe("ask");
  });

  test("a capability the catalog reports as absent is dimmed and says why", () => {
    render(
      <CapabilityTile
        id="plugin:gmail"
        capability={capability({ connected: false })}
        palette={PALETTE}
        disconnectedHint="not connected"
      />,
    );
    const el = tile("plugin:gmail");
    expect(el.style.opacity).toBe("0.5");
    expect(el.getAttribute("title")).toContain("not connected");
  });
});
