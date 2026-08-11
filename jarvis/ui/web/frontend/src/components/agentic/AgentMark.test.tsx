import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { AgentMark } from "./AgentMark";

afterEach(cleanup);

/**
 * How a brand mark survives BOTH themes.
 *
 * Every logo that ships with the app was drawn for the dark product this
 * started as, and on the light theme the restore screen showed seven terminals
 * with no sign of which CLI each was running (maintainer, 2026-08-11). The
 * marks are not all broken the same way, so what is pinned here is the routing:
 * which file gets redrawn in the theme's ink, which one keeps its colours and
 * gets the dark ground it was painted against, and which one is left alone.
 *
 * `data-ground` and `data-logo` are asserted rather than the DOM shape, because
 * an ink mark is a CSS mask and jsdom does not model mask properties — a test
 * that looked for an <img> would call the working marks broken.
 */
function mark(agent: string): HTMLElement {
  return screen.getByTestId(`agent-mark-${agent}`);
}

describe("AgentMark", () => {
  it("redraws a single-colour silhouette in the theme's own ink", () => {
    // Claude and OpenAI ship as one near-white fill. Nothing about that colour
    // is brand, so the mark is a mask and follows light/dark by itself.
    render(<AgentMark agent="claude" label="Claude Code" />);
    expect(mark("claude").getAttribute("data-ground")).toBe("ink");
    expect(mark("claude").getAttribute("data-logo")).toBe(
      "/provider-logos/claude.svg",
    );
    // The <img> is what made it invisible on paper; it must be gone.
    expect(mark("claude").querySelector("img")).toBeNull();
  });

  it("gives a lockup with a white background the dark ground it was drawn on", () => {
    // OpenCode is a white square with a dark inner shape. On paper the square
    // IS the paper and the mark collapsed to an unlabelled grey dot.
    render(<AgentMark agent="opencode" label="OpenCode" />);
    expect(mark("opencode").getAttribute("data-ground")).toBe("dark");
    expect(mark("opencode").querySelector("img")?.getAttribute("src")).toBe(
      "/agent-logos/opencode.svg",
    );
    expect(mark("opencode").className).toContain("bg-scrim");
  });

  it("leaves a lockup that carries its own ground untouched", () => {
    // Z.ai is already a dark tile with a white glyph, so it reads on either
    // theme — a canvas behind it would only be a box inside a box.
    render(<AgentMark agent="glm" label="GLM" />);
    expect(mark("glm").getAttribute("data-ground")).toBe("any");
    expect(mark("glm").querySelector("img")?.getAttribute("src")).toBe(
      "/agent-logos/zai.svg",
    );
    expect(mark("glm").className).not.toContain("bg-scrim");
  });

  it("does not guess a ground for a logo the user uploaded", () => {
    // We know nothing about the file; a canvas added on a guess could hide a
    // mark that was perfectly legible on its own.
    render(
      <AgentMark agent="mycli" label="My CLI" logoUrl="/uploads/mine.svg" />,
    );
    expect(mark("mycli").getAttribute("data-ground")).toBe("any");
    expect(mark("mycli").querySelector("img")?.getAttribute("src")).toBe(
      "/uploads/mine.svg",
    );
  });

  it("falls back to a monogram for a CLI with no mark at all", () => {
    render(<AgentMark agent="mystery" label="Mystery CLI" />);
    expect(mark("mystery").getAttribute("data-ground")).toBe("none");
    expect(mark("mystery").querySelector("img")).toBeNull();
    expect(mark("mystery").textContent).toBe("MY");
  });

  it("keeps the plain variant tile-free unless the mark needs a canvas", () => {
    const { unmount } = render(
      <AgentMark agent="claude" label="Claude Code" variant="plain" />,
    );
    expect(mark("claude").className).not.toContain("bg-scrim");
    unmount();

    render(<AgentMark agent="kimi" label="Kimi" variant="plain" />);
    expect(mark("kimi").className).toContain("bg-scrim");
  });
});
