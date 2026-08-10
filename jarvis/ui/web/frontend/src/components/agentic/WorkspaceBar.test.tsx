import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { WorkspaceCard } from "@/lib/agenticIdeApi";
import { WorkspaceBar } from "./WorkspaceBar";

function card(index: number): WorkspaceCard {
  return {
    id: `w${index}`,
    folder: `C:/work/Workspace ${index}`,
    name: `Workspace ${index}`,
    branch: "main",
    terminals: 2,
    live_terminals: 2,
    focus_mode: false,
    created_at: index,
    last_active_at: index,
    active: index === 1,
  };
}

function renderBar(
  count: number,
  maxWorkspaces: number | null = null,
  embedded = false,
) {
  return render(
    <WorkspaceBar
      workspaces={Array.from({ length: count }, (_, index) => card(index + 1))}
      activeId="w1"
      addingNew={false}
      maxWorkspaces={maxWorkspaces}
      onSelect={vi.fn()}
      onAdd={vi.fn()}
      onRename={vi.fn(async () => true)}
      onClose={vi.fn()}
      embedded={embedded}
    />,
  );
}

describe("WorkspaceBar density", () => {
  it("keeps six named workspaces in one shrinking row without a scrollbar", () => {
    const bounds = vi
      .spyOn(HTMLElement.prototype, "getBoundingClientRect")
      .mockReturnValue({ width: 1200 } as DOMRect);
    renderBar(6);

    const bar = screen.getByTestId("workspace-bar");
    expect(bar.dataset.density).toBe("full");
    expect(bar.className).toContain("overflow-hidden");
    expect(bar.className).not.toContain("overflow-x-auto");
    expect(screen.getByText("Workspace 6").className).not.toContain("sr-only");
    expect((screen.getByTestId("workspace-add") as HTMLButtonElement).disabled).toBe(
      false,
    );
    bounds.mockRestore();
  });

  it("does not treat an unmeasurable zero-width mount as a narrow window", () => {
    const bounds = vi
      .spyOn(HTMLElement.prototype, "getBoundingClientRect")
      .mockReturnValue({ width: 0 } as DOMRect);
    renderBar(2);

    expect(screen.getByTestId("workspace-bar").dataset.density).toBe("full");
    expect(screen.getByText("Workspace 2").className).not.toContain("sr-only");
    bounds.mockRestore();
  });

  it("compacts additional workspaces to folder icons and ordinal numbers", () => {
    const bounds = vi
      .spyOn(HTMLElement.prototype, "getBoundingClientRect")
      .mockReturnValue({ width: 800 } as DOMRect);
    renderBar(9);

    expect(screen.getByTestId("workspace-bar").dataset.density).toBe("compact");
    for (let index = 1; index <= 9; index += 1) {
      expect(screen.getByTestId(`workspace-ordinal-w${index}`).textContent).toBe(
        String(index),
      );
      expect(screen.getByText(`Workspace ${index}`).className).toContain("sr-only");
    }
    expect((screen.getByTestId("workspace-add") as HTMLButtonElement).disabled).toBe(
      false,
    );
    expect(screen.getByText("New workspace").className).toContain("sr-only");
    bounds.mockRestore();
  });

  it("renders beyond the former cap and keeps adding available", () => {
    renderBar(15);

    expect(screen.getAllByRole("tab")).toHaveLength(16);
    expect(screen.getByTestId("workspace-ordinal-w15").textContent).toBe("15");
    expect((screen.getByTestId("workspace-add") as HTMLButtonElement).disabled).toBe(
      false,
    );
  });

  it("still honours a finite cap from an older backend", () => {
    renderBar(12, 12);

    expect((screen.getByTestId("workspace-add") as HTMLButtonElement).disabled).toBe(
      true,
    );
  });

  it("drops the folder glyph tier when the measured bar is especially narrow", () => {
    const bounds = vi
      .spyOn(HTMLElement.prototype, "getBoundingClientRect")
      .mockReturnValue({ width: 200 } as DOMRect);
    renderBar(9, 12, true);

    expect(screen.getByTestId("workspace-bar").dataset.density).toBe("ordinal");
    expect(screen.getByTestId("workspace-ordinal-w9").textContent).toBe("9");
    expect(screen.getByTestId("workspace-bar").parentElement?.className).toContain(
      "min-w-[12rem]",
    );
    bounds.mockRestore();
  });

  it("moves focus across the workspace tabs with arrow, home and end keys", () => {
    renderBar(4);
    const tabs = screen.getAllByRole("tab") as HTMLButtonElement[];

    fireEvent.focus(tabs[0]);
    fireEvent.keyDown(tabs[0], { key: "ArrowRight" });
    expect(document.activeElement).toBe(tabs[1]);
    fireEvent.keyDown(tabs[1], { key: "End" });
    expect(document.activeElement).toBe(tabs[4]);
    fireEvent.keyDown(tabs[4], { key: "Home" });
    expect(document.activeElement).toBe(tabs[0]);
    fireEvent.keyDown(tabs[0], { key: "ArrowLeft" });
    expect(document.activeElement).toBe(tabs[4]);
  });

  it("keeps exactly one declarative tab stop after an external workspace switch", () => {
    const props = {
      workspaces: Array.from({ length: 4 }, (_, index) => card(index + 1)),
      addingNew: false,
      maxWorkspaces: 12,
      onSelect: vi.fn(),
      onAdd: vi.fn(),
      onRename: vi.fn(async () => true),
      onClose: vi.fn(),
    };
    const { rerender } = render(<WorkspaceBar {...props} activeId="w1" />);
    const tabs = screen.getAllByRole("tab") as HTMLButtonElement[];
    fireEvent.focus(tabs[0]);
    fireEvent.keyDown(tabs[0], { key: "ArrowRight" });

    rerender(<WorkspaceBar {...props} activeId="w3" />);

    const currentTabs = screen.getAllByRole("tab") as HTMLButtonElement[];
    expect(currentTabs.filter((tab) => tab.tabIndex === 0)).toEqual([currentTabs[2]]);
  });

  it("preserves roved focus when a refresh clones the same workspace list", () => {
    const callbacks = {
      addingNew: false,
      maxWorkspaces: 12,
      onSelect: vi.fn(),
      onAdd: vi.fn(),
      onRename: vi.fn(async () => true),
      onClose: vi.fn(),
    };
    const initial = Array.from({ length: 4 }, (_, index) => card(index + 1));
    const { rerender } = render(
      <WorkspaceBar {...callbacks} workspaces={initial} activeId="w1" />,
    );
    const tabs = screen.getAllByRole("tab") as HTMLButtonElement[];
    fireEvent.focus(tabs[0]);
    fireEvent.keyDown(tabs[0], { key: "ArrowRight" });

    rerender(
      <WorkspaceBar
        {...callbacks}
        workspaces={initial.map((workspace) => ({ ...workspace }))}
        activeId="w1"
      />,
    );

    const refreshedTabs = screen.getAllByRole("tab") as HTMLButtonElement[];
    expect(refreshedTabs.filter((tab) => tab.tabIndex === 0)).toEqual([
      refreshedTabs[1],
    ]);
  });
});
