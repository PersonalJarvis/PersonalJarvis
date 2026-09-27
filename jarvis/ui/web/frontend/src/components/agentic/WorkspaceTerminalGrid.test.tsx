import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { SessionState } from "@/lib/agenticIdeApi";
const api = vi.hoisted(() => ({ reorder: vi.fn(), rename: vi.fn(), toast: vi.fn() }));
vi.mock("@/lib/agenticIdeApi", () => ({ reorderIdeTerminals: api.reorder, renameTerminal: api.rename }));
vi.mock("@/store/events", () => ({ useEventStore: (select: (state: unknown) => unknown) => select({ pushToast: api.toast }) }));
vi.mock("./AgenticTerminal", () => ({ AgenticTerminal: (props: {
  name: string; onToggleMaximize: () => void; onRestart: () => void; restartToken: number;
  onFocus: () => void;
  onArrangeStart?: (event: React.PointerEvent) => void;
}) => <div onMouseDown={props.onFocus}>
  <button onClick={props.onToggleMaximize}>Maximize {props.name}</button>
  <button type="button" data-ide-drag-handle="true" onPointerDown={props.onArrangeStart}>Move {props.name}</button>
  <button onClick={props.onRestart}>Restart {props.name}</button>
  <output data-testid={`restart-${props.name}`}>{props.restartToken}</output>
  <textarea aria-label={`Terminal input ${props.name}`} />
</div> }));
vi.mock("@/hooks/useTheme", () => ({ useThemeValue: () => "dark" }));
import { columnsForSessions, swapSessions, WorkspaceTerminalGrid } from "./WorkspaceTerminalGrid";

class ResizeObserverStub { observe() {} disconnect() {} }
class PointerEventStub extends MouseEvent {
  pointerId: number;
  constructor(type: string, init: PointerEventInit = {}) { super(type, init); this.pointerId = init.pointerId ?? 1; }
}
vi.stubGlobal("ResizeObserver", ResizeObserverStub);
vi.stubGlobal("PointerEvent", PointerEventStub);
const makeSession = (names = ["T1", "T2", "T3", "T4"]) => ({
  id: "w1", terminals: names.map((name) => ({ name, key: name, history_id: name, display_name: "Codex", agent: "codex" })),
}) as SessionState;
const props = { onChanged: vi.fn(), onAdd: vi.fn(), onClose: vi.fn(), onSelect: vi.fn(), selected: "", fontSize: 13, appearance: null };
const order = () => [...document.querySelectorAll<HTMLElement>("[data-session-id]")].map((node) => node.dataset.sessionId);
const geometry = () => document.querySelectorAll<HTMLElement>("[data-session-id]").forEach((node, index) => {
  const x = (index % 2) * 400, y = Math.floor(index / 2) * 400;
  node.getBoundingClientRect = () => ({ left: x, top: y, right: x + 390, bottom: y + 390, width: 390, height: 390, x, y, toJSON() {} });
});
const dragToThird = () => {
  geometry();
  fireEvent.pointerDown(screen.getByRole("button", { name: "Move T1" }), { button: 0, pointerId: 1, clientX: 15, clientY: 15 });
  fireEvent.pointerMove(window, { pointerId: 1, clientX: 20, clientY: 450 });
  fireEvent.pointerUp(window, { pointerId: 1, clientX: 20, clientY: 450 });
};
function Controlled() {
  const [session, setSession] = useState(makeSession());
  return <WorkspaceTerminalGrid {...props} session={session} onChanged={setSession} />;
}
beforeEach(() => { vi.clearAllMocks(); });
afterEach(cleanup);

describe("workspace columns", () => {
  it("uses all useful space on wide displays and keeps six in the reference layout", () => {
    expect([1, 2, 3, 4, 5, 6, 7, 8].map((count) => columnsForSessions(count, 1800)))
      .toEqual([1, 2, 3, 4, 3, 3, 4, 4]);
    expect(columnsForSessions(4, 1100)).toBe(2);
  });
  it("never creates a third row, including manual column preferences", () => {
    expect(columnsForSessions(8, 680)).toBe(4);
    expect(columnsForSessions(3, 310)).toBe(2);
    expect(columnsForSessions(2, 310)).toBe(1);
    expect(columnsForSessions(8, 1800, 2)).toBe(4);
    expect(columnsForSessions(4, 1800, 2)).toBe(2);
  });
  it("swaps two slots without shifting the terminals between them", () => {
    expect(swapSessions(["a", "b", "c", "d"], "a", "c")).toEqual(["c", "b", "a", "d"]);
  });
});

it("swaps immediately, persists once and preserves mounted terminal nodes", async () => {
  let finish!: (state: { session: SessionState }) => void;
  api.reorder.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
  render(<Controlled />);
  const original = screen.getByLabelText("Terminal input T1");
  dragToThird();
  expect(order()).toEqual(["T3", "T2", "T1", "T4"]);
  expect(screen.getByLabelText("Terminal input T1")).toBe(original);
  expect(api.reorder).toHaveBeenCalledWith("w1", ["T3", "T2", "T1", "T4"]);
  fireEvent.keyDown(document.querySelector('[data-session-id="T2"]')!, { altKey: true, key: "ArrowRight" });
  expect(api.reorder).toHaveBeenCalledTimes(1);
  await act(async () => finish({ session: makeSession(["T3", "T2", "T1", "T4"]) }));
  expect(order()).toEqual(["T3", "T2", "T1", "T4"]);
});

it("allows title pointer presses to select the pane without starting a reorder", () => {
  render(<WorkspaceTerminalGrid {...props} session={makeSession()} />);
  const handle = screen.getByRole("button", { name: "Move T1" });
  const down = new PointerEvent("pointerdown", { bubbles: true, cancelable: true, button: 0, pointerId: 1 });
  fireEvent(handle, down);
  // Browsers suppress compatibility mousedown when pointerdown is cancelled.
  expect(down.defaultPrevented).toBe(false);
  if (!down.defaultPrevented) fireEvent.mouseDown(handle);
  fireEvent.pointerUp(window, { pointerId: 1 });
  fireEvent.click(handle, { detail: 1 });
  expect(props.onSelect).toHaveBeenCalledExactlyOnceWith("T1");
  expect(api.reorder).not.toHaveBeenCalled();
});

it("rolls back a rejected swap and releases the mutation barrier", async () => {
  api.reorder.mockRejectedValue(new Error("Connection lost"));
  const onMutationStart = vi.fn(), onMutationEnd = vi.fn();
  render(<WorkspaceTerminalGrid {...props} session={makeSession()} onMutationStart={onMutationStart} onMutationEnd={onMutationEnd} />);
  dragToThird();
  await waitFor(() => expect(api.toast).toHaveBeenCalledWith("error", "Connection lost"));
  expect(order()).toEqual(["T1", "T2", "T3", "T4"]);
  expect(onMutationStart).toHaveBeenCalledOnce();
  expect(onMutationEnd).toHaveBeenCalledOnce();
});

it("cannot resurrect a removed terminal from a late response", async () => {
  let finish!: (state: { session: SessionState }) => void;
  api.reorder.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
  const changed = vi.fn();
  const { rerender } = render(<WorkspaceTerminalGrid {...props} session={makeSession()} onChanged={changed} />);
  dragToThird();
  rerender(<WorkspaceTerminalGrid {...props} session={makeSession(["T2", "T3", "T4"])} onChanged={changed} />);
  await act(async () => finish({ session: makeSession(["T3", "T2", "T1", "T4"]) }));
  expect(changed).not.toHaveBeenCalled();
  expect(order()).toEqual(["T2", "T3", "T4"]);
});

it("cancels a drag with Escape and does not steal terminal keyboard shortcuts", () => {
  render(<WorkspaceTerminalGrid {...props} session={makeSession()} />);
  geometry();
  const handle = screen.getByRole("button", { name: "Move T1" });
  fireEvent.pointerDown(handle, { button: 0, clientX: 15, clientY: 15 });
  fireEvent.pointerMove(window, { clientX: 20, clientY: 450 });
  fireEvent.keyDown(window, { key: "Escape" });
  fireEvent.pointerUp(window, { clientX: 20, clientY: 450 });
  fireEvent.keyDown(screen.getByLabelText("Terminal input T1"), { altKey: true, key: "ArrowRight" });
  expect(api.reorder).not.toHaveBeenCalled();
});

it("receives drag movement over a terminal even when it stops event bubbling", async () => {
  api.reorder.mockResolvedValue({ session: makeSession(["T3", "T2", "T1", "T4"]) });
  render(<WorkspaceTerminalGrid {...props} session={makeSession()} />);
  geometry();
  const input = screen.getByLabelText("Terminal input T3");
  input.addEventListener("pointermove", (event) => event.stopPropagation());
  input.addEventListener("pointerup", (event) => event.stopPropagation());
  fireEvent.pointerDown(screen.getByRole("button", { name: "Move T1" }), { button: 0, clientX: 15, clientY: 15 });
  fireEvent.pointerMove(input, { clientX: 20, clientY: 450 });
  fireEvent.pointerUp(input, { clientX: 20, clientY: 450 });
  await waitFor(() => expect(api.reorder).toHaveBeenCalledWith("w1", ["T3", "T2", "T1", "T4"]));
});

it("only restarts the addressed session and shows survivors after maximized close", async () => {
  const { rerender } = render(<WorkspaceTerminalGrid {...props} session={makeSession(["T1", "T2"])} />);
  fireEvent.click(screen.getByRole("button", { name: "Restart T1" }));
  expect(screen.getByTestId("restart-T1").textContent).toBe("1");
  expect(screen.getByTestId("restart-T2").textContent).toBe("0");
  fireEvent.click(screen.getByRole("button", { name: "Maximize T1" }));
  rerender(<WorkspaceTerminalGrid {...props} session={makeSession(["T2"])} />);
  await waitFor(() => expect(screen.getByRole("button", { name: "Maximize T2" }).closest("[data-session-id]")?.className).not.toContain("hidden"));
});
