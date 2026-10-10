/**
 * Moving a pane into another open workspace, from its menu or by dropping it
 * on that workspace's row in the sidebar. Both ask where in that grid it goes
 * (MovePaneDialog) before anything moves.
 *
 * The agent keeps running; only the tab that lists it changes. What the grid
 * owes is to send the pane's stable identity (never a call-sign another tab
 * also has), to drop the pane from its own screen with the answer, and to say
 * when the pane arrived under another name. And only workspaces on the same
 * folder are offered at all: a pane never moves to another project's folder.
 */
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { SessionState } from "@/lib/agenticIdeApi";
const api = vi.hoisted(() => ({ transfer: vi.fn(), move: vi.fn(), toast: vi.fn(), layout: vi.fn() }));
vi.mock("@/lib/agenticIdeApi", () => ({ transferTerminal: api.transfer, moveTerminal: api.move, fetchWorkspaceLayout: api.layout }));
vi.mock("@/store/events", () => ({ useEventStore: Object.assign(
  (select: (state: unknown) => unknown) => select({ pushToast: api.toast }),
  { getState: () => ({ pushToast: api.toast, assistantName: "Jarvis" }) },
) }));
vi.mock("./AgenticTerminal", () => ({ AgenticTerminal: (props: {
  name: string;
  onArrangeStart?: (event: React.PointerEvent) => void;
  workspaceItems?: { label: string; run: () => void }[];
}) => <div data-testid={`pane-${props.name}`}>
  <button type="button" onPointerDown={props.onArrangeStart}>Move {props.name}</button>
  {(props.workspaceItems ?? []).map((item) => <button key={item.label} type="button" onClick={item.run}>{props.name}: {item.label}</button>)}
</div> }));
vi.mock("@/hooks/useTheme", () => ({ useThemeValue: () => "dark" }));
import { WorkspaceTerminalGrid } from "./WorkspaceTerminalGrid";
import { balancedLayout } from "./workspaceDocking";

class ResizeObserverStub { observe() {} disconnect() {} }
class PointerEventStub extends MouseEvent {
  pointerId: number;
  constructor(type: string, init: PointerEventInit = {}) { super(type, init); this.pointerId = init.pointerId ?? 1; }
}
vi.stubGlobal("ResizeObserver", ResizeObserverStub);
vi.stubGlobal("PointerEvent", PointerEventStub);

const makeSession = (names = ["T1", "T2"]) => ({
  id: "w1", folder: "C:/work/jarvis", layout: balancedLayout(names), terminals: names.map((name) => ({ name, key: name.toLowerCase(), history_id: `h-${name}`, display_name: "Claude Code", agent: "claude" })),
}) as SessionState;
// Blog and VMs share this folder (spelled differently, as Windows allows);
// Website is another project.
const workspaces = [
  { id: "w1", name: "Personal Jarvis", folder: "C:/work/jarvis" },
  { id: "w2", name: "Blog", folder: "c:\\work\\Jarvis\\" },
  { id: "w3", name: "VMs", folder: "C:/work/jarvis" },
  { id: "w4", name: "Website", folder: "C:/work/site" },
];
const props = { onChanged: vi.fn(), onAdd: vi.fn(), onClose: vi.fn(), onSelect: vi.fn(), selected: "", fontSize: 13, appearance: null, workspaces };

/** A sidebar row as IdeProjectTree draws it, and a pointer that finds it right of x=1000. */
function sidebarRow(id: string, name: string): HTMLElement {
  const row = document.createElement("div");
  row.setAttribute("data-pane-drop-workspace", id);
  row.setAttribute("data-pane-drop-name", name);
  const label = document.createElement("span");
  row.appendChild(label);
  document.body.appendChild(row);
  document.elementFromPoint = ((x: number) => (x > 1000 ? label : null)) as typeof document.elementFromPoint;
  return row;
}

/** Blog, as the move dialog's map draws it: one Claude pane. */
const blog = { id: "w2", name: "Blog", layout: { pane: "t1" },
  terminals: [{ key: "t1", name: "T1", agent: "claude", display_name: "Claude Code", history_id: "b-T1" }] };

beforeEach(() => { vi.clearAllMocks(); api.layout.mockResolvedValue(blog); });
afterEach(() => { cleanup(); document.body.innerHTML = ""; });

it("offers the other workspaces on this folder and moves the pane to the place picked", async () => {
  api.transfer.mockResolvedValue({ terminal: { name: "T3" }, state: { session: makeSession(["T1"]) } });
  const onChanged = vi.fn();
  render(<WorkspaceTerminalGrid {...props} session={makeSession()} onChanged={onChanged} />);

  expect(screen.queryByRole("button", { name: "T2: Move to Personal Jarvis…" })).toBeNull();
  expect(screen.queryByRole("button", { name: "T2: Move to Website…" })).toBeNull();
  expect(screen.getByRole("button", { name: "T2: Move to VMs…" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "T2: Move to Blog…" }));

  // Nothing moves until a place is confirmed.
  fireEvent.click(await screen.findByRole("button", { name: "Place T2 below T1" }));
  expect(api.transfer).not.toHaveBeenCalled();
  fireEvent.click(screen.getByTestId("move-pane-confirm"));

  await waitFor(() => expect(api.transfer).toHaveBeenCalledExactlyOnceWith("pane:h-T2", "w1", "w2", { anchor: "pane:b-T1", side: "below" }));
  await waitFor(() => expect(onChanged).toHaveBeenCalledOnce());
  expect(onChanged.mock.calls[0][0].terminals.map((t: { name: string }) => t.name)).toEqual(["T1"]);
  // It arrived under another call-sign; the user is told which.
  expect(api.toast).toHaveBeenCalledWith("success", "T2 moved to Blog as T3. Its agent keeps running.");
});

it("says why a move was refused and keeps the pane", async () => {
  api.transfer.mockRejectedValue(new Error("Blog works in another folder."));
  const onChanged = vi.fn(), onMutationEnd = vi.fn();
  render(<WorkspaceTerminalGrid {...props} session={makeSession()} onChanged={onChanged} onMutationEnd={onMutationEnd} />);

  fireEvent.click(screen.getByRole("button", { name: "T1: Move to Blog…" }));
  await screen.findByRole("button", { name: "Place T1 below T1" });
  fireEvent.click(screen.getByTestId("move-pane-confirm"));

  await waitFor(() => expect(api.toast).toHaveBeenCalledWith("error", "Blog works in another folder."));
  expect(onChanged).not.toHaveBeenCalled();
  expect(onMutationEnd).toHaveBeenCalledOnce();
  // Still open: another place can be picked.
  expect(screen.getByTestId("move-pane-dialog")).toBeTruthy();
});

it("asks where a pane dropped on a workspace in the sidebar goes, lighting the row while it hovers", async () => {
  api.transfer.mockResolvedValue({ terminal: { name: "T1" }, state: { session: makeSession(["T2"]) } });
  const row = sidebarRow("w2", "Blog");
  render(<WorkspaceTerminalGrid {...props} session={makeSession()} />);

  fireEvent.pointerDown(screen.getByRole("button", { name: "Move T1" }), { button: 0, pointerId: 1, clientX: 15, clientY: 15 });
  fireEvent.pointerMove(window, { pointerId: 1, clientX: 1200, clientY: 40 });
  expect(row.getAttribute("data-pane-drop-active")).toBe("true");
  expect(screen.getByText("Move to Blog")).toBeTruthy();
  fireEvent.pointerUp(window, { pointerId: 1, clientX: 1200, clientY: 40 });

  expect(row.hasAttribute("data-pane-drop-active")).toBe(false);
  expect(await screen.findByText("Move T1 to Blog")).toBeTruthy();
  expect(api.transfer).not.toHaveBeenCalled();
  await screen.findByRole("button", { name: "Place T1 below T1" });
  fireEvent.click(screen.getByTestId("move-pane-confirm"));
  // The place shown from the start: right of Blog's last pane.
  await waitFor(() => expect(api.transfer).toHaveBeenCalledExactlyOnceWith("pane:h-T1", "w1", "w2", { anchor: "pane:b-T1", side: "right" }));
  expect(api.move).not.toHaveBeenCalled();
});

it("ignores the row of the workspace the pane is already in", () => {
  const row = sidebarRow("w1", "Personal Jarvis");
  render(<WorkspaceTerminalGrid {...props} session={makeSession()} />);

  fireEvent.pointerDown(screen.getByRole("button", { name: "Move T1" }), { button: 0, pointerId: 1, clientX: 15, clientY: 15 });
  fireEvent.pointerMove(window, { pointerId: 1, clientX: 1200, clientY: 40 });
  expect(row.hasAttribute("data-pane-drop-active")).toBe(false);
  fireEvent.pointerUp(window, { pointerId: 1, clientX: 1200, clientY: 40 });

  expect(api.transfer).not.toHaveBeenCalled();
});

it("does not take a pane into a workspace on another folder, and says why", () => {
  const row = sidebarRow("w4", "Website");
  render(<WorkspaceTerminalGrid {...props} session={makeSession()} />);

  fireEvent.pointerDown(screen.getByRole("button", { name: "Move T1" }), { button: 0, pointerId: 1, clientX: 15, clientY: 15 });
  fireEvent.pointerMove(window, { pointerId: 1, clientX: 1200, clientY: 40 });
  expect(row.hasAttribute("data-pane-drop-active")).toBe(false);
  expect(screen.getByText("Not into Website: another folder")).toBeTruthy();
  fireEvent.pointerUp(window, { pointerId: 1, clientX: 1200, clientY: 40 });

  expect(screen.queryByTestId("move-pane-dialog")).toBeNull();
  expect(api.transfer).not.toHaveBeenCalled();
});
