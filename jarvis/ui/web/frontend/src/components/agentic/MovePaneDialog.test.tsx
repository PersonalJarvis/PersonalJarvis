/**
 * The place picker for a pane moving into another workspace.
 *
 * Its promise is that nothing it offers is refused later and that what it
 * shows is what will happen: the tile pointed at splits in place to show the
 * moved pane, a side with no room says so instead of being offered, a full
 * workspace cannot be confirmed, and what is sent names the target pane by
 * identity.
 */
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
const api = vi.hoisted(() => ({ layout: vi.fn() }));
vi.mock("@/lib/agenticIdeApi", () => ({ fetchWorkspaceLayout: api.layout }));
import { MovePaneDialog, type MovePaneRequest } from "./MovePaneDialog";
import { balancedLayout } from "./workspaceDocking";

const pane = (name: string) => ({ key: name.toLowerCase(), name, agent: "codex", display_name: "Codex", history_id: `h-${name}`, title: `Work of ${name}` });
const workspace = (names: string[], layout = balancedLayout(names.map((name) => name.toLowerCase())), max = 16) =>
  ({ id: "w2", name: "Blog", layout, max_terminals: max, terminals: names.map(pane) });
const request: MovePaneRequest = { pane: { name: "T9", agent: "claude", displayName: "Claude Code" }, target: { id: "w2", name: "Blog" } };
const status = () => screen.getByRole("status").textContent;

beforeEach(() => { vi.clearAllMocks(); });
afterEach(cleanup);

it("draws the target panes with their task and starts on the automatic place", async () => {
  api.layout.mockResolvedValue(workspace(["T1", "T2"]));
  const onConfirm = vi.fn();
  render(<MovePaneDialog request={request} busy={false} onCancel={() => {}} onConfirm={onConfirm} />);

  expect(await screen.findByText("Work of T1")).toBeTruthy();
  expect(screen.getByTestId("move-pane-auto").getAttribute("aria-pressed")).toBe("true");
  expect(status()).toContain("Automatic: T9 joins the right edge");
  fireEvent.click(screen.getByTestId("move-pane-confirm"));

  expect(api.layout).toHaveBeenCalledWith("w2");
  expect(onConfirm).toHaveBeenCalledExactlyOnceWith(null);
});

it("splits the pointed-at tile to preview the place, and keeps it on click", async () => {
  api.layout.mockResolvedValue(workspace(["T1", "T2"]));
  const onConfirm = vi.fn();
  render(<MovePaneDialog request={request} busy={false} onCancel={() => {}} onConfirm={onConfirm} />);

  const left = await screen.findByRole("button", { name: "Place T9 left of T2" });
  fireEvent.mouseEnter(left);
  expect(screen.getByTestId("move-pane-tile-T2").dataset.lit).toBe("left");
  // The pane keeps its own label beside the preview.
  expect(screen.getByText("Work of T2")).toBeTruthy();
  expect(status()).toBe("Left of T2: T9 takes half of its space.");

  fireEvent.click(left);
  fireEvent.mouseLeave(screen.getByTestId("move-pane-map").parentElement!);
  expect(screen.getByTestId("move-pane-tile-T2").dataset.lit).toBe("left");
  expect(screen.getByTestId("move-pane-auto").getAttribute("aria-pressed")).toBe("false");
  fireEvent.click(screen.getByTestId("move-pane-confirm"));

  expect(onConfirm).toHaveBeenCalledExactlyOnceWith({ anchor: "pane:h-T2", side: "left" });
});

it("goes back to automatic from the edge column", async () => {
  api.layout.mockResolvedValue(workspace(["T1"]));
  const onConfirm = vi.fn();
  render(<MovePaneDialog request={request} busy={false} onCancel={() => {}} onConfirm={onConfirm} />);

  fireEvent.click(await screen.findByRole("button", { name: "Place T9 below T1" }));
  fireEvent.click(screen.getByTestId("move-pane-auto"));
  fireEvent.click(screen.getByTestId("move-pane-confirm"));

  expect(onConfirm).toHaveBeenCalledExactlyOnceWith(null);
});

it("says a side has no room instead of taking it", async () => {
  const row = ["T1", "T2", "T3", "T4"];
  api.layout.mockResolvedValue(workspace(row, { direction: "row", children: row.map((name) => ({ pane: name.toLowerCase() })), weights: [1, 1, 1, 1] }));
  const onConfirm = vi.fn();
  render(<MovePaneDialog request={request} busy={false} onCancel={() => {}} onConfirm={onConfirm} />);

  // Four columns already: a fifth beside any of them does not fit, a second row does.
  const right = await screen.findByRole("button", { name: "Place T9 right of T1" });
  expect(right.getAttribute("aria-disabled")).toBe("true");
  fireEvent.mouseEnter(right);
  expect(screen.getByText("No room")).toBeTruthy();
  expect(status()).toContain("No room right of T1");
  fireEvent.click(right);
  expect(screen.getByTestId("move-pane-auto").getAttribute("aria-pressed")).toBe("true");
  expect(screen.getByRole("button", { name: "Place T9 below T1" }).getAttribute("aria-disabled")).toBe("false");
});

it("lets arrow keys pick a side of the focused pane", async () => {
  api.layout.mockResolvedValue(workspace(["T1", "T2"]));
  const onConfirm = vi.fn();
  render(<MovePaneDialog request={request} busy={false} onCancel={() => {}} onConfirm={onConfirm} />);

  await screen.findByText("Work of T1");
  fireEvent.keyDown(screen.getByTestId("move-pane-tile-T1"), { key: "ArrowDown" });
  fireEvent.click(screen.getByTestId("move-pane-confirm"));

  expect(onConfirm).toHaveBeenCalledExactlyOnceWith({ anchor: "pane:h-T1", side: "below" });
});

it("cannot confirm into a full workspace", async () => {
  api.layout.mockResolvedValue(workspace(["T1", "T2"], undefined, 2));
  render(<MovePaneDialog request={request} busy={false} onCancel={() => {}} onConfirm={() => {}} />);

  await screen.findByText("Work of T1");
  expect((screen.getByTestId("move-pane-confirm") as HTMLButtonElement).disabled).toBe(true);
  expect(status()).toContain("is full");
});

it("says an empty workspace is simply filled", async () => {
  api.layout.mockResolvedValue(workspace([], null));
  render(<MovePaneDialog request={request} busy={false} onCancel={() => {}} onConfirm={() => {}} />);

  expect(await screen.findByText("Fills the empty workspace")).toBeTruthy();
  expect(status()).toBe("Blog is empty, so T9 fills it.");
  expect(screen.queryByTestId("move-pane-auto")).toBeNull();
});
