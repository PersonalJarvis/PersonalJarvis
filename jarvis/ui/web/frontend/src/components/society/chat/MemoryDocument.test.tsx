import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { MemoryChanges, MemoryMarkdown, readableMemory } from "./MemoryDocument";
import { MemoryUpdateNotice } from "./MemoryUpdateNotice";
import type { NoticeItem } from "@/components/agentchat/reduce";

vi.mock("@/i18n", () => ({ useT: () => (key: string) => key }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const page = (text: string, revision: number) => `---\ntype: society\ntitle: Memory\n---\n\n<!-- memory-entry: {"id":"entry-1","revision":${revision}} -->\n${text}\n`;

it("renders readable Markdown and hides storage metadata", () => {
  render(<MemoryMarkdown text={page("# Preferences\n\nUse **plain text** reports.", 1)} />);
  expect(screen.getByRole("heading", { name: "Preferences" })).toBeTruthy();
  expect(screen.getByText("plain text").tagName).toBe("STRONG");
  expect(document.body.textContent).not.toContain("memory-entry");
  expect(document.body.textContent).not.toContain("type: society");
});

it("marks changed content green/red without painting changed revisions as user content", () => {
  const { container } = render(<MemoryChanges before={page("Use paragraphs.", 1)} after={page("Use bullet lists.", 2)} />);
  expect(container.querySelector('[data-change="add"]')?.textContent).toContain("Use bullet lists.");
  expect(container.querySelector('[data-change="del"]')?.textContent).toContain("Use paragraphs.");
  expect(container.querySelector(".diff-line-add")).toBeTruthy();
  expect(container.querySelector(".diff-line-del")).toBeTruthy();
  expect(container.textContent).not.toContain("revision");
  expect(readableMemory(page("Same content", 1))).toBe(readableMemory(page("Same content", 2)));
});

it("opens a saved change from one chat notice and keeps it distinct from the current file", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ content: page("Current latest content.", 3), path: "society/scout/memory.md", updated_ms: 3 }))));
  const item: NoticeItem = { type: "notice", id: "notice-1", kind: "memory_updated", text: "Memory updated", agentName: "Scout", agentId: "scout", status: "done", tsMs: 1, resolved: "", data: { path: "society/scout/memory.md", before: page("Old preference.", 1), after: page("New preference.", 2) } };
  render(<MemoryUpdateNotice item={item} />);
  fireEvent.click(screen.getByRole("button", { name: /society.chat.memory_updated/ }));
  await screen.findByRole("dialog");
  expect(screen.getByText("Old preference.")).toBeTruthy();
  expect(screen.getByText("New preference.")).toBeTruthy();
  expect(screen.queryByText("Current latest content.")).toBeNull();
  fireEvent.mouseDown(screen.getByRole("tab", { name: "society.chat.memory_current" }), { button: 0, ctrlKey: false });
  await screen.findByText("Current latest content.");
  expect(screen.queryByText("New preference.")).toBeNull();
  fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});

it("does not expose a different agent's file through a forged notice", () => {
  const item: NoticeItem = { type: "notice", id: "notice-1", kind: "memory_updated", text: "Memory updated", agentName: "Scout", agentId: "scout", status: "done", tsMs: 1, resolved: "", data: { path: "society/other/memory.md", before: "", after: "Private" } };
  render(<MemoryUpdateNotice item={item} />);
  expect(screen.queryByRole("button")).toBeNull();
});
