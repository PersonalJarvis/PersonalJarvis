/**
 * A fence in a Markdown deliverable always renders as a BLOCK — the defect
 * this pins is a fence with no language tag escaping as a bare `<code>` with
 * no `<pre>` around it, which folds every line break away and turns a drawn
 * table into one run-on line. An ASCII grid goes further and becomes a real
 * table, with the original art behind the Source switch.
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { MarkdownProse } from "@/components/outputs/MarkdownProse";

afterEach(() => cleanup());

const GRID = [
  "+------+----------+",
  "| ID   | Priority |",
  "+------+----------+",
  "| 01   | P1       |",
  "+------+----------+",
].join("\n");

function draw(text: string) {
  return render(<MarkdownProse slug="run" path="a/report.md" files={[]} text={text} />);
}

describe("fences in an artifact document", () => {
  it("keeps a fence with no language tag a block, not inline code", () => {
    const { container } = draw("```\nfirst line\nsecond line\n```\n");
    const pre = container.querySelector("pre");
    expect(pre).not.toBeNull();
    expect(pre?.textContent).toContain("first line\nsecond line");
    // The bare `<code>` the bug produced sat directly under the article.
    expect(container.querySelector("article > code")).toBeNull();
  });

  it("never writes react-markdown's syntax tree into the DOM", () => {
    const { container } = draw("`inline` and [a link](https://example.com)\n");
    expect(container.querySelector("[node]")).toBeNull();
  });

  it("draws an unfenced grid as a table", () => {
    draw(`## Matrix\n\n${GRID}\n`);
    expect(screen.getByRole("columnheader", { name: "Priority" })).toBeTruthy();
    expect(screen.getByRole("cell", { name: "01" })).toBeTruthy();
  });

  it("draws a plain-text fence holding a grid as a table", () => {
    draw(`\`\`\`text\n${GRID}\n\`\`\`\n`);
    expect(screen.getByTestId("ascii-table-fence")).toBeTruthy();
    expect(screen.getByRole("columnheader", { name: "ID" })).toBeTruthy();
  });

  it("shows the original art behind the Source switch", () => {
    draw(`\`\`\`\n${GRID}\n\`\`\`\n`);
    const fence = screen.getByTestId("ascii-table-fence");
    expect(fence.getAttribute("data-mode")).toBe("rendered");
    fireEvent.click(screen.getByRole("button", { name: "Source" }));
    expect(fence.getAttribute("data-mode")).toBe("source");
    expect(fence.textContent).toContain("+------+----------+");
  });

  it("leaves a code fence a code block", () => {
    draw("```python\nprint('hi')\n```\n");
    expect(screen.queryByTestId("ascii-table-fence")).toBeNull();
  });

  it("leaves a GFM table to Markdown", () => {
    const { container } = draw("| a | b |\n|---|---|\n| 1 | 2 |\n");
    expect(screen.queryByTestId("ascii-table-fence")).toBeNull();
    expect(container.querySelector("table")).not.toBeNull();
  });
});
