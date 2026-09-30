import { useEffect, useRef } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { WorkspaceTerminalHeader } from "./WorkspaceTerminalHeader";
import { usePaneContextMenu } from "./usePaneContextMenu";
import { PANE_CHROME } from "./terminalThemes";

afterEach(cleanup);

function Harness({ appearance = "dark", forwarded, selected, swap, enabled = true }: {
  appearance?: "light" | "dark"; forwarded: () => void; selected: () => void;
  swap?: () => void; enabled?: boolean;
}) {
  const menu = usePaneContextMenu(enabled);
  const host = useRef<HTMLDivElement>(null);
  // A native target listener models xterm consuming mouse events before React bubbles.
  useEffect(() => {
    const node = host.current!;
    node.addEventListener("mousedown", forwarded);
    return () => node.removeEventListener("mousedown", forwarded);
  }, [forwarded]);
  return <div onMouseDown={selected}>
    <WorkspaceTerminalHeader name="Dana" agent="codex" displayName="Codex" status="live"
      appearance={appearance} contextMenuRequest={menu.request} onSwapWithFocused={swap}
      sendRightClicks={menu.sendRightClicks} onToggleSendRightClicks={menu.toggleSendRightClicks}
      onRename={async () => true} onAdd={() => {}} onToggleMaximize={() => {}} onClose={() => {}} />
    <div {...menu.handlers}><div ref={host} data-testid="terminal-content" /></div>
  </div>;
}

describe("terminal surface context menu", () => {
  it.each(["light", "dark"] as const)("opens the pane actions in %s without selecting or sending a right-click", (appearance) => {
    const forwarded = vi.fn(), selected = vi.fn(), swap = vi.fn();
    render(<Harness appearance={appearance} forwarded={forwarded} selected={selected} swap={swap} />);
    const host = screen.getByTestId("terminal-content");
    fireEvent.mouseDown(host, { button: 2 });
    const event = new MouseEvent("contextmenu", { button: 2, bubbles: true, cancelable: true, clientX: 30, clientY: 40 });
    fireEvent(host, event);
    expect(event.defaultPrevented).toBe(true);
    expect(forwarded).not.toHaveBeenCalled();
    expect(selected).not.toHaveBeenCalled();
    const menu = screen.getByRole("menu");
    const expectedColor = document.createElement("div");
    expectedColor.style.background = PANE_CHROME[appearance].float;
    expect(menu.style.background).toBe(expectedColor.style.background);
    for (const name of ["Rename pane", "Swap with focused pane", "Split right…", "Split down…", "Zoom", "Close pane"])
      expect(screen.getByRole("menuitem", { name })).toBeTruthy();
    expect(screen.getByRole("menuitemcheckbox", { name: "Send right-clicks to pane" })).toBeTruthy();
    const swapItem = screen.getByRole("menuitem", { name: "Swap with focused pane" });
    fireEvent.mouseDown(swapItem);
    expect(selected).not.toHaveBeenCalled();
    fireEvent.click(swapItem);
    expect(swap).toHaveBeenCalledOnce();
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("forwards only after opt-in and retains Shift+right-click as a reversible escape hatch", () => {
    const forwarded = vi.fn(), selected = vi.fn();
    render(<Harness forwarded={forwarded} selected={selected} />);
    const host = screen.getByTestId("terminal-content");
    fireEvent.contextMenu(host);
    expect((screen.getByRole("menuitem", { name: "Swap with focused pane" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("menuitemcheckbox"));
    fireEvent.mouseDown(host, { button: 2 });
    expect(forwarded).toHaveBeenCalledOnce();
    fireEvent.contextMenu(host);
    expect(screen.queryByRole("menu")).toBeNull();
    fireEvent.mouseDown(host, { button: 2, shiftKey: true });
    fireEvent.contextMenu(host, { shiftKey: true });
    expect(forwarded).toHaveBeenCalledOnce();
    expect(screen.getByRole("menuitemcheckbox").getAttribute("aria-checked")).toBe("true");
    fireEvent.click(screen.getByRole("menuitemcheckbox"));
    fireEvent.contextMenu(host);
    expect(screen.getByRole("menuitemcheckbox").getAttribute("aria-checked")).toBe("false");
    fireEvent.keyDown(screen.getByRole("menuitem", { name: "Close pane" }), { key: "ArrowUp" });
    // Prompt history remains between forwarding and close; Home/End skip disabled actions.
    fireEvent.keyDown(document.activeElement!, { key: "Home" });
    fireEvent.keyDown(document.activeElement!, { key: "ArrowDown" });
    expect(document.activeElement).toBe(screen.getByRole("menuitem", { name: "Split right…" }));
  });

  it("leaves legacy terminal gestures untouched", () => {
    const forwarded = vi.fn();
    render(<Harness enabled={false} forwarded={forwarded} selected={() => {}} />);
    const host = screen.getByTestId("terminal-content");
    fireEvent.mouseDown(host, { button: 2 });
    const event = new MouseEvent("contextmenu", { bubbles: true, cancelable: true });
    fireEvent(host, event);
    expect(forwarded).toHaveBeenCalledOnce();
    expect(event.defaultPrevented).toBe(false);
    expect(screen.queryByRole("menu")).toBeNull();
  });
});
