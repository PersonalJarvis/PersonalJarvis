import { describe, it, expect, vi } from "vitest";
import { render, fireEvent } from "@testing-library/react";

vi.mock("@/i18n", () => ({
  useT: () => (k: string) => k,
  useUiLanguage: () => "en",
}));

import { ToolTable } from "../ToolTable";
import type { ToolCall } from "../types";

const tools: ToolCall[] = [
  { name: "computer_use", caller: "router_tool", risk_tier: "monitor", approved_by: "whitelist",
    duration_ms: 420, exit_code: null, success: true, error_line: null,
    command: "open_app(name='settings')", output: "window focused" },
  { name: "open_app", caller: "", risk_tier: "safe", approved_by: null,
    duration_ms: null, exit_code: null, success: false,
    error_line: "application 'settings' not found",
    command: "", output: "" },
];

describe("ToolTable", () => {
  it("shows each tool with its caller, risk tier, approval and outcome", () => {
    const { container } = render(<ToolTable tools={tools} />);
    const text = container.textContent ?? "";
    expect(text).toContain("computer_use");
    expect(text).toContain("router_tool");
    expect(text).toContain("monitor");
    expect(text).toContain("whitelist");
    expect(text).toContain("fail");
  });

  it("reveals the command and the result on expand", () => {
    const { container, getByText } = render(<ToolTable tools={tools} />);
    // Collapsed: the captured I/O is not rendered yet.
    expect(container.textContent).not.toContain("window focused");
    fireEvent.click(getByText("computer_use"));
    expect(container.textContent).toContain("open_app(name='settings')");
    expect(container.textContent).toContain("window focused");
  });

  it("reveals a failed tool's reason on expand", () => {
    const { container, getByText } = render(<ToolTable tools={tools} />);
    fireEvent.click(getByText("open_app"));
    expect(container.textContent).toContain("application 'settings' not found");
  });

  it("renders the empty marker for no tools", () => {
    const { container } = render(<ToolTable tools={[]} />);
    expect(container.textContent).toContain("run_inspector.tools.empty");
  });
});
