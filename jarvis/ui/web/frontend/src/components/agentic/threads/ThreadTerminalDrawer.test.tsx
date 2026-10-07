import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DEFAULT_DRAWER_HEIGHT, MAX_FOLDER_SHELLS, useThreadTerminalsStore } from "@/store/threadTerminals";
import { ThreadTerminalDrawer } from "./ThreadTerminalDrawer";
import { ThreadTerminalToggle } from "./ThreadTerminalToggle";

vi.mock("@/components/workspace/WorkspaceTerminal", () => ({
  WorkspaceTerminal: ({ paneKey, folder, active, title }: { paneKey: string; folder: string; active: boolean; title: string }) => (
    <div data-testid={`shell-${paneKey}`} data-folder={folder} data-active={String(active)}>{title}</div>
  ),
}));

function Harness({ onScreen = true }: { onScreen?: boolean }) {
  return (
    <>
      <ThreadTerminalToggle />
      <ThreadTerminalDrawer onScreen={onScreen} />
    </>
  );
}

const toggle = () => screen.getByTestId("thread-terminal-toggle") as HTMLButtonElement;
const shells = () => useThreadTerminalsStore.getState().shells;

beforeEach(() => {
  localStorage.clear();
  useThreadTerminalsStore.setState({ folder: "/code/app", open: false, shells: [], active: {}, height: DEFAULT_DRAWER_HEIGHT });
});

afterEach(cleanup);

describe("ThreadTerminalDrawer", () => {
  it("starts the folder's first shell on the first press and hides it on the next", () => {
    render(<Harness />);
    expect(screen.queryByTestId("thread-terminal-drawer")).toBeNull();
    expect(toggle().getAttribute("aria-pressed")).toBe("false");

    fireEvent.click(toggle());
    expect(shells()).toHaveLength(1);
    const [first] = shells();
    const node = screen.getByTestId(`shell-${first.id}`);
    expect(node.dataset.folder).toBe("/code/app");
    expect(node.dataset.active).toBe("true");
    expect(screen.getByTestId("thread-terminal-drawer").className).toContain("flex");
    expect(toggle().getAttribute("aria-pressed")).toBe("true");
    expect(toggle().getAttribute("aria-label")).toBe("Hide terminal");

    // Hidden, not ended: the same shell comes back on the next press.
    fireEvent.click(toggle());
    expect(screen.getByTestId("thread-terminal-drawer").className).toContain("hidden");
    expect(screen.getByTestId(`shell-${first.id}`)).toBe(node);
    expect(node.dataset.active).toBe("false");
    fireEvent.click(toggle());
    expect(shells()).toHaveLength(1);
    expect(screen.getByTestId(`shell-${first.id}`)).toBe(node);
    expect(node.dataset.active).toBe("true");
  });

  it("is unavailable without a project folder", () => {
    useThreadTerminalsStore.setState({ folder: "" });
    render(<Harness />);
    expect(toggle().disabled).toBe(true);
    fireEvent.click(toggle());
    expect(shells()).toHaveLength(0);
  });

  it("adds shells as tabs and goes down with the folder's last one", () => {
    render(<Harness />);
    fireEvent.click(toggle());
    fireEvent.click(screen.getByTestId("thread-terminal-new"));
    expect(screen.getByRole("tab", { name: /Terminal 1/ })).toBeTruthy();
    expect(screen.getByRole("tab", { name: /Terminal 2/ }).getAttribute("aria-selected")).toBe("true");

    fireEvent.click(screen.getByTestId("thread-terminal-close-2"));
    expect(shells()).toHaveLength(1);
    expect(screen.getByRole("tab", { name: /Terminal 1/ }).getAttribute("aria-selected")).toBe("true");
    fireEvent.click(screen.getByTestId("thread-terminal-close-1"));
    expect(shells()).toHaveLength(0);
    expect(useThreadTerminalsStore.getState().open).toBe(false);
    expect(screen.queryByTestId("thread-terminal-drawer")).toBeNull();
  });

  it("keeps another folder's shells running but shows only the open thread's", () => {
    render(<Harness />);
    fireEvent.click(toggle());
    const [app] = shells();
    const appNode = screen.getByTestId(`shell-${app.id}`);

    act(() => useThreadTerminalsStore.getState().setFolder("/code/worktree"));
    // The new folder has no shell yet: the drawer stays down until asked.
    expect(screen.getByTestId("thread-terminal-drawer").className).toContain("hidden");
    expect(toggle().getAttribute("aria-pressed")).toBe("false");
    expect(appNode.dataset.active).toBe("false");
    fireEvent.click(toggle());
    expect(shells().map((shell) => shell.folder)).toEqual(["/code/app", "/code/worktree"]);
    expect(screen.getAllByRole("tab")).toHaveLength(1);

    act(() => useThreadTerminalsStore.getState().setFolder("/code/app"));
    expect(screen.getByTestId(`shell-${app.id}`)).toBe(appNode);
    expect(appNode.dataset.active).toBe("true");
  });

  it("lets no shell take focus while the drawer is off screen", () => {
    render(<Harness onScreen={false} />);
    fireEvent.click(toggle());
    expect(screen.getByTestId(`shell-${shells()[0].id}`).dataset.active).toBe("false");
  });

  it("caps the shells of one folder", () => {
    render(<Harness />);
    fireEvent.click(toggle());
    for (let index = 1; index < MAX_FOLDER_SHELLS; index += 1) fireEvent.click(screen.getByTestId("thread-terminal-new"));
    expect(shells()).toHaveLength(MAX_FOLDER_SHELLS);
    expect((screen.getByTestId("thread-terminal-new") as HTMLButtonElement).disabled).toBe(true);
  });

  it("resizes from its top edge and keeps the height, but no shell, for the next session", () => {
    render(<Harness />);
    fireEvent.click(toggle());
    const edge = screen.getByRole("separator", { name: "Resize the terminal" });
    edge.setPointerCapture = () => {};
    // jsdom has no PointerEvent; a MouseEvent of the pointer type carries the position.
    const pointer = (type: string, clientY: number) => fireEvent(edge, new MouseEvent(type, { bubbles: true, button: 0, clientY }));
    pointer("pointerdown", 600);
    pointer("pointermove", 500);
    pointer("pointerup", 500);
    expect(useThreadTerminalsStore.getState().height).toBe(DEFAULT_DRAWER_HEIGHT + 100);
    expect(localStorage.getItem("jarvis.ide.threadDrawerHeight.v1")).toBe(String(DEFAULT_DRAWER_HEIGHT + 100));
    expect(JSON.stringify(localStorage)).not.toContain("thread-shell-");
  });
});
