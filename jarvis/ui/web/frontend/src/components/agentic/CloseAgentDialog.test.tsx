import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { CloseAgentDialog } from "./CloseAgentDialog";

afterEach(cleanup);

it("asks in-app before closing a pane and defaults focus to keeping it", () => {
  const onCancel = vi.fn(), onConfirm = vi.fn();
  render(<CloseAgentDialog target={{ kind: "terminal", name: "T8", agent: "claude", displayName: "Claude Code" }}
    busy={false} onCancel={onCancel} onConfirm={onConfirm} />);
  expect(screen.getByRole("dialog", { name: "Close T8?" })).toBeTruthy();
  expect(document.activeElement).toBe(screen.getByRole("button", { name: "Keep running" }));
  fireEvent.click(screen.getByRole("button", { name: "Close agent" }));
  expect(onConfirm).toHaveBeenCalledOnce();
  fireEvent.keyDown(document.activeElement!, { key: "Escape" });
  expect(onCancel).toHaveBeenCalledOnce();
});

it("names every agent a workspace close stops and locks while busy", () => {
  const onCancel = vi.fn();
  render(<CloseAgentDialog busy onCancel={onCancel} onConfirm={vi.fn()} target={{ kind: "workspace", name: "Installer",
    agents: [{ agent: "claude", displayName: "Claude Code" }, { agent: "codex", displayName: "Codex" }] }} />);
  expect(screen.getByText(/All 2 coding agents stop/)).toBeTruthy();
  expect((screen.getByRole("button", { name: /Close workspace/ }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.keyDown(document.activeElement!, { key: "Escape" });
  expect(onCancel).not.toHaveBeenCalled();
});
