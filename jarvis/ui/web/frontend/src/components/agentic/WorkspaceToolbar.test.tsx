import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { WorkspaceToolbar } from "./WorkspaceToolbar";

afterEach(cleanup);
const setup = () => ({
  project: "Project", workspace: "Project", folder: "/code/project", count: 6, busy: false, canAdd: true,
  onAdd: vi.fn(), onRename: vi.fn(), onClose: vi.fn(), fontSize: 20, onFontSize: vi.fn(),
  appearance: null, onAppearance: vi.fn(), columns: 0, onColumns: vi.fn(), voiceOpen: false, onVoice: vi.fn(),
});

it("keeps the toolbar compact and exposes working display controls on demand", () => {
  const props = setup();
  render(<WorkspaceToolbar {...props} />);
  expect(screen.getAllByText("Project")).toHaveLength(1);
  expect(screen.queryByText("/code/project")).toBeNull();
  expect(screen.queryByRole("button", { name: "Decrease terminal text size" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Workspace options" }));
  fireEvent.click(screen.getByRole("button", { name: "3 columns" }));
  expect(props.onColumns).toHaveBeenCalledWith(3);
  expect((screen.getByRole("button", { name: "2 columns" }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Decrease terminal text size" }));
  expect(props.onFontSize).toHaveBeenCalledWith(19);
  fireEvent.click(screen.getByRole("button", { name: "light terminals" }));
  expect(props.onAppearance).toHaveBeenCalledWith("light");
  fireEvent.keyDown(document, { key: "Escape" });
  expect(screen.queryByRole("dialog", { name: "Workspace options" })).toBeNull();
});

it("dismisses after a workspace change and disables adding the ninth session", () => {
  const props = setup();
  const { rerender } = render(<WorkspaceToolbar {...props} />);
  fireEvent.click(screen.getByRole("button", { name: "Workspace options" }));
  rerender(<WorkspaceToolbar {...props} workspace="Another" count={8} />);
  expect(screen.queryByRole("dialog", { name: "Workspace options" })).toBeNull();
  expect((screen.getByRole("button", { name: "Add coding agent" }) as HTMLButtonElement).disabled).toBe(true);
});

it("shows the effective columns when a saved preference cannot fit this group", () => {
  const props = setup();
  const { rerender } = render(<WorkspaceToolbar {...props} count={1} columns={4} />);
  fireEvent.click(screen.getByRole("button", { name: "Workspace options" }));
  expect(screen.getByRole("button", { name: "1 columns" }).getAttribute("aria-pressed")).toBe("true");
  expect((screen.getByRole("button", { name: "4 columns" }) as HTMLButtonElement).disabled).toBe(true);
  rerender(<WorkspaceToolbar {...props} count={6} columns={2} />);
  expect(screen.getByRole("button", { name: "3 columns" }).getAttribute("aria-pressed")).toBe("true");
  expect(screen.getByRole("button", { name: "2 columns" }).getAttribute("aria-pressed")).toBe("false");
  expect(props.onColumns).not.toHaveBeenCalled();
});
