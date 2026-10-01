import { useEffect } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { SessionState } from "@/lib/agenticIdeApi";

const lifecycle = vi.hoisted(() => ({ mount: vi.fn(), unmount: vi.fn() }));
vi.mock("./WorkspaceTerminalGrid", () => ({
  WorkspaceTerminalGrid: ({ session, active, disabled }: { session: SessionState; active: boolean; disabled: boolean }) => {
    useEffect(() => {
      lifecycle.mount(session.id);
      return () => lifecycle.unmount(session.id);
    }, []);
    return <input aria-label={session.id} data-active={String(active)} disabled={disabled} defaultValue={session.name} />;
  },
}));
import { RetainedWorkspaceGrid } from "./RetainedWorkspaceGrid";

const session = (id: string, count = 1) => ({
  id, name: id,
  terminals: Array.from({ length: count }, (_, index) => ({ key: `${id}-${index}`, history_id: `${id}-${index}` })),
}) as SessionState;
const props = {
  onScreen: true, selected: "", fontSize: 13, appearance: null,
  onChanged: vi.fn(), onAdd: vi.fn(), onClose: vi.fn(), onSelect: vi.fn(),
};
beforeEach(() => { vi.clearAllMocks(); });
afterEach(cleanup);

describe("retained workspace terminals", () => {
  it("keeps the same terminal view and input when switching away and back", () => {
    const first = session("first");
    const second = session("second");
    const view = render(<RetainedWorkspaceGrid {...props} session={first} />);
    const input = screen.getByLabelText("first");
    fireEvent.change(input, { target: { value: "unfinished input" } });
    view.rerender(<RetainedWorkspaceGrid {...props} session={second} />);
    expect(input.closest("[hidden]")).not.toBeNull();
    expect(input.getAttribute("data-active")).toBe("false");
    expect(input.hasAttribute("disabled")).toBe(true);
    view.rerender(<RetainedWorkspaceGrid {...props} session={{ ...first }} />);
    expect(screen.getByLabelText("first")).toBe(input);
    expect((input as HTMLInputElement).value).toBe("unfinished input");
    expect(input.closest("[hidden]")).toBeNull();
    expect(input.getAttribute("data-active")).toBe("true");
    expect(lifecycle.mount).toHaveBeenCalledTimes(2);
    expect(lifecycle.unmount).not.toHaveBeenCalled();
  });

  it("parks every retained view when the IDE is offscreen", () => {
    const view = render(<RetainedWorkspaceGrid {...props} session={session("first")} />);
    view.rerender(<RetainedWorkspaceGrid {...props} session={session("second")} />);
    view.rerender(<RetainedWorkspaceGrid {...props} session={session("second")} onScreen={false} />);
    expect(document.querySelectorAll("[data-active=true]")).toHaveLength(0);
    expect(lifecycle.unmount).not.toHaveBeenCalled();
  });

  it("evicts the least recently visited workspace after three views", () => {
    const view = render(<RetainedWorkspaceGrid {...props} session={session("first")} />);
    for (const id of ["second", "third", "first", "fourth"]) {
      view.rerender(<RetainedWorkspaceGrid {...props} session={session(id)} />);
    }
    expect(lifecycle.unmount).toHaveBeenCalledExactlyOnceWith("second");
    expect(screen.queryByLabelText("second")).toBeNull();
    expect(document.querySelectorAll("input")).toHaveLength(3);
  });

  it("bounds retained panes while always allowing the active workspace", () => {
    const view = render(<RetainedWorkspaceGrid {...props} session={session("first", 6)} />);
    view.rerender(<RetainedWorkspaceGrid {...props} session={session("second", 6)} />);
    expect(lifecycle.unmount).not.toHaveBeenCalled();
    view.rerender(<RetainedWorkspaceGrid {...props} session={session("large", 16)} />);
    expect(screen.queryByLabelText("first")).toBeNull();
    expect(screen.queryByLabelText("second")).toBeNull();
    expect(screen.getByLabelText("large")).toBeTruthy();
  });

  it("releases closed workspaces and views made stale by a transferred pane", () => {
    const first = session("first");
    const view = render(<RetainedWorkspaceGrid {...props} session={first} workspaceIds={["first", "second"]} />);
    view.rerender(<RetainedWorkspaceGrid {...props} session={session("second")} workspaceIds={["second"]} />);
    expect(screen.queryByLabelText("first")).toBeNull();
    view.rerender(<RetainedWorkspaceGrid {...props} session={{ ...session("third"), terminals: session("second").terminals }} />);
    expect(screen.queryByLabelText("second")).toBeNull();
    expect(screen.getByLabelText("third")).toBeTruthy();
  });

  it("releases all retained views on unmount", () => {
    const view = render(<RetainedWorkspaceGrid {...props} session={session("first")} />);
    view.rerender(<RetainedWorkspaceGrid {...props} session={session("second")} />);
    view.unmount();
    expect(lifecycle.unmount.mock.calls).toEqual([["second"], ["first"]]);
  });
});
