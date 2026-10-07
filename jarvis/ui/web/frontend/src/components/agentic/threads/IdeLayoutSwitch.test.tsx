import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { IdeLayoutSwitch } from "./IdeLayoutSwitch";
import { useIdeSidePanelStore } from "@/store/ideSidePanel";
import { useIdeThreadsStore } from "@/store/ideThreads";

beforeEach(() => {
  localStorage.clear();
  useIdeThreadsStore.setState({ layout: "grid" });
  useIdeSidePanelStore.setState({ open: false, tabs: ["agents"], active: "agents", terminals: [], maximized: false, inUse: false });
});

afterEach(cleanup);

const checked = (id: string) => screen.getByTestId(`ide-layout-${id}`).getAttribute("aria-checked");

describe("IdeLayoutSwitch", () => {
  it("offers grid, threads and the Jarvis Verse as icon buttons", () => {
    render(<IdeLayoutSwitch />);
    expect(screen.getAllByRole("radio").map((button) => button.getAttribute("aria-label")))
      .toEqual(["Terminal grid", "Threads", "Jarvis Verse"]);
    for (const button of screen.getAllByRole("radio")) expect(button.textContent).toBe("");
    expect(checked("grid")).toBe("true");
  });

  it("opens the Verse over the whole view and puts the panel back on leaving", () => {
    render(<IdeLayoutSwitch />);
    fireEvent.click(screen.getByTestId("ide-layout-verse"));
    let panel = useIdeSidePanelStore.getState();
    expect(panel).toMatchObject({ open: true, active: "office", maximized: true });
    expect(checked("verse")).toBe("true");

    fireEvent.click(screen.getByTestId("ide-layout-threads"));
    panel = useIdeSidePanelStore.getState();
    expect(panel).toMatchObject({ open: false, maximized: false, tabs: ["agents"] });
    expect(useIdeThreadsStore.getState().layout).toBe("threads");
    expect(checked("threads")).toBe("true");
  });

  it("returns to the tab that was in front when the panel was already open", () => {
    useIdeSidePanelStore.setState({ open: true, tabs: ["agents", "git"], active: "git" });
    render(<IdeLayoutSwitch />);
    fireEvent.click(screen.getByTestId("ide-layout-verse"));
    fireEvent.click(screen.getByTestId("ide-layout-grid"));
    expect(useIdeSidePanelStore.getState()).toMatchObject({ open: true, active: "git", maximized: false, tabs: ["agents", "git"] });
  });
});
