import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { ToolTable } from "../ToolTable";
import type { ToolCall } from "../types";

const tools: ToolCall[] = [
  { name: "computer_use", caller: "", risk_tier: "monitor", approved_by: null,
    duration_ms: null, exit_code: null, success: true, error_line: null },
  { name: "open_app", caller: "", risk_tier: "safe", approved_by: null,
    duration_ms: null, exit_code: null, success: false,
    error_line: "Anwendung 'settings' nicht gefunden" },
];

describe("ToolTable", () => {
  it("shows a failed tool as 'fail' and surfaces the reason", () => {
    const { container } = render(<ToolTable tools={tools} />);
    expect(container.textContent).toContain("computer_use");
    expect(container.textContent).toContain("fail");
    expect(container.textContent).toContain("settings' nicht gefunden");
  });

  it("renders the empty marker for no tools", () => {
    const { container } = render(<ToolTable tools={[]} />);
    expect(container.textContent).toContain("—");
  });
});
