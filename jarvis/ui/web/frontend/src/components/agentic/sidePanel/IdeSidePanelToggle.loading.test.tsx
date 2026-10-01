import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { IdeSidePanelToggle } from "./IdeSidePanelToggle";
import { SIDE_PANEL_ID } from "./sidePanelIds";
import { useEventStore } from "@/store/events";
import { useIdeSidePanelStore } from "@/store/ideSidePanel";

const loaded = vi.hoisted(() => ({ panels: 0 }));
vi.mock("./IdeSidePanel", () => {
  loaded.panels += 1;
  return { SIDE_PANEL_ID: "ide-side-panel" };
});
afterEach(cleanup);

it("keeps caption controls functional without importing the IDE panel or its tab implementations", () => {
  useEventStore.setState({ activeSection: "chats" });
  useIdeSidePanelStore.setState({ open: false });
  render(<IdeSidePanelToggle />);
  expect(screen.queryByTestId("ide-side-panel-toggle")).toBeNull();
  expect(loaded.panels).toBe(0);
  act(() => useEventStore.setState({ activeSection: "agentic-ide" }));
  const toggle = screen.getByTestId("ide-side-panel-toggle");
  expect(toggle.getAttribute("aria-controls")).toBe(SIDE_PANEL_ID);
  fireEvent.click(toggle);
  expect(toggle.getAttribute("aria-expanded")).toBe("true");
  fireEvent.click(toggle);
  expect(toggle.getAttribute("aria-expanded")).toBe("false");
  expect(loaded.panels).toBe(0);
});
