import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { WorkspaceOptionsDialog } from "./WorkspaceOptionsDialog";

afterEach(cleanup);
const setup = () => ({
  open: true, onOpenChange: vi.fn(), workspace: "Installer", count: 6, busy: false, canAdd: true,
  maxPanes: 16, onAdd: vi.fn(), onBalance: vi.fn(), onRename: vi.fn(), onClose: vi.fn(), onGit: vi.fn(),
  appearance: null, onAppearance: vi.fn(),
});

it("keeps arrangement and display controls accessible without a main toolbar", () => {
  const props = setup();
  render(<WorkspaceOptionsDialog {...props} />);
  expect(screen.getByRole("dialog", { name: "Workspace options" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Decrease terminal text size" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "light terminals" }));
  expect(props.onAppearance).toHaveBeenCalledWith("light");
  fireEvent.click(screen.getByRole("button", { name: "Balance layout" }));
  expect(props.onOpenChange).toHaveBeenCalledWith(false);
  expect(props.onBalance).toHaveBeenCalledOnce();
});

it("disables adding past the workspace limit and closes with Escape", () => {
  const props = setup();
  render(<WorkspaceOptionsDialog {...props} count={16} maxPanes={16} />);
  expect((screen.getByRole("button", { name: "Add coding agent" }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.keyDown(document, { key: "Escape" });
  expect(props.onOpenChange).toHaveBeenCalledWith(false);
});

it("switches between the minimal and the classic terminal style", () => {
  const props = { ...setup(), paneStyle: "minimal" as const, onPaneStyle: vi.fn() };
  render(<WorkspaceOptionsDialog {...props} />);
  const minimal = screen.getByRole("button", { name: /Minimal/ });
  const classic = screen.getByRole("button", { name: /Classic/ });
  expect(minimal.getAttribute("aria-pressed")).toBe("true");
  expect(classic.getAttribute("aria-pressed")).toBe("false");
  fireEvent.click(classic);
  expect(props.onPaneStyle).toHaveBeenCalledWith("classic");
  // A style change keeps the dialog open so the reader sees the result.
  expect(props.onOpenChange).not.toHaveBeenCalled();
});
