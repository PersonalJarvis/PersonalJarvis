import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { WorkspaceOptionsDialog } from "./WorkspaceOptionsDialog";

afterEach(cleanup);
const setup = () => ({
  open: true, onOpenChange: vi.fn(), workspace: "Installer", count: 6, busy: false, canAdd: true,
  onAdd: vi.fn(), onBalance: vi.fn(), onRename: vi.fn(), onClose: vi.fn(),
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

it("disables adding a ninth session and closes with Escape", () => {
  const props = setup();
  render(<WorkspaceOptionsDialog {...props} count={8} />);
  expect((screen.getByRole("button", { name: "Add coding agent" }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.keyDown(document, { key: "Escape" });
  expect(props.onOpenChange).toHaveBeenCalledWith(false);
});
