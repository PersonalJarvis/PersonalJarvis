import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { WorkspaceTerminalHeader } from "./WorkspaceTerminalHeader";
import { PANE_BRAND, PANE_CHROME, PANE_TILE, themeFor } from "./terminalThemes";

const BASE = { name: "Dana", agent: "codex", displayName: "Codex", appearance: "dark" as const, status: "live" as const };

function pressPointer(target: Element, button = 0) {
  fireEvent(target, new MouseEvent("pointerdown", { bubbles: true, button }));
}

describe("compact workspace terminal header", () => {
  it("offers a fork button and menu entry, and shows a worktree fork's branch", () => {
    const fork = vi.fn();
    render(<WorkspaceTerminalHeader {...BASE} onFork={fork} branch="dana-fix-login" />);
    fireEvent.click(screen.getByRole("button", { name: "Fork Dana" }));
    expect(fork).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("pane-branch-Dana").textContent).toBe("dana-fix-login");
    fireEvent.click(screen.getByRole("button", { name: "More actions for Dana" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Fork…" }));
    expect(fork).toHaveBeenCalledTimes(2);
  });

  it("uses the name and accurate status without a verbose toolbar", () => {
    render(<WorkspaceTerminalHeader {...BASE} onOpenConversation={() => {}} />);
    const header = screen.getByTestId("workspace-terminal-header-Dana");
    expect(header.className).toContain("h-9");
    expect(within(header).getByText("Dana")).toBeTruthy();
    expect(within(header).getByRole("img", { name: "Dana: live" }).style.background).toBeTruthy();
    expect(screen.getByTestId("agent-mark-codex").dataset.logo).toContain("openai.svg");
    expect(within(header).getAllByRole("button")).toHaveLength(5);
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("starts drag from the entire title but never from header controls", () => {
    const arrange = vi.fn();
    render(<WorkspaceTerminalHeader {...BASE} onArrangeStart={arrange} onOpenConversation={() => {}} />);
    pressPointer(screen.getByText("Dana"));
    expect(arrange).toHaveBeenCalledTimes(1);
    pressPointer(screen.getByTestId("workspace-terminal-header-Dana"));
    expect(arrange).toHaveBeenCalledTimes(2);
    pressPointer(screen.getByRole("button", { name: "More actions for Dana" }));
    pressPointer(screen.getByText("Dana"), 2);
    expect(arrange).toHaveBeenCalledTimes(2);
    const title = screen.getByRole("button", { name: "Move Dana" });
    expect(title.dataset.ideDragHandle).toBe("true");
    expect(title.getAttribute("aria-keyshortcuts")).toContain("Alt+ArrowLeft");
  });

  it("wires maximize, add and close and honors the eight-agent limit", () => {
    const maximize = vi.fn(), add = vi.fn(), close = vi.fn();
    const { rerender } = render(<WorkspaceTerminalHeader {...BASE} onToggleMaximize={maximize} onAdd={add} onClose={close} />);
    fireEvent.click(screen.getByRole("button", { name: "Maximize Dana" }));
    fireEvent.click(screen.getByRole("button", { name: "Add agent beside Dana" }));
    fireEvent.click(screen.getByRole("button", { name: "Close Dana" }));
    expect(maximize).toHaveBeenCalledOnce();
    expect(add).toHaveBeenCalledOnce();
    expect(close).toHaveBeenCalledOnce();
    rerender(<WorkspaceTerminalHeader {...BASE} maximized addDisabled onToggleMaximize={maximize} onAdd={add} onClose={close} />);
    expect(screen.getByRole("button", { name: "Restore Dana" })).toBeTruthy();
    expect((screen.getByRole("button", { name: "Add agent beside Dana" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("selects on keyboard activation without stealing move focus or repeating pointer selection", () => {
    const activate = vi.fn();
    render(<WorkspaceTerminalHeader {...BASE} onActivate={activate} onArrangeStart={(event) => event.preventDefault()} />);
    const title = screen.getByRole("button", { name: "Move Dana" });
    fireEvent.click(title, { detail: 1 });
    expect(activate).not.toHaveBeenCalled();
    title.focus();
    fireEvent.click(title, { detail: 0 });
    expect(activate).toHaveBeenCalledOnce();
    expect(document.activeElement).toBe(title);
  });

  it("opens the pane menu on right-click with split, maximize and close actions", () => {
    const add = vi.fn(), maximize = vi.fn(), close = vi.fn(), activate = vi.fn();
    render(<WorkspaceTerminalHeader {...BASE} onAdd={add} onToggleMaximize={maximize} onClose={close} onActivate={activate} />);
    const event = new MouseEvent("contextmenu", { bubbles: true, cancelable: true, clientX: 40, clientY: 30 });
    fireEvent(screen.getByText("Dana"), event);
    expect(event.defaultPrevented).toBe(true);
    expect(activate).toHaveBeenCalled();
    const menu = screen.getByRole("menu", { name: "Actions for Dana" });
    expect(within(menu).getAllByRole("menuitem").map((item) => item.textContent)).toEqual(expect.arrayContaining(
      ["Split right…", "Split down…", "Split left…", "Split up…", "Maximize", "Close pane"]));
    fireEvent.click(within(menu).getByRole("menuitem", { name: "Split left…" }));
    expect(add).toHaveBeenCalledWith("left");
    fireEvent(screen.getByText("Dana"), new MouseEvent("contextmenu", { bubbles: true, cancelable: true }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Split up…" }));
    expect(add).toHaveBeenLastCalledWith("above");
    fireEvent(screen.getByText("Dana"), new MouseEvent("contextmenu", { bubbles: true, cancelable: true }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Close pane" }));
    expect(close).toHaveBeenCalledTimes(1);
  });

  it("hides split actions once the workspace is full", () => {
    render(<WorkspaceTerminalHeader {...BASE} addDisabled onAdd={vi.fn()} />);
    fireEvent(screen.getByText("Dana"), new MouseEvent("contextmenu", { bubbles: true, cancelable: true }));
    expect(screen.queryByRole("menuitem", { name: "Split right…" })).toBeNull();
  });

  it("keeps menu focus navigable and restores focus on Escape", () => {
    render(<WorkspaceTerminalHeader {...BASE} onRename={async () => true} onOpenConversation={() => {}} />);
    const more = screen.getByRole("button", { name: "More actions for Dana" });
    fireEvent.click(more);
    expect(document.activeElement).toBe(screen.getByRole("menuitem", { name: "Rename" }));
    fireEvent.keyDown(document.activeElement!, { key: "ArrowDown" });
    expect(document.activeElement).toBe(screen.getByRole("menuitem", { name: "Conversation history" }));
    fireEvent.keyDown(document.activeElement!, { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
    expect(document.activeElement).toBe(more);
  });

  it("closes the overflow after opening history and exposes restart only when stopped", () => {
    const history = vi.fn(), restart = vi.fn();
    const { rerender } = render(<WorkspaceTerminalHeader {...BASE} onOpenConversation={history} onRestart={restart} />);
    fireEvent.click(screen.getByRole("button", { name: "More actions for Dana" }));
    expect(screen.queryByRole("menuitem", { name: "Restart agent" })).toBeNull();
    fireEvent.click(screen.getByRole("menuitem", { name: "Conversation history" }));
    expect(history).toHaveBeenCalledOnce();
    expect(screen.queryByRole("menu")).toBeNull();
    rerender(<WorkspaceTerminalHeader {...BASE} status="exited" onOpenConversation={history} onRestart={restart} />);
    fireEvent.click(screen.getByRole("button", { name: "More actions for Dana" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Restart agent" }));
    expect(restart).toHaveBeenCalledOnce();
  });

  it("keeps a refused rename editable and permits cancellation", async () => {
    const rename = vi.fn().mockResolvedValue(false);
    render(<WorkspaceTerminalHeader {...BASE} onRename={rename} />);
    fireEvent.click(screen.getByRole("button", { name: "More actions for Dana" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Rename" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Name for Dana" }), { target: { value: "Installer" } });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Save name" })); });
    expect(rename).toHaveBeenCalledWith("Installer");
    expect((screen.getByRole("textbox", { name: "Name for Dana" }) as HTMLInputElement).value).toBe("Installer");
    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Escape" });
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it("uses the terminal's light appearance even within a dark app", () => {
    render(<div className="dark"><WorkspaceTerminalHeader {...BASE} appearance="light" onOpenConversation={() => {}} /></div>);
    const header = screen.getByTestId("workspace-terminal-header-Dana");
    expect(header.style.getPropertyValue("--pane-ink")).toBe(PANE_BRAND.light.ink);
    expect(header.style.background).toBe(PANE_CHROME.light.shell);
    const mark = screen.getByTestId("agent-mark-codex");
    expect(mark.className).toContain("[&>.bg-foreground]:!bg-[color:var(--pane-ink)]");
    const dot = screen.getByRole("img", { name: "Dana: live" });
    const expected = document.createElement("span");
    expected.style.background = themeFor("light").green ?? "";
    expect(dot.style.background).toBe(expected.style.background);
    fireEvent.click(screen.getByRole("button", { name: "More actions for Dana" }));
    expect(screen.getByRole("menu").style.background).toBe("rgb(255, 255, 255)");
  });

  it("dismisses the menu when the user starts interacting elsewhere", () => {
    render(<><WorkspaceTerminalHeader {...BASE} onOpenConversation={() => {}} /><button>Outside</button></>);
    fireEvent.click(screen.getByRole("button", { name: "More actions for Dana" }));
    pressPointer(screen.getByRole("button", { name: "Outside" }));
    expect(screen.queryByRole("menu")).toBeNull();
  });
});

describe("minimal tile title row", () => {
  it("keeps every control of the card header in a slimmer, square row", () => {
    render(<WorkspaceTerminalHeader {...BASE} variant="tile" onFork={() => {}} onAdd={() => {}} onToggleMaximize={() => {}} onClose={() => {}} />);
    const header = screen.getByTestId("workspace-terminal-header-Dana");
    expect(header.className).toContain("h-7");
    expect(header.className).not.toContain("h-9");
    expect(within(header).getByTestId("pane-title-Dana").textContent).toBe("Dana");
    for (const label of ["More actions for Dana", "Maximize Dana", "Fork Dana", "Add agent beside Dana", "Close Dana"]) {
      expect(within(header).getByRole("button", { name: label }).className).toContain("rounded-none");
    }
  });

  it("lights the title of the pane in use in the signal hue", () => {
    const { rerender } = render(<WorkspaceTerminalHeader {...BASE} variant="tile" />);
    const expected = document.createElement("span");
    expected.style.color = PANE_BRAND.dark.inkMuted;
    expect(screen.getByTestId("pane-title-Dana").style.color).toBe(expected.style.color);
    rerender(<WorkspaceTerminalHeader {...BASE} variant="tile" focused />);
    expected.style.color = PANE_TILE.dark.focus;
    expect(screen.getByTestId("pane-title-Dana").style.color).toBe(expected.style.color);
    expect(screen.getByTestId("pane-title-Dana").className).toContain("font-semibold");
  });

  it("starts a move from the title and opens a square menu", () => {
    const arrange = vi.fn();
    render(<WorkspaceTerminalHeader {...BASE} variant="tile" onArrangeStart={arrange} onOpenConversation={() => {}} />);
    pressPointer(screen.getByText("Dana"));
    expect(arrange).toHaveBeenCalledTimes(1);
    pressPointer(screen.getByRole("button", { name: "More actions for Dana" }));
    expect(arrange).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "More actions for Dana" }));
    expect(screen.getByRole("menu").className).toContain("rounded-none");
  });
});
