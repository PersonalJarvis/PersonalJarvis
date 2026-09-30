import { useState } from "react";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AgentStatus } from "@/lib/agenticIdeApi";
import { WorkspaceAgentSetup } from "./WorkspaceAgentSetup";

const agents: AgentStatus[] = [
  { name: "claude", display_name: "Claude Code", installed: true, version: null, install_command: null },
  { name: "codex", display_name: "Codex", installed: true, version: null, install_command: null },
];

function Setup() {
  const [sessions, setSessions] = useState(["claude"]);
  return <><WorkspaceAgentSetup agents={agents} sessions={sessions} onChange={setSessions} />
    <output data-testid="plan">{sessions.join(",")}</output></>;
}

afterEach(cleanup);

describe("Workspace agent selection", () => {
  it("chooses a logo tile and expands that choice with the count buttons", () => {
    render(<Setup />);
    const codex = screen.getByRole("button", { name: "Codex" });
    expect(within(codex).getByTestId("agent-mark-codex").getAttribute("data-logo")).toBe("/provider-logos/openai.svg");
    fireEvent.click(codex);
    fireEvent.click(screen.getByRole("button", { name: "6 sessions" }));
    expect(screen.getByTestId("plan").textContent).toBe(Array(6).fill("codex").join(","));
    expect(codex.getAttribute("aria-pressed")).toBe("true");
    expect(screen.queryByRole("combobox")).toBeNull();
  });

  it("edits a single session and preserves the rest when resizing the lineup", () => {
    render(<Setup />);
    fireEvent.click(screen.getByRole("button", { name: "3 sessions" }));
    fireEvent.click(screen.getByRole("button", { name: "Edit session 2: Claude Code" }));
    fireEvent.click(screen.getByRole("button", { name: "Codex" }));
    expect(screen.getByTestId("plan").textContent).toBe("claude,codex,claude");
    fireEvent.click(screen.getByRole("button", { name: "4 sessions" }));
    expect(screen.getByTestId("plan").textContent).toBe("claude,codex,claude,codex");
    fireEvent.click(screen.getByRole("button", { name: "2 sessions" }));
    expect(screen.getByTestId("plan").textContent).toBe("claude,codex");
    fireEvent.click(screen.getByRole("button", { name: "All sessions" }));
    fireEvent.click(screen.getByRole("button", { name: "Codex" }));
    expect(screen.getByTestId("plan").textContent).toBe("codex,codex");
  });

  it("offers up to sixteen sessions and names the grid they open as", () => {
    render(<Setup />);
    fireEvent.click(screen.getByRole("button", { name: "16 sessions" }));
    expect(screen.getByText(/opens as 4 × 4/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "9 sessions" }));
    expect(screen.getByText(/opens as 3 × 3/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "17 sessions" })).toBeNull();
  });

  it("clears a removed editing target when the count shrinks", () => {
    render(<Setup />);
    fireEvent.click(screen.getByRole("button", { name: "8 sessions" }));
    fireEvent.click(screen.getByRole("button", { name: "Edit session 8: Claude Code" }));
    fireEvent.click(screen.getByRole("button", { name: "1 session" }));
    fireEvent.click(screen.getByRole("button", { name: "Codex" }));
    expect(screen.getByTestId("plan").textContent).toBe("codex");
    expect(screen.queryByRole("button", { name: "17 sessions" })).toBeNull();
  });

  it("fills an unassigned seat when discovery arrives without replacing existing picks", () => {
    const change = vi.fn();
    const { rerender } = render(<WorkspaceAgentSetup agents={[]} sessions={[""]} onChange={change} />);
    expect(change).not.toHaveBeenCalled();
    rerender(<WorkspaceAgentSetup agents={agents} sessions={["codex", ""]} onChange={change} />);
    expect(change).toHaveBeenLastCalledWith(["codex", "claude"]);
  });

  it("forgets an editing target removed by an external lineup reset", () => {
    const change = vi.fn();
    const { rerender } = render(<WorkspaceAgentSetup agents={agents} sessions={Array(8).fill("claude")} onChange={change} />);
    fireEvent.click(screen.getByRole("button", { name: "Edit session 8: Claude Code" }));
    rerender(<WorkspaceAgentSetup agents={agents} sessions={["claude"]} onChange={change} />);
    fireEvent.click(screen.getByRole("button", { name: "Codex" }));
    expect(change).toHaveBeenLastCalledWith(["codex"]);
  });

  it("does not change the launch plan while it is being submitted", () => {
    const change = vi.fn();
    render(<WorkspaceAgentSetup agents={agents} sessions={["claude"]} disabled onChange={change} />);
    fireEvent.click(screen.getByRole("button", { name: "Codex" }));
    fireEvent.click(screen.getByRole("button", { name: "8 sessions" }));
    expect(change).not.toHaveBeenCalled();
  });
});
