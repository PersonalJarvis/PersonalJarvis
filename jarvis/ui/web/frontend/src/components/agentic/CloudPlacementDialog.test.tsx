import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { Computer, Readiness } from "@/lib/computersApi";
import type { TerminalState } from "@/lib/agenticIdeApi";
const api = vi.hoisted(() => ({ list: vi.fn(), check: vi.fn(), readiness: vi.fn(), navigate: vi.fn() }));
vi.mock("@/lib/computersApi", () => ({ computersApi: { list: api.list, check: api.check }, readinessApi: { get: api.readiness } }));
vi.mock("@/store/events", () => ({ useEventStore: (select: (state: unknown) => unknown) => select({ setActiveSection: api.navigate }) }));
import { CloudPlacementDialog } from "./CloudPlacementDialog";

const server = { id: "vps", name: "Build server", kind: "server", facts: { os_id: "ubuntu", os_name: "Ubuntu" }, health: { status: "online" }, busy: false } as Computer;
const ready = { tools: ["git", "tmux", "claude", "codex"].map((id) => ({ id, installed: true })), logins: { claude: true, codex: true }, os: "ubuntu", ready: true } as Readiness;
const terminal = { name: "Dana", agent: "claude", display_name: "Claude Code" } as TerminalState;
const props = { terminal, busy: false, error: "", onCancel: vi.fn(), onConfirm: vi.fn() };
beforeEach(() => { vi.resetAllMocks(); api.list.mockResolvedValue([server]); api.check.mockResolvedValue(server); api.readiness.mockResolvedValue(ready); });
afterEach(cleanup);

it("checks the selected destination and moves only after explicit review confirmation", async () => {
  render(<CloudPlacementDialog {...props} />);
  fireEvent.click(await screen.findByRole("radio", { name: /Build server/ }));
  await waitFor(() => expect((screen.getByRole("button", { name: "Move to Build server" }) as HTMLButtonElement).disabled).toBe(false));
  expect(api.check).toHaveBeenCalledExactlyOnceWith("vps");
  expect(api.readiness).toHaveBeenCalledExactlyOnceWith("vps");
  expect(props.onConfirm).not.toHaveBeenCalled();
  expect(screen.getByText(/interrupted turn can continue through native session resume/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Move to Build server" }));
  fireEvent.click(screen.getByRole("button", { name: "Move to Build server" }));
  expect(props.onConfirm).toHaveBeenCalledExactlyOnceWith("vps", "Build server");
});

it("distinguishes failed listing from an empty list and offers retry", async () => {
  api.list.mockRejectedValueOnce(new Error("Connection interrupted")).mockResolvedValueOnce([]);
  render(<CloudPlacementDialog {...props} />);
  expect(await screen.findByRole("alert")).toHaveProperty("textContent", expect.stringContaining("Connection interrupted"));
  expect(screen.queryByText(/No servers connected/)).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  expect(await screen.findByText(/No servers connected yet/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Connect a server" }));
  expect(props.onCancel).toHaveBeenCalledOnce();
  expect(api.navigate).toHaveBeenCalledWith("computers");
});

it("does not treat unknown or failed readiness as ready", async () => {
  api.list.mockResolvedValue([{ ...server, health: { status: "unknown" } }]);
  api.check.mockResolvedValue({ ...server, health: { status: "offline", message: "Server is offline" } });
  render(<CloudPlacementDialog {...props} />);
  fireEvent.click(await screen.findByRole("radio", { name: /Not checked yet/ }));
  expect(await screen.findByRole("alert")).toHaveProperty("textContent", "Server is offline");
  expect(api.readiness).not.toHaveBeenCalled();
  expect((screen.getByRole("button", { name: "Move to Build server" }) as HTMLButtonElement).disabled).toBe(true);
  api.check.mockResolvedValue(server);
  api.readiness.mockRejectedValue(new Error("Readiness unavailable"));
  fireEvent.click(screen.getByRole("button", { name: "Check again" }));
  expect(await screen.findByText("Readiness unavailable")).toBeTruthy();
  expect((screen.getByRole("button", { name: "Move to Build server" }) as HTMLButtonElement).disabled).toBe(true);
});

it("requires the selected CLI and login rather than a generic ready flag", async () => {
  api.readiness.mockResolvedValue({ ...ready, tools: ready.tools.filter((tool) => tool.id !== "codex"), logins: { claude: true, codex: false } });
  render(<CloudPlacementDialog {...props} terminal={{ ...terminal, agent: "codex", display_name: "Codex" }} initialTarget="vps" />);
  expect(await screen.findByText(/Install or update on Build server: codex/)).toBeTruthy();
  expect(screen.getByText(/Sign in to Codex/)).toBeTruthy();
  expect(screen.getByText(/Send the next prompt on the destination/)).toBeTruthy();
  expect((screen.getByRole("button", { name: "Move to Build server" }) as HTMLButtonElement).disabled).toBe(true);
});

it.each(["windows", "local_vm"])("does not promise PC-off operation for %s targets", async (kind) => {
  const computer = kind === "windows" ? { ...server, facts: { ...server.facts!, os_id: "windows", os_name: "Windows" } } : { ...server, kind: "local_vm" as const };
  api.list.mockResolvedValue([computer]);
  api.check.mockResolvedValue(computer);
  api.readiness.mockResolvedValue({ ...ready, os: kind === "windows" ? "windows" : "ubuntu" });
  render(<CloudPlacementDialog {...props} initialTarget="vps" />);
  await waitFor(() => expect((screen.getByRole("button", { name: "Move to Build server" }) as HTMLButtonElement).disabled).toBe(false));
  expect(screen.getByText(kind === "windows" ? /Windows sessions need this PC/ : /This VM runs on this PC/)).toBeTruthy();
  expect(screen.queryByText(/coding process can stay running after this PC disconnects/)).toBeNull();
});

it("brings a remote pane home after review without checking a paid provider", async () => {
  api.list.mockResolvedValue([server, { ...server, id: "other", name: "Another server" }]);
  render(<CloudPlacementDialog {...props} terminal={{ ...terminal, computer_id: "vps" }} initialTarget={null} />);
  const confirm = await screen.findByRole("button", { name: "Bring session back" });
  await waitFor(() => expect((confirm as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(confirm);
  expect(props.onConfirm).toHaveBeenCalledExactlyOnceWith(null, "this computer");
  expect(api.check).not.toHaveBeenCalled();
  expect(api.readiness).not.toHaveBeenCalled();
  expect((screen.getByRole("radio", { name: /Another server/ }) as HTMLInputElement).disabled).toBe(true);
  expect(screen.getByText(/Bring this session back before moving it to a different server/)).toBeTruthy();
});

it("keeps the dialog and destination locked throughout a pending transfer", async () => {
  const { rerender } = render(<CloudPlacementDialog {...props} initialTarget="vps" />);
  await waitFor(() => expect((screen.getByRole("button", { name: "Move to Build server" }) as HTMLButtonElement).disabled).toBe(false));
  rerender(<CloudPlacementDialog {...props} initialTarget="vps" busy />);
  fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
  expect(props.onCancel).not.toHaveBeenCalled();
  expect((screen.getByRole("button", { name: "Cancel" }) as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByRole("button", { name: "Move to Build server" }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByText(/Keep this PC connected…/)).toBeTruthy();
});
