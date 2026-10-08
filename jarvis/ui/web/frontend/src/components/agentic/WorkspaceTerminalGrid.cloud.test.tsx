import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { SessionState, TerminalState } from "@/lib/agenticIdeApi";
const api = vi.hoisted(() => ({ place: vi.fn(), toast: vi.fn() }));
vi.mock("@/lib/agenticIdeApi", () => ({ placeTerminal: api.place }));
vi.mock("@/hooks/useComputers", () => ({ useComputerChoices: () => [] }));
vi.mock("@/hooks/useTheme", () => ({ useThemeValue: () => "dark" }));
vi.mock("@/store/events", () => ({ useEventStore: (select: (state: unknown) => unknown) => select({ pushToast: api.toast }) }));
vi.mock("./AgenticTerminal", () => ({ AgenticTerminal: (props: { name: string; onOpenCloud: () => void; placementBusy: boolean; restartToken: number }) =>
  <button onClick={props.onOpenCloud} disabled={props.placementBusy} data-restart={props.restartToken}>Cloud options for {props.name}</button> }));
vi.mock("./CloudPlacementDialog", () => ({ CloudPlacementDialog: (props: { terminal: TerminalState; busy: boolean; error: string; onConfirm: (id: string, name: string) => void; onCancel: () => void }) =>
  <div role="dialog" aria-label={`Move ${props.terminal.name}`}>
    <button disabled={props.busy} onClick={() => props.onConfirm("vps", "Build server")}>Confirm move</button>
    <button disabled={props.busy} onClick={props.onCancel}>Cancel move</button>
    {props.error && <p role="alert">{props.error}</p>}
  </div> }));
import { WorkspaceTerminalGrid } from "./WorkspaceTerminalGrid";
class ResizeObserverStub { observe() {} disconnect() {} }
vi.stubGlobal("ResizeObserver", ResizeObserverStub);
const session = { id: "workspace", folder: "/work/project", terminals: [{ key: "dana", history_id: "stable-dana", name: "Dana", agent: "claude", display_name: "Claude Code" }] } as SessionState;
const props = { session, onChanged: vi.fn(), onAdd: vi.fn(), onClose: vi.fn(), onSelect: vi.fn(), selected: "Dana", fontSize: 13, appearance: null };
beforeEach(() => vi.clearAllMocks());
afterEach(cleanup);

it("keeps cloud discovery available without cached servers and confirms before sending the stable pane identity", async () => {
  api.place.mockResolvedValue({ session: { ...session, terminals: [{ ...session.terminals[0], computer_id: "vps" }] }, message: "Native conversation restored." });
  render(<WorkspaceTerminalGrid {...props} />);
  fireEvent.click(screen.getByRole("button", { name: "Cloud options for Dana" }));
  expect(api.place).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Confirm move" }));
  await waitFor(() => expect(api.place).toHaveBeenCalledExactlyOnceWith("pane:stable-dana", "workspace", "vps"));
  await waitFor(() => expect(props.onChanged).toHaveBeenCalledOnce());
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.getByRole("button", { name: "Cloud options for Dana" }).getAttribute("data-restart")).toBe("1");
});

it("prevents repeated transfer calls while pending and retains a failed review for retry", async () => {
  let reject: (error: Error) => void = () => {};
  api.place.mockImplementation(() => new Promise((_resolve, no) => { reject = no; }));
  render(<WorkspaceTerminalGrid {...props} />);
  fireEvent.click(screen.getByRole("button", { name: "Cloud options for Dana" }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm move" }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm move" }));
  expect(api.place).toHaveBeenCalledOnce();
  expect((screen.getByRole("button", { name: "Cloud options for Dana" }) as HTMLButtonElement).disabled).toBe(true);
  await act(async () => { reject(new Error("Snapshot conflicts with remote changes")); });
  expect(screen.getByRole("alert").textContent).toBe("Snapshot conflicts with remote changes");
  expect(props.onChanged).not.toHaveBeenCalled();
  expect((screen.getByRole("button", { name: "Confirm move" }) as HTMLButtonElement).disabled).toBe(false);
});

it("drops a stale review when its pane has gone instead of moving another pane with the same name", () => {
  const { rerender } = render(<WorkspaceTerminalGrid {...props} />);
  fireEvent.click(screen.getByRole("button", { name: "Cloud options for Dana" }));
  rerender(<WorkspaceTerminalGrid {...props} session={{ ...session, terminals: [{ ...session.terminals[0], history_id: "replacement" }] }} />);
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(api.place).not.toHaveBeenCalled();
});
