/**
 * Dropping a file on a workspace TAB.
 *
 * The tab is how a user addresses a project they are not currently looking at,
 * so the gesture has to name the workspace it landed on — dropping on "Website"
 * while the "Jarvis" grid is on screen must not quietly send the file to Jarvis.
 *
 * The other half is what the bar must NOT accept. A browser fires `dragenter`
 * for a stray text selection dragged by the mouse, and a tab that lights up for
 * one tells a user holding nothing that they can drop it here (BUG-110). Worse,
 * an unclaimed drop of a link NAVIGATES, which would replace the whole IDE and
 * every agent running in it.
 */
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { WorkspaceBar } from "./WorkspaceBar";
import type { WorkspaceCard } from "@/lib/agenticIdeApi";

function card(id: string, name: string): WorkspaceCard {
  return {
    id,
    folder: `C:/work/${name}`,
    name,
    branch: "main",
    terminals: 2,
    live_terminals: 2,
    focus_mode: false,
    created_at: 0,
    last_active_at: 0,
    active: false,
  };
}

const workspaces = [card("w1", "Jarvis"), card("w2", "Website")];

/** A DataTransfer stand-in — jsdom does not construct real ones. */
function transfer(types: string[], files: File[] = []) {
  return {
    types,
    files,
    items: [],
    dropEffect: "none",
    getData: (kind: string) =>
      kind === "text/uri-list" && files.length === 0 ? "file:///C:/shot.png" : "",
  } as unknown as DataTransfer;
}

const base = {
  workspaces,
  activeId: "w1",
  addingNew: false,
  maxWorkspaces: 6,
  onSelect: () => {},
  onAdd: () => {},
  onRename: async () => true,
  onClose: () => {},
};

describe("WorkspaceBar file drops", () => {
  it("routes a dropped file to the workspace it landed on", () => {
    const onDropFiles = vi.fn();
    render(<WorkspaceBar {...base} onDropFiles={onDropFiles} />);

    const tab = screen.getByTestId("workspace-tab-drop-w2");
    fireEvent.drop(tab, { dataTransfer: transfer(["Files"]) });

    expect(onDropFiles).toHaveBeenCalledTimes(1);
    // The workspace that was dropped ON, not the one on screen.
    expect(onDropFiles.mock.calls[0][0]).toBe("w2");
  });

  it("hands over the paths the drag carried", () => {
    const onDropFiles = vi.fn();
    render(<WorkspaceBar {...base} onDropFiles={onDropFiles} />);

    fireEvent.drop(screen.getByTestId("workspace-tab-drop-w1"), {
      dataTransfer: transfer(["Files"]),
    });

    expect(onDropFiles.mock.calls[0][1].paths).toEqual(["C:/shot.png"]);
  });

  it("ignores a drag carrying only selected text", () => {
    const onDropFiles = vi.fn();
    render(<WorkspaceBar {...base} onDropFiles={onDropFiles} />);

    fireEvent.drop(screen.getByTestId("workspace-tab-drop-w1"), {
      dataTransfer: transfer(["text/plain"]),
    });

    expect(onDropFiles).not.toHaveBeenCalled();
  });

  it("arms the tab a file drag is over, and only that one", () => {
    render(<WorkspaceBar {...base} onDropFiles={vi.fn()} />);

    const target = screen.getByTestId("workspace-tab-drop-w2");
    const other = screen.getByTestId("workspace-tab-drop-w1");
    const before = target.className;
    fireEvent.dragEnter(target, { dataTransfer: transfer(["Files"]) });

    expect(target.className).not.toBe(before);
    expect(target.className).toContain("border-dashed");
    expect(other.className).not.toContain("border-dashed");
  });

  it("does not arm for a drag it would refuse", () => {
    render(<WorkspaceBar {...base} onDropFiles={vi.fn()} />);

    const tab = screen.getByTestId("workspace-tab-drop-w1");
    fireEvent.dragEnter(tab, { dataTransfer: transfer(["text/plain"]) });

    expect(tab.className).not.toContain("border-dashed");
  });

  it("disarms when the drag leaves the tab", () => {
    render(<WorkspaceBar {...base} onDropFiles={vi.fn()} />);

    const tab = screen.getByTestId("workspace-tab-drop-w2");
    fireEvent.dragEnter(tab, { dataTransfer: transfer(["Files"]) });
    fireEvent.dragLeave(tab, { dataTransfer: transfer(["Files"]) });

    expect(tab.className).not.toContain("border-dashed");
  });

  it("disarms when a drag that started here ends somewhere else", () => {
    // `dragleave` is not guaranteed to arrive; a tab left highlighted over a
    // workspace nobody dropped on is a lie about what happens next.
    render(<WorkspaceBar {...base} onDropFiles={vi.fn()} />);

    const tab = screen.getByTestId("workspace-tab-drop-w2");
    fireEvent.dragEnter(tab, { dataTransfer: transfer(["Files"]) });
    fireEvent.dragEnd(window);

    expect(tab.className).not.toContain("border-dashed");
  });

  it("still selects and renames normally with drops wired up", () => {
    const onSelect = vi.fn();
    render(<WorkspaceBar {...base} onSelect={onSelect} onDropFiles={vi.fn()} />);

    fireEvent.click(screen.getByTestId("workspace-tab-w2"));

    expect(onSelect).toHaveBeenCalledWith("w2");
  });

  it("takes no drags at all when the owner wired no handler", () => {
    // Accepting one would mean swallowing a file nothing does anything with.
    render(<WorkspaceBar {...base} />);

    const tab = screen.getByTestId("workspace-tab-drop-w1");
    fireEvent.dragEnter(tab, { dataTransfer: transfer(["Files"]) });

    expect(tab.className).not.toContain("border-dashed");
  });
});
