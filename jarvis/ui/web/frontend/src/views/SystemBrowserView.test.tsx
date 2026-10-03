import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useEventStore } from "@/store/events";
import { SystemBrowserView } from "./SystemBrowserView";

const api = vi.hoisted(() => vi.fn());
const environment = vi.hoisted(() => ({ desktop: true }));
vi.mock("@/lib/nativeDrop", () => ({ inDesktopShell: () => environment.desktop }));
vi.mock("@/lib/systemBrowser", () => ({
  systemBrowser: api,
  browserBounds: () => ({ x: 200, y: 100, width: 900, height: 600, viewport_width: 1280, viewport_height: 800 }),
}));

beforeEach(() => {
  environment.desktop = true;
  api.mockReset();
  api.mockImplementation(async (action: string) => {
    if (action === "status") return { available: true, can_dock: true, browser: "Chrome" };
    if (action === "windows") return { available: true, can_dock: true, windows: [{ id: "selected", title: "Example - Chrome" }] };
    if (action === "attach") return { ok: true, lease: "lease-one" };
    return { ok: true };
  });
  useEventStore.setState({ activeSection: "browser" });
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("does not launch or select a browser when the section mounts", async () => {
  render(<SystemBrowserView />);
  await waitFor(() => expect(api).toHaveBeenCalledWith("status"));
  expect(api.mock.calls.map(([action]) => action)).toEqual(["status"]);
});

it("opens the ordinary browser only from its explicit button", async () => {
  render(<SystemBrowserView />);
  await waitFor(() => expect((screen.getByText("Open default browser") as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByText("Open default browser"));
  await waitFor(() => expect(api).toHaveBeenCalledWith("open", {}));
  expect(api.mock.calls.some(([action]) => action === "attach")).toBe(false);
});

async function selectWindow() {
  await waitFor(() => expect((screen.getByText("Show windows") as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByText("Show windows"));
  fireEvent.click(await screen.findByText("Example - Chrome"));
}

it("requires explicit selection and restores the window when navigating away", async () => {
  render(<SystemBrowserView />);
  await selectWindow();
  await screen.findByText("Release window");
  expect(api).toHaveBeenCalledWith("attach", expect.objectContaining({ window_id: "selected" }));
  act(() => useEventStore.setState({ activeSection: "chats" }));
  await waitFor(() => expect(api).toHaveBeenCalledWith("detach", { lease: "lease-one" }));
});

it("releases an attach result arriving after the view unmounts", async () => {
  let resolve!: (value: unknown) => void;
  const defaultApi = api.getMockImplementation()!;
  api.mockImplementation((action, body) => action === "attach"
    ? new Promise((done) => { resolve = done; }) : defaultApi(action, body));
  const view = render(<SystemBrowserView />);
  await selectWindow();
  view.unmount();
  await act(async () => { resolve({ ok: true, lease: "late-lease" }); });
  expect(api).toHaveBeenCalledWith("detach", { lease: "late-lease" });
});

it("does not control the desktop from a normal browser or remote web tab", () => {
  environment.desktop = false;
  render(<SystemBrowserView />);
  expect((screen.getByText("Open default browser") as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByText("Show windows") as HTMLButtonElement).disabled).toBe(true);
  expect(api).not.toHaveBeenCalled();
});
