/** The inline formatting a chat answer actually uses. */
import { describe, expect, it } from "vitest";

import { renderInline } from "./assistantText";

function textOf(nodes: unknown[]): string {
  return nodes
    .map((n) =>
      typeof n === "string"
        ? n
        : ((n as { props?: { children?: string } }).props?.children ?? ""),
    )
    .join("");
}

describe("renderInline", () => {
  it("turns the model's emphasis into elements, never printed asterisks", () => {
    const nodes = renderInline("I propose **Gemma 4 12B** and ++Ornith 9B++ today.");
    expect(textOf(nodes)).toBe("I propose Gemma 4 12B and Ornith 9B today.");
    expect(nodes.filter((n) => typeof n !== "string")).toHaveLength(2);
  });

  it("keeps inline code as code and leaves plain prose alone", () => {
    const code = renderInline("Run `ollama pull qwen3.5` first.");
    expect(textOf(code)).toBe("Run ollama pull qwen3.5 first.");
    expect(renderInline("nothing to format")).toEqual(["nothing to format"]);
  });
});
