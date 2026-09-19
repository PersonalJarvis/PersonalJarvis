import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import { AgentBrowserPreview } from "./AgentBrowserPreview";
import type { SocietyAgent } from "../data";
const { control, state } = vi.hoisted(() => ({
  control: vi.fn(),
  state: { connected: true, ready: true, fullWindow: false, manual: false, running: false,
    url: "https://example.com", target: "one", tabs: [{ id: "one", url: "https://example.com" }], error: "" },
}));
vi.mock("./useBrowserView", () => ({
  useBrowserView: () => ({ canvas: { current: null }, state, control, approve: vi.fn() }),
}));
vi.mock("../cardData", () => ({
  useBrowserInstallStatus: () => ({ data: { installed: true, running: false } }),
}));
const agent = { agentId: "scout", name: "Scout" } as SocietyAgent;
function mount() {
  return render(<QueryClientProvider client={new QueryClient()}>
    <AgentBrowserPreview agent={agent} />
  </QueryClientProvider>);
}
afterEach(() => { cleanup(); control.mockClear(); state.manual = false; state.fullWindow = false; });
describe("live agent browser", () => {
  test("full Chrome window never adds a second address bar or tab picker", async () => {
    state.fullWindow = true;
    mount();
    fireEvent.click(await screen.findByRole("button", { name: /Take control/ }));
    expect(screen.queryByLabelText("Website address")).toBeNull();
    expect(screen.queryByLabelText("Back")).toBeNull();
    expect(screen.getByLabelText("Live browser of Scout")).toBeTruthy();
  });
  test("renders the real browser canvas in the options rail", async () => {
    mount();
    expect((await screen.findByLabelText("Live browser of Scout")).tagName).toBe("CANVAS");
    expect(screen.getByTestId("agent-browser-preview").textContent).toContain("Live");
    expect(screen.queryByTestId("agent-browser-setup")).toBeNull();
  });
  test("takeover requests a control lease and opens navigation", () => {
    mount();
    fireEvent.click(screen.getByRole("button", { name: /Take control/ }));
    expect(control).toHaveBeenCalledWith("takeover", { enabled: true });
    expect(screen.getByLabelText("Website address")).toBeTruthy();
  });
  test("view-only canvas never sends user input", () => {
    mount();
    fireEvent.keyDown(screen.getByLabelText("Live browser of Scout"), { key: "x" });
    expect(control).not.toHaveBeenCalled();
  });
  test("clicking the preview starts interaction without a separate takeover button", () => {
    mount();
    const canvas = screen.getByLabelText("Live browser of Scout") as HTMLCanvasElement;
    vi.spyOn(canvas, "getBoundingClientRect").mockReturnValue({
      left: 0, top: 0, width: 1280, height: 800,
    } as DOMRect);
    fireEvent.click(canvas, { clientX: 200, clientY: 60 });
    expect(document.activeElement).toBe(canvas);
    expect(control).toHaveBeenCalledWith("click", { x: 200, y: 60 });
    fireEvent.keyDown(canvas, { key: "x" });
    expect(control).toHaveBeenCalledWith("text", { text: "x" });
  });
  test("manual typing uses browser control, never chat", () => {
    state.manual = true;
    mount();
    fireEvent.keyDown(screen.getByLabelText("Live browser of Scout"), { key: "x" });
    expect(control).toHaveBeenCalledWith("text", { text: "x" });
  });
});
