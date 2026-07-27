import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(resolve(process.cwd(), "src/index.css"), "utf8");
const component = readFileSync(
  resolve(process.cwd(), "src/components/agentic/AgenticTerminal.tsx"),
  "utf8",
);

describe("AgenticTerminal scrollbar", () => {
  it("styles the shared xterm viewport with a visible track and interactive thumb", () => {
    const host = ".agentic-terminal-host .xterm-viewport";

    expect(component).toContain('className="agentic-terminal-host ');
    expect(css).toContain(`${host} {`);
    expect(css).toContain("scrollbar-gutter: stable;");
    expect(css).toContain(`${host}::-webkit-scrollbar-track`);
    expect(css).toContain(`${host}::-webkit-scrollbar-thumb`);
    expect(css).toContain(`${host}::-webkit-scrollbar-thumb:hover`);
    expect(css).toContain(`${host}::-webkit-scrollbar-thumb:active`);
  });
});
