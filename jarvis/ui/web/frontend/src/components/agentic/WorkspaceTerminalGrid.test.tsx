import { useState } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { SessionState } from "@/lib/agenticIdeApi";
const api = vi.hoisted(() => ({ move: vi.fn(), rename: vi.fn(), toast: vi.fn(), weights: vi.fn() }));
vi.mock("@/lib/agenticIdeApi", () => ({ moveTerminal: api.move, renameTerminal: api.rename, saveLayoutWeights: api.weights }));
vi.mock("@/store/events", () => ({ useEventStore: (select: (state: unknown) => unknown) => select({ pushToast: api.toast }) }));
vi.mock("./AgenticTerminal", () => ({ AgenticTerminal: (props: {
  name: string; onToggleMaximize: () => void; onRestart: () => void; restartToken: number;
  onFocus: () => void;
  onArrangeStart?: (event: React.PointerEvent) => void;
  headerMode?: string; markFocus?: boolean;
}) => <div onMouseDown={props.onFocus} data-testid={`pane-${props.name}`} data-header-mode={props.headerMode} data-mark-focus={String(props.markFocus)}>
  <button onClick={props.onToggleMaximize}>Maximize {props.name}</button>
  <button type="button" data-ide-drag-handle="true" onPointerDown={props.onArrangeStart}>Move {props.name}</button>
  <button onClick={props.onRestart}>Restart {props.name}</button>
  <output data-testid={`restart-${props.name}`}>{props.restartToken}</output>
  <textarea aria-label={`Terminal input ${props.name}`} />
</div> }));
vi.mock("@/hooks/useTheme", () => ({ useThemeValue: () => "dark" }));
import { WorkspaceTerminalGrid } from "./WorkspaceTerminalGrid";
import { useIdeSidePanelStore } from "@/store/ideSidePanel";
import { useIdeChatStore } from "@/store/ideChat";
import { balancedLayout, previewDock } from "./workspaceDocking";

class ResizeObserverStub { observe() {} disconnect() {} }
class PointerEventStub extends MouseEvent {
  pointerId: number;
  constructor(type: string, init: PointerEventInit = {}) { super(type, init); this.pointerId = init.pointerId ?? 1; }
}
vi.stubGlobal("ResizeObserver", ResizeObserverStub);
vi.stubGlobal("PointerEvent", PointerEventStub);
const makeSession = (names = ["T1", "T2", "T3", "T4"]) => ({
  id: "w1", layout: balancedLayout(names), terminals: names.map((name) => ({ name, key: name, history_id: name, display_name: "Codex", agent: "codex" })),
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
  fireEvent.pointerMove(window, { pointerId: 1, clientX: 195, clientY: 595 });
  fireEvent.pointerUp(window, { pointerId: 1, clientX: 195, clientY: 595 });
};
function Controlled() {
  const [session, setSession] = useState(makeSession());
  return <WorkspaceTerminalGrid {...props} session={session} onChanged={setSession} />;
}
beforeEach(() => { vi.clearAllMocks(); });
afterEach(cleanup);

it("swaps immediately, persists once and preserves mounted terminal nodes", async () => {
  let finish!: (state: SessionState) => void;
  api.move.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
  render(<Controlled />);
  const original = screen.getByLabelText("Terminal input T1");
  dragToThird();
  expect(document.querySelector<HTMLElement>('[data-session-id="T1"]')!.style.top).toContain("50%");
  expect(screen.getByLabelText("Terminal input T1")).toBe(original);
  expect(api.move).toHaveBeenCalledWith("pane:T1", "pane:T3", "swap");
  fireEvent.keyDown(document.querySelector('[data-session-id="T2"]')!, { altKey: true, key: "ArrowRight" });
  expect(api.move).toHaveBeenCalledTimes(1);
  await act(async () => finish(makeSession(["T3", "T2", "T1", "T4"])));
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
  expect(api.move).not.toHaveBeenCalled();
});

it("rolls back a rejected swap and releases the mutation barrier", async () => {
  api.move.mockRejectedValue(new Error("Connection lost"));
  const onMutationStart = vi.fn(), onMutationEnd = vi.fn();
  render(<WorkspaceTerminalGrid {...props} session={makeSession()} onMutationStart={onMutationStart} onMutationEnd={onMutationEnd} />);
  dragToThird();
  await waitFor(() => expect(api.toast).toHaveBeenCalledWith("error", "Connection lost"));
  expect(order()).toEqual(["T1", "T2", "T3", "T4"]);
  expect(onMutationStart).toHaveBeenCalledOnce();
  expect(onMutationEnd).toHaveBeenCalledOnce();
});

it("cannot resurrect a removed terminal from a late response", async () => {
  let finish!: (state: SessionState) => void;
  api.move.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
  const changed = vi.fn();
  const { rerender } = render(<WorkspaceTerminalGrid {...props} session={makeSession()} onChanged={changed} />);
  dragToThird();
  rerender(<WorkspaceTerminalGrid {...props} session={makeSession(["T2", "T3", "T4"])} onChanged={changed} />);
  await act(async () => finish(makeSession(["T3", "T2", "T1", "T4"])));
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
  expect(api.move).not.toHaveBeenCalled();
});

it("receives drag movement over a terminal even when it stops event bubbling", async () => {
  api.move.mockResolvedValue(makeSession(["T3", "T2", "T1", "T4"]));
  render(<WorkspaceTerminalGrid {...props} session={makeSession()} />);
  geometry();
  const input = screen.getByLabelText("Terminal input T3");
  input.addEventListener("pointermove", (event) => event.stopPropagation());
  input.addEventListener("pointerup", (event) => event.stopPropagation());
  fireEvent.pointerDown(screen.getByRole("button", { name: "Move T1" }), { button: 0, clientX: 15, clientY: 15 });
  fireEvent.pointerMove(input, { clientX: 195, clientY: 595 });
  fireEvent.pointerUp(input, { clientX: 195, clientY: 595 });
  await waitFor(() => expect(api.move).toHaveBeenCalledWith("pane:T1", "pane:T3", "swap"));
});

it("docks beside or below another pane and restores the returned layout after remount", async () => {
  const session = makeSession(["T1", "T2"]);
  const stacked = { ...session, layout: previewDock(session.layout!, "T1", "T2", "below") };
  api.move.mockResolvedValue(stacked);
  const { unmount } = render(<WorkspaceTerminalGrid {...props} session={session} />);
  geometry();
  fireEvent.pointerDown(screen.getByRole("button", { name: "Move T1" }), { button: 0, clientX: 15, clientY: 15 });
  fireEvent.pointerMove(window, { clientX: 595, clientY: 380 });
  expect(screen.getByTestId("dock-preview").dataset.position).toBe("below");
  fireEvent.pointerUp(window, { clientX: 595, clientY: 380 });
  await waitFor(() => expect(api.move).toHaveBeenCalledWith("pane:T1", "pane:T2", "below"));
  await waitFor(() => expect(props.onChanged).toHaveBeenCalledWith(stacked));
  unmount();
  render(<WorkspaceTerminalGrid {...props} session={stacked} />);
  expect(document.querySelector<HTMLElement>('[data-session-id="T1"]')!.style.width).toContain("100%");
  expect(document.querySelector<HTMLElement>('[data-session-id="T1"]')!.style.top).toContain("50%");
});

it("cancels a pending drag when a workspace switch starts", () => {
  const session = makeSession();
  const { rerender } = render(<WorkspaceTerminalGrid {...props} session={session} />);
  geometry();
  fireEvent.pointerDown(screen.getByRole("button", { name: "Move T1" }), { button: 0, clientX: 15, clientY: 15 });
  fireEvent.pointerMove(window, { clientX: 195, clientY: 595 });
  rerender(<WorkspaceTerminalGrid {...props} session={session} disabled />);
  fireEvent.pointerUp(window, { clientX: 195, clientY: 595 });
  expect(api.move).not.toHaveBeenCalled();
  expect(screen.queryByTestId("dock-preview")).toBeNull();
});

it("keeps keyboard swaps in the same row when two neighbors share an x coordinate", async () => {
  api.move.mockResolvedValue(makeSession(["T1", "T2", "T4", "T3"]));
  render(<WorkspaceTerminalGrid {...props} session={makeSession()} />);
  fireEvent.keyDown(document.querySelector('[data-session-id="T3"]')!, { altKey: true, key: "ArrowRight" });
  await waitFor(() => expect(api.move).toHaveBeenCalledWith("pane:T3", "pane:T4", "swap"));
});

it("does not select a diagonal pane when the bottom pane spans the whole row", async () => {
  api.move.mockResolvedValue(makeSession(["T2", "T1", "T3"]));
  render(<WorkspaceTerminalGrid {...props} session={makeSession(["T1", "T2", "T3"])} />);
  fireEvent.keyDown(document.querySelector('[data-session-id="T1"]')!, { altKey: true, key: "ArrowRight" });
  await waitFor(() => expect(api.move).toHaveBeenCalledWith("pane:T1", "pane:T2", "swap"));
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

it("frames the pane an agent card spotlit and drops the frame when another pane takes focus", () => {
  useIdeSidePanelStore.setState({ spotlight: { workspaceId: "w1", pane: "T2" } });
  render(<WorkspaceTerminalGrid {...props} session={makeSession()} />);
  const spotlit = document.querySelector<HTMLElement>('[data-session-id="T2"]')!;
  expect(spotlit.dataset.spotlit).toBe("true");
  expect(spotlit.className).toContain("ring-accent");
  expect(document.querySelector<HTMLElement>('[data-session-id="T1"]')!.dataset.spotlit).toBeUndefined();
  fireEvent.mouseDown(screen.getByLabelText("Terminal input T1"));
  expect(useIdeSidePanelStore.getState().spotlight).toBeNull();
  expect(document.querySelector<HTMLElement>('[data-session-id="T2"]')!.dataset.spotlit).toBeUndefined();
});

it("ignores a spotlight aimed at another workspace", () => {
  useIdeSidePanelStore.setState({ spotlight: { workspaceId: "w9", pane: "T2" } });
  render(<WorkspaceTerminalGrid {...props} session={makeSession()} />);
  expect(document.querySelector("[data-spotlit]")).toBeNull();
  useIdeSidePanelStore.setState({ spotlight: null });
});

it("drags the seam between two panes to resize them and saves the new sizes", async () => {
  vi.useFakeTimers();
  try {
    api.weights.mockResolvedValue(makeSession());
    render(<WorkspaceTerminalGrid {...props} session={makeSession()} />);
    const canvas = document.querySelector<HTMLElement>('[data-session-id="T1"]')!.parentElement!;
    Object.defineProperty(canvas, "clientWidth", { configurable: true, value: 1000 });
    Object.defineProperty(canvas, "clientHeight", { configurable: true, value: 800 });
    // The balanced four-pane grid is two rows of two; "0:1" divides T1 | T2.
    const seam = screen.getByTestId("pane-seam-0:1");
    fireEvent.pointerDown(seam, { button: 0, pointerId: 1, clientX: 500, clientY: 100 });
    fireEvent.pointerMove(window, { pointerId: 1, clientX: 700, clientY: 100 });
    fireEvent.pointerUp(window, { pointerId: 1, clientX: 700, clientY: 100 });
    expect(document.querySelector<HTMLElement>('[data-session-id="T1"]')!.style.width).toContain("70%");
    expect(document.querySelector<HTMLElement>('[data-session-id="T3"]')!.style.width).toContain("50%");
    await act(async () => { vi.advanceTimersByTime(1000); });
    expect(api.weights).toHaveBeenCalledTimes(1);
  } finally { vi.useRealTimers(); }
});

it("maximizes the pane the office asked for, once, and only in its own workspace", () => {
  act(() => useIdeChatStore.setState({ paneRequest: { workspaceId: "w2", pane: "T3", nonce: 7, maximize: true } }));
  const { unmount } = render(<WorkspaceTerminalGrid {...props} session={makeSession()} />);
  expect(screen.getAllByRole("separator").length).toBe(3);
  unmount();

  act(() => useIdeChatStore.setState({ paneRequest: { workspaceId: "w1", pane: "T3", nonce: 8, maximize: true } }));
  render(<WorkspaceTerminalGrid {...props} session={makeSession()} />);
  expect(screen.queryAllByRole("separator")).toHaveLength(0);
  const hidden = [...document.querySelectorAll<HTMLElement>("[data-session-id]")]
    .filter((node) => node.className.split(/\s+/).includes("hidden"))
    .map((node) => node.dataset.sessionId);
  expect(hidden).toEqual(["T1", "T2", "T4"]);
  expect(useIdeChatStore.getState().paneRequest).toMatchObject({ nonce: 8, maximize: false });
  act(() => useIdeChatStore.setState({ paneRequest: null }));
});

it("hides the seams while a pane is maximized", () => {
  render(<WorkspaceTerminalGrid {...props} session={makeSession()} />);
  expect(screen.getAllByRole("separator").length).toBe(3);
  fireEvent.click(screen.getByRole("button", { name: "Maximize T1" }));
  expect(screen.queryAllByRole("separator")).toHaveLength(0);
});

it("draws square tiles in the minimal style and cards in the classic one", () => {
  const { rerender } = render(<WorkspaceTerminalGrid {...props} session={makeSession()} paneStyle="minimal" />);
  const tile = document.querySelector<HTMLElement>('[data-session-id="T1"]')!;
  expect(tile.className).toContain("rounded-none");
  expect(screen.getByTestId("pane-T1").dataset.headerMode).toBe("minimal");
  rerender(<WorkspaceTerminalGrid {...props} session={makeSession()} paneStyle="classic" />);
  expect(document.querySelector<HTMLElement>('[data-session-id="T1"]')!.className).toContain("rounded-2xl");
  expect(screen.getByTestId("pane-T1").dataset.headerMode).toBe("compact");
});

it("hands the blue frame back from the side panel to a pressed pane", () => {
  useIdeSidePanelStore.setState({ open: true, inUse: true });
  render(<WorkspaceTerminalGrid {...props} session={makeSession()} paneStyle="minimal" />);
  expect(screen.getByTestId("pane-T1").dataset.markFocus).toBe("false");
  fireEvent.mouseDown(screen.getByTestId("pane-T2"));
  expect(useIdeSidePanelStore.getState().inUse).toBe(false);
  expect(screen.getByTestId("pane-T1").dataset.markFocus).toBe("true");
  useIdeSidePanelStore.setState({ open: false, inUse: false });
});
