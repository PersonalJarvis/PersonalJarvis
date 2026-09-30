import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import { AgentBrowserPreview } from "./AgentBrowserPreview";
import type { SocietyAgent } from "../data";
vi.mock("@/i18n", () => ({
  useLocaleChunk: () => true,
  useT: () => (key: string) => ({
    "society.browser_live.take_control": "Take control",
    "society.browser_live.address": "Website address",
    "society.browser_live.back": "Back",
    "society.browser_live.screen": "Live browser of {0}",
    "society.browser_live.live": "Live",
    "society.browser_live.off_hint": "Starts when {0} needs it",
    "society.browser_live.open": "Open browser",
  } as Record<string, string>)[key] ?? key,
}));
const { control, state, view, browser } = vi.hoisted(() => ({
  control: vi.fn(),
  view: vi.fn(),
  browser: { open: true },
  state: { connected: true, ready: true, fullWindow: false, manual: false, running: false,
    url: "https://example.com", target: "one", tabs: [{ id: "one", url: "https://example.com" }], error: "" },
}));
vi.mock("./useBrowserView", () => ({
  useBrowserView: (agentId: string, enabled: boolean) => {
    view(agentId, enabled);
    return { canvas: { current: null }, state, control, approve: vi.fn() };
  },
}));
vi.mock("../cardData", () => ({
  useBrowserInstallStatus: () => ({ data: { installed: true, running: false } }),
  useAgentBrowserOpen: () => ({ data: browser.open }),
}));
const agent = { agentId: "scout", name: "Scout" } as SocietyAgent;
function mount() {
  return render(<QueryClientProvider client={new QueryClient()}>
    <AgentBrowserPreview agent={agent} />
  </QueryClientProvider>);
}
afterEach(() => {
  cleanup(); control.mockClear(); view.mockClear();
  state.manual = false; state.fullWindow = false; browser.open = true;
});
describe("live agent browser", () => {
  test("opening the card never launches a browser the agent is not using", () => {
    browser.open = false;
    mount();
    expect(view).toHaveBeenLastCalledWith("scout", false);
    expect(screen.getByTestId("agent-browser-preview").textContent).toContain("Starts when Scout needs it");
    fireEvent.click(screen.getByTestId("agent-browser-open"));
    expect(view).toHaveBeenLastCalledWith("scout", true);
  });
  test("a browser the agent already runs is shown straight away", () => {
    mount();
    expect(view).toHaveBeenLastCalledWith("scout", true);
    expect(screen.queryByTestId("agent-browser-open")).toBeNull();
  });
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
