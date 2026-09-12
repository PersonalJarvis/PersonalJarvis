import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MainView } from "./MainView";
import { useEventStore } from "@/store/events";

vi.mock("@/views/ChatsSurface", () => ({
  ChatsSurface: () => <input aria-label="Unsent message" defaultValue="A draft" />,
}));
vi.mock("@/views/PluginsView", () => ({
  PluginsView: ({ inDialog }: { inDialog: boolean }) => <div>Catalog {String(inDialog)}</div>,
}));
vi.mock("@/views/SkillsView", () => ({ SkillsView: () => <input aria-label="Skill search" /> }));
vi.mock("@/views/McpsView", () => ({ McpsView: () => <div>MCP content</div> }));

beforeEach(() => { useEventStore.setState({ activeSection: "chats", solo: false, detachedViews: [] }); });
afterEach(() => { cleanup(); vi.clearAllMocks(); });

it("opens a bounded dialog while preserving the current draft and restores that view on close", async () => {
  render(<MainView />);
  const draft = screen.getByLabelText("Unsent message");
  fireEvent.change(draft, { target: { value: "Keep this draft" } });
  act(() => useEventStore.getState().setActiveSection("plugins"));
  await screen.findByRole("dialog", { name: "Plugins" });
  expect(screen.getByText("Catalog true")).toBeDefined();
  expect(screen.getByLabelText("Unsent message")).toBe(draft);
  fireEvent.click(screen.getByRole("button", { name: "Close" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(useEventStore.getState().activeSection).toBe("chats");
  expect((screen.getByLabelText("Unsent message") as HTMLInputElement).value).toBe("Keep this draft");
});

it("routes Skills and MCPs into the same dialog and keeps the original background", async () => {
  render(<MainView />);
  const draft = screen.getByLabelText("Unsent message");
  draft.focus();
  act(() => useEventStore.getState().setActiveSection("skills"));
  await screen.findByLabelText("Skill search");
  const dialog = screen.getByRole("dialog");
  expect(screen.getByRole("tab", { name: "Skills" }).getAttribute("aria-selected")).toBe("true");
  act(() => useEventStore.getState().setActiveSection("mcps"));
  await screen.findByText("MCP content");
  expect(screen.getByRole("dialog")).toBe(dialog);
  expect(screen.getByRole("tab", { name: "MCPs" }).getAttribute("aria-selected")).toBe("true");
  expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["Plugins", "MCPs", "Skills"]);
  fireEvent.mouseDown(screen.getByRole("tab", { name: "Plugins" }), { button: 0, ctrlKey: false });
  await screen.findByText("Catalog true");
  expect(useEventStore.getState().activeSection).toBe("plugins");
  expect(screen.getByLabelText("Unsent message")).toBe(draft);
  fireEvent.keyDown(document, { key: "Escape" });
  await waitFor(() => expect(useEventStore.getState().activeSection).toBe("chats"));
  await waitFor(() => expect(document.activeElement).toBe(draft));
});

it.each(["plugins", "mcps", "skills"] as const)("provides a home background and close action for direct %s navigation", async (activeSection) => {
  useEventStore.setState({ activeSection });
  render(<MainView />);
  await screen.findByRole("dialog");
  fireEvent.click(screen.getByRole("button", { name: "Close" }));
  await waitFor(() => expect(useEventStore.getState().activeSection).toBe("chats"));
});
