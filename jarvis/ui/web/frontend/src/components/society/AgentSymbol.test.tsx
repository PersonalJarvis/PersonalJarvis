import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { AgentSymbol } from "./AgentSymbol";
import { COMPANION_SHAPES } from "./companion/appearance";

afterEach(cleanup);

describe("symbol gaze and thinking", () => {
  it.each(COMPANION_SHAPES)("keeps %s idle with plain black eyes facing the same direction", shape => {
    const { container } = render(<AgentSymbol shape={shape} color="#8b5cf6" size={48} />);
    const eyes = container.querySelector("[data-agent-eyes]")!;
    expect(eyes.getAttribute("fill")).toBe("#101014");
    expect(eyes.getAttribute("transform")).toContain("translate(2 -0.6) rotate(-14");
    expect(eyes.querySelectorAll("ellipse")).toHaveLength(2);
    expect(eyes.querySelectorAll("circle")).toHaveLength(0);
    expect(container.querySelector("[data-thinking=true]")).toBeNull();
    expect(container.querySelector(".agent-symbol-orbit")).toBeNull();
  });

  it("restores the dot interlude without a rainbow and stops when work ends", () => {
    const { container, rerender } = render(<AgentSymbol shape="cloud" color="#8b5cf6" size={48} thinking />);
    expect(container.querySelector("[data-thinking=true]")).not.toBeNull();
    expect(container.querySelector(".agent-symbol-orbit")).toBeNull();
    expect(container.querySelectorAll(".agent-symbol-thoughts circle")).toHaveLength(3);
    expect(container.querySelector("[data-agent-body]")).not.toBeNull();
    expect(container.querySelectorAll("[data-agent-eyes] ellipse")).toHaveLength(2);
    rerender(<AgentSymbol shape="cloud" color="#8b5cf6" size={48} />);
    expect(container.querySelector("[data-thinking]")).toBeNull();
    expect(container.querySelector(".agent-symbol-orbit, .agent-symbol-thoughts")).toBeNull();
  });
});
