import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ProjectConnectDialog } from "./ProjectConnectDialog";

const api = vi.hoisted(() => ({
  fetchFolders: vi.fn(), fetchRecents: vi.fn(), fetchNativePickerSupport: vi.fn(),
  searchFolders: vi.fn(), createFolder: vi.fn(), forgetRecent: vi.fn(),
  openNativePicker: vi.fn(), resolveDroppedFolder: vi.fn(),
}));
vi.mock("@/lib/agenticIdeApi", () => api);

const folder = { name: "notes", path: "/code/notes", is_project: false, is_repo: false };
const listing = (path: string | null = null) => ({ path, parent: path ? "/code" : null, entries: path ? [] : [folder], error: null });

beforeEach(() => {
  vi.resetAllMocks();
  api.fetchFolders.mockImplementation(async (path: string | null = null) => listing(path));
  api.fetchRecents.mockResolvedValue({ recents: [] });
  api.fetchNativePickerSupport.mockResolvedValue({ available: false });
  api.searchFolders.mockResolvedValue({ entries: [], truncated: false });
});
afterEach(cleanup);

async function selectFolder() {
  fireEvent.click(screen.getByRole("button", { name: "Choose folder" }));
  fireEvent.click(await screen.findByText("notes", { exact: true }));
  fireEvent.click(screen.getByRole("button", { name: "Use this folder" }));
  await screen.findByRole("button", { name: "Change" });
}

describe("ProjectConnectDialog", () => {
  it("starts compact, focuses the name, and requires a confirmed folder", async () => {
    const onConnect = vi.fn();
    render(<ProjectConnectDialog onClose={vi.fn()} onConnect={onConnect} />);
    expect(screen.getByRole("button", { name: "Connect project" }).hasAttribute("disabled")).toBe(true);
    expect(document.activeElement).toBe(screen.getByRole("textbox", { name: "Project name" }));
    expect(api.fetchFolders).not.toHaveBeenCalled();
    await selectFolder();
    expect(onConnect).not.toHaveBeenCalled();
    expect(screen.getByRole("textbox", { name: "Project name" }).getAttribute("placeholder")).toBe("notes");
    fireEvent.click(screen.getByRole("button", { name: "Connect project" }));
    await waitFor(() => expect(onConnect).toHaveBeenCalledWith("/code/notes", undefined));
  });

  it("keeps the confirmed folder and custom name when browsing is cancelled", async () => {
    const onConnect = vi.fn().mockResolvedValue(undefined);
    render(<ProjectConnectDialog onClose={vi.fn()} onConnect={onConnect} />);
    fireEvent.change(screen.getByRole("textbox", { name: "Project name" }), { target: { value: "  Research  " } });
    await selectFolder();
    fireEvent.click(screen.getByRole("button", { name: "Change" }));
    api.fetchFolders.mockResolvedValueOnce({ ...listing(), entries: [{ ...folder, name: "other", path: "/code/other" }] });
    // Changing the candidate never changes the project until it is confirmed.
    fireEvent.change(await screen.findByPlaceholderText(/Search/), { target: { value: "/code/other" } });
    await waitFor(() => expect(api.fetchFolders).toHaveBeenCalledWith("/code/other", false));
    fireEvent.click(screen.getByRole("button", { name: "Back to project" }));
    fireEvent.click(screen.getByRole("button", { name: "Connect project" }));
    await waitFor(() => expect(onConnect).toHaveBeenCalledWith("/code/notes", "Research"));
  });

  it("refuses a stale folder and allows another selection", async () => {
    render(<ProjectConnectDialog onClose={vi.fn()} onConnect={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Choose folder" }));
    fireEvent.click(await screen.findByText("notes", { exact: true }));
    await waitFor(() => expect(api.fetchFolders).toHaveBeenCalledWith("/code/notes", false));
    api.fetchFolders.mockResolvedValueOnce({ ...listing(), error: "Folder no longer exists." });
    fireEvent.click(screen.getByRole("button", { name: "Use this folder" }));
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", "Folder no longer exists.");
    expect(screen.getByRole("dialog", { name: "Choose a folder" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Use this folder" }));
    await screen.findByRole("button", { name: "Change" });
  });

  it("prevents duplicate submissions and dismissal while saving, then shows retryable errors", async () => {
    let reject!: (reason: Error) => void;
    const onClose = vi.fn();
    const onConnect = vi.fn(() => new Promise<void>((_, fail) => { reject = fail; }));
    render(<ProjectConnectDialog onClose={onClose} onConnect={onConnect} />);
    await selectFolder();
    const form = screen.getByRole("textbox", { name: "Project name" }).closest("form")!;
    fireEvent.submit(form);
    fireEvent.submit(form);
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(onConnect).toHaveBeenCalledTimes(1);
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Close" }).hasAttribute("disabled")).toBe(true);
    await act(async () => reject(new Error("Connection failed. Try again.")));
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", "Connection failed. Try again.");
    expect(screen.getByRole("button", { name: "Connect project" }).hasAttribute("disabled")).toBe(false);
    onConnect.mockResolvedValueOnce(undefined);
    fireEvent.submit(form);
    await waitFor(() => expect(onConnect).toHaveBeenCalledTimes(2));
  });

  it("uses Escape to return from browsing before dismissing the dialog", async () => {
    const onClose = vi.fn();
    render(<ProjectConnectDialog onClose={onClose} onConnect={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Choose folder" }));
    await screen.findByText("notes", { exact: true });
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(screen.getByRole("dialog", { name: "Connect project" })).toBeTruthy();
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("creates and confirms a new folder without connecting it prematurely", async () => {
    const onConnect = vi.fn().mockResolvedValue(undefined);
    api.createFolder.mockResolvedValue({ folder: { ...folder, name: "research", path: "/code/research" }, error: null });
    render(<ProjectConnectDialog onClose={vi.fn()} onConnect={onConnect} />);
    fireEvent.click(screen.getByRole("button", { name: "Choose folder" }));
    await screen.findByText("notes", { exact: true });
    fireEvent.click(screen.getByRole("button", { name: "New folder" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Name of the new folder" }), { target: { value: "research" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(api.createFolder).toHaveBeenCalledWith({ parent: null, name: "research" }));
    await waitFor(() => expect(api.fetchFolders).toHaveBeenCalledWith("/code/research", false));
    expect(onConnect).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Use this folder" }));
    await screen.findByRole("button", { name: "Change" });
    fireEvent.click(screen.getByRole("button", { name: "Connect project" }));
    await waitFor(() => expect(onConnect).toHaveBeenCalledWith("/code/research", undefined));
  });

  it("keeps the folder selection when the native picker is cancelled", async () => {
    api.fetchNativePickerSupport.mockResolvedValue({ available: true });
    api.openNativePicker.mockResolvedValue({ cancelled: true });
    const onConnect = vi.fn().mockResolvedValue(undefined);
    render(<ProjectConnectDialog onClose={vi.fn()} onConnect={onConnect} />);
    await selectFolder();
    fireEvent.click(screen.getByRole("button", { name: "Change" }));
    fireEvent.click(await screen.findByTestId("native-browse"));
    await waitFor(() => expect(api.openNativePicker).toHaveBeenCalledWith("/code/notes"));
    await waitFor(() => expect(screen.getByTestId("native-browse").hasAttribute("disabled")).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: "Use this folder" }));
    await screen.findByRole("button", { name: "Change" });
    fireEvent.click(screen.getByRole("button", { name: "Connect project" }));
    await waitFor(() => expect(onConnect).toHaveBeenCalledWith("/code/notes", undefined));
  });
});
