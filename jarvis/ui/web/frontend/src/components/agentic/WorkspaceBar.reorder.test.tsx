import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { WorkspaceBar } from "./WorkspaceBar";
import type { WorkspaceCard } from "@/lib/agenticIdeApi";

function card(id: string, name: string): WorkspaceCard {
  return {
    id,
    folder: `C:/work/${name}`,
    name,
    branch: "main",
    terminals: 1,
    live_terminals: 1,
    focus_mode: false,
    created_at: 0,
    last_active_at: 0,
    active: false,
  };
}

const base = {
  workspaces: [card("w1", "First"), card("w2", "Second"), card("w3", "Third")],
  activeId: "w1",
  addingNew: false,
  maxWorkspaces: null,
  onSelect: () => {},
  onAdd: () => {},
  onRename: async () => true,
  onClose: () => {},
};

describe("WorkspaceBar tab reorder", () => {
  it("leaves tabs static when no reorder handler is wired", () => {
    render(<WorkspaceBar {...base} />);
    expect(screen.getByTestId("workspace-tab-drop-w1").getAttribute("draggable")).toBeNull();
  });

  it("makes tabs draggable when reordering is supported", () => {
    render(<WorkspaceBar {...base} onReorder={vi.fn()} />);
    expect(screen.getByTestId("workspace-tab-drop-w1").getAttribute("draggable")).toBe("true");
  });

  it("moves a tab with Alt plus arrow keys", () => {
    const onReorder = vi.fn();
    render(<WorkspaceBar {...base} onReorder={onReorder} />);
    fireEvent.keyDown(screen.getByTestId("workspace-tab-w1"), { key: "ArrowRight", altKey: true });
    expect(onReorder).toHaveBeenCalledWith(["w2", "w1", "w3"]);
  });

  it("ignores Alt plus arrow keys at the bar edges", () => {
    const onReorder = vi.fn();
    render(<WorkspaceBar {...base} onReorder={onReorder} />);
    fireEvent.keyDown(screen.getByTestId("workspace-tab-w1"), { key: "ArrowLeft", altKey: true });
    fireEvent.keyDown(screen.getByTestId("workspace-tab-w3"), { key: "ArrowRight", altKey: true });
    expect(onReorder).not.toHaveBeenCalled();
  });

  it("drags a tab after its neighbour", () => {
    const onReorder = vi.fn();
    render(<WorkspaceBar {...base} onReorder={onReorder} />);
    const source = screen.getByTestId("workspace-tab-drop-w1");
    const target = screen.getByTestId("workspace-tab-drop-w2");
    const data = new Map<string, string>();
    const dataTransfer = {
      types: ["application/x-jarvis-workspace-tab"],
      effectAllowed: "",
      dropEffect: "",
      setData: (kind: string, value: string) => void data.set(kind, value),
      getData: (kind: string) => data.get(kind) ?? "",
    };
    fireEvent.dragStart(source, { dataTransfer });
    target.getBoundingClientRect = () => ({ left: 0, width: 200, top: 0, height: 32 } as DOMRect);
    fireEvent.dragOver(target, { dataTransfer, clientX: 180 });
    fireEvent.drop(target, { dataTransfer, clientX: 180 });
    expect(onReorder).toHaveBeenCalledWith(["w2", "w1", "w3"]);
  });

  it("never treats a file drag as a tab reorder", () => {
    const onReorder = vi.fn();
    const onDropFiles = vi.fn();
    render(<WorkspaceBar {...base} onReorder={onReorder} onDropFiles={onDropFiles} />);
    const target = screen.getByTestId("workspace-tab-drop-w2");
    fireEvent.drop(target, {
      dataTransfer: {
        types: ["Files"],
        files: [],
        items: [],
        dropEffect: "none",
        getData: () => "",
      },
    });
    expect(onReorder).not.toHaveBeenCalled();
  });
});
