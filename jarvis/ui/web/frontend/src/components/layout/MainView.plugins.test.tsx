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

it("does not replace Skills behind the dialog when the shared navigation state changes", async () => {
  useEventStore.setState({ activeSection: "skills" });
  render(<MainView />);
  const search = await screen.findByLabelText("Skill search");
  fireEvent.change(search, { target: { value: "remembered search" } });
  act(() => useEventStore.getState().setActiveSection("plugins"));
  await screen.findByRole("dialog");
  expect(screen.getByLabelText("Skill search")).toBe(search);
  fireEvent.keyDown(document, { key: "Escape" });
  await waitFor(() => expect(useEventStore.getState().activeSection).toBe("skills"));
  expect((screen.getByLabelText("Skill search") as HTMLInputElement).value).toBe("remembered search");
});

it("provides a home background and a working close action for direct plugin navigation", async () => {
  useEventStore.setState({ activeSection: "plugins" });
  render(<MainView />);
  await screen.findByRole("dialog");
  fireEvent.click(screen.getByRole("button", { name: "Close" }));
  await waitFor(() => expect(useEventStore.getState().activeSection).toBe("chats"));
});
