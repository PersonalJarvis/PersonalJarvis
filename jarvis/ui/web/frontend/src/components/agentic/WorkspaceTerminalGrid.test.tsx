import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { SessionState } from "@/lib/agenticIdeApi";
const reorder = vi.hoisted(() => vi.fn());
vi.mock("@/lib/agenticIdeApi", () => ({ reorderIdeTerminals: reorder }));
vi.mock("./AgenticTerminal", () => ({ AgenticTerminal: ({ name, onToggleMaximize, onArrangeStart, showArrangeHandle }: { name: string; onToggleMaximize: () => void; onArrangeStart?: (event: React.PointerEvent) => void; showArrangeHandle?: boolean }) => <>
  <button onClick={onToggleMaximize}>{name}</button>
  {showArrangeHandle && <button type="button" data-ide-drag-handle="true" onPointerDown={onArrangeStart}>Move {name}</button>}
</> }));
vi.mock("@/hooks/useTheme", () => ({ useThemeValue: () => "dark" }));
import { columnsForSessions } from "./WorkspaceTerminalGrid";
import { WorkspaceTerminalGrid } from "./WorkspaceTerminalGrid";

class ResizeObserverStub { observe() {} disconnect() {} }
vi.stubGlobal("ResizeObserver", ResizeObserverStub);
vi.stubGlobal("PointerEvent", MouseEvent);

describe("workspace session columns", () => {
  it("uses the compact reference layout on a wide desktop", () => {
    expect([1, 2, 3, 4, 5, 6, 7, 8].map((count) => columnsForSessions(count, 1800)))
      .toEqual([1, 2, 3, 2, 3, 3, 4, 4]);
  });

  it("limits columns as available width shrinks", () => {
    expect(columnsForSessions(8, 680)).toBe(4);
    expect(columnsForSessions(3, 310)).toBe(2);
    expect(columnsForSessions(2, 310)).toBe(1);
  });
});

it("shows the remaining pane when a maximized session closes", async () => {
  const terminal = (name: string) => ({ name, key: name, history_id: name, display_name: name });
  const session = (names: string[]) => ({ id: "w1", terminals: names.map(terminal) }) as SessionState;
  const props = { onChanged: vi.fn(), onAdd: vi.fn(), onClose: vi.fn(), onSelect: vi.fn(), selected: "", fontSize: 13, appearance: null };
  const { rerender } = render(<WorkspaceTerminalGrid session={session(["T1", "T2"])} {...props} />);
  fireEvent.click(screen.getByRole("button", { name: "T1" }));
  rerender(<WorkspaceTerminalGrid session={session(["T2"])} {...props} />);
  await waitFor(() => expect(screen.getByRole("button", { name: "T2" }).closest("[data-session-id]")?.className).not.toContain("hidden"));
});

it("reorders through the visible handle and leaves terminal keys alone", async () => {
  reorder.mockReset();
  const session = { id: "w1", terminals: ["T1", "T2"].map((name) => ({ name, key: name, history_id: name, display_name: name })) } as SessionState;
  reorder.mockResolvedValue({ session });
  const props = { onChanged: vi.fn(), onAdd: vi.fn(), onClose: vi.fn(), onSelect: vi.fn(), selected: "", fontSize: 13, appearance: null };
  render(<WorkspaceTerminalGrid session={session} {...props} />);
  const source = screen.getByRole("button", { name: "Move T1" });
  const target = document.querySelector<HTMLElement>('[data-session-id="T2"]');
  Object.defineProperty(document, "elementFromPoint", { configurable: true, value: vi.fn(() => target) });
  fireEvent.pointerDown(source, { button: 0, clientX: 10, clientY: 10 });
  fireEvent.pointerMove(window, { clientX: 35, clientY: 10 });
  fireEvent.pointerUp(window, { clientX: 35, clientY: 10 });
  await waitFor(() => expect(reorder).toHaveBeenCalledWith("w1", ["T2", "T1"]));

  reorder.mockClear();
  const tile = document.querySelector<HTMLElement>('[data-session-id="T1"]')!;
  const terminalInput = document.createElement("textarea");
  tile.appendChild(terminalInput);
  fireEvent.keyDown(terminalInput, { altKey: true, key: "ArrowRight" });
  expect(reorder).not.toHaveBeenCalled();
  fireEvent.keyDown(tile, { altKey: true, key: "ArrowRight" });
  await waitFor(() => expect(reorder).toHaveBeenCalledWith("w1", ["T2", "T1"]));
});
