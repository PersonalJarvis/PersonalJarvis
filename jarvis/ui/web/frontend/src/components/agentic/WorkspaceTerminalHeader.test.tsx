import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { WorkspaceTerminalHeader } from "./WorkspaceTerminalHeader";
import { PANE_BRAND, PANE_CHROME, themeFor } from "./terminalThemes";

const BASE = { name: "Dana", agent: "codex", displayName: "Codex", appearance: "dark" as const, status: "live" as const };

function pressPointer(target: Element, button = 0) {
  fireEvent(target, new MouseEvent("pointerdown", { bubbles: true, button }));
}

describe("compact workspace terminal header", () => {
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
