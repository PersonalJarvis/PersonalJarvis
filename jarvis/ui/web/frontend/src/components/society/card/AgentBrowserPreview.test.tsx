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
    "society.browser_profiles.chrome_offline": "Chrome disconnected",
    "society.browser_profiles.profile_unavailable": "Profile disconnected — choose a profile",
    "society.browser_profiles.title": "Browser profiles",
    "society.browser_profiles.sign_in_chrome": "Sign in directly in Chrome",
    "society.browser_profiles.preview_paused": "Sign in directly in Chrome. The preview is paused.",
    "society.browser_live.return_control": "Return control",
    "society.browser_live.repair": "Repair browser",
    "society.browser_profiles.google_signin_rejected": "Google declined this sign-in",
    "society.browser_profiles.google_signin_recovery": "Sign in yourself in regular Chrome, then connect its profile.",
    "society.browser_profiles.connect_supported_chrome": "Connect regular Chrome",
    "society.browser_profiles.google_signin_help": "Google sign-in help",
  } as Record<string, string>)[key] ?? key,
}));
const { control, state, view, browser } = vi.hoisted(() => ({
  control: vi.fn(),
  view: vi.fn(),
  browser: { open: true, mode: "own", connected: true, profileName: "" },
  state: { connected: true, ready: true, fullWindow: false, extendedInput: false, previewPaused: false, manual: false, running: false,
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
  useAgentBrowserOpen: () => ({ data: browser }),
}));
vi.mock("../browser/BrowserProfilesDialog", () => ({
  default: ({ agentId, connectChrome }: { agentId?: string; connectChrome?: boolean }) =>
    <div data-testid="chrome-recovery-dialog">{agentId}:{String(connectChrome)}</div>,
}));
const agent = { agentId: "scout", name: "Scout" } as SocietyAgent;
function mount() {
  return render(<QueryClientProvider client={new QueryClient()}>
    <AgentBrowserPreview agent={agent} />
  </QueryClientProvider>);
}
afterEach(() => {
  cleanup(); control.mockClear(); view.mockClear();
  state.manual = false; state.fullWindow = false; state.extendedInput = false; state.previewPaused = false; state.ready = true; state.error = ""; browser.open = true;
  browser.mode = "own"; browser.connected = true; browser.profileName = "";
  state.url = "https://example.com";
});
describe("live agent browser", () => {
  test("a rejected Google login opens explicit Chrome recovery without retrying or changing the browser", async () => {
    state.url = "https://accounts.google.com/v3/signin/rejected?flowName=fixture";
    state.manual = true;
    mount();
    expect(screen.getByRole("alert").textContent).toContain("Google declined this sign-in");
    expect(screen.getByRole("link", { name: "Google sign-in help" }).getAttribute("href")).toBe("https://support.google.com/accounts/answer/7675428");
    fireEvent.click(screen.getByRole("button", { name: "Connect regular Chrome" }));
    expect((await screen.findByTestId("chrome-recovery-dialog")).textContent).toBe("scout:true");
    expect(control).not.toHaveBeenCalled();
  });
  test("an unavailable profile blocks live access and offers profile selection without open or repair", () => {
    browser.mode = "unavailable";
    state.error = "Previous browser error";
    mount();
    expect(view).toHaveBeenLastCalledWith("scout", false);
    expect(screen.getByTestId("agent-browser-preview").textContent).toContain("Profile disconnected — choose a profile");
    expect(screen.queryByRole("button", { name: "Open browser" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Repair browser" })).toBeNull();
    expect((screen.getByRole("button", { name: "Browser profiles" }) as HTMLButtonElement).disabled).toBe(false);
    expect((screen.getByRole("button", { name: "Take control" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByLabelText("Live browser of Scout"));
    fireEvent.keyDown(screen.getByLabelText("Live browser of Scout"), { key: "x" });
    expect(control).not.toHaveBeenCalled();
  });
  test("Chrome metadata shows the assigned profile and disables opening while disconnected", () => {
    browser.mode = "chrome"; browser.connected = false; browser.open = false; browser.profileName = "Work Chrome";
    mount();
    expect(screen.getByTestId("agent-browser-preview").textContent).toContain("Work Chrome · Chrome disconnected");
    expect((screen.getByRole("button", { name: "Open browser" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByRole("button", { name: "Repair browser" })).toBeNull();
  });
  test("Chrome manual login pauses mirrored input while return control stays available", () => {
    browser.mode = "chrome"; state.previewPaused = true; state.ready = false; state.manual = true;
    mount();
    expect(screen.getByText("Sign in directly in Chrome. The preview is paused.")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Return control" }) as HTMLButtonElement).disabled).toBe(false);
    fireEvent.keyDown(screen.getByLabelText("Live browser of Scout"), { key: "x" });
    expect(control).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Return control" }));
    expect(control).toHaveBeenCalledWith("takeover", { enabled: false });
  });
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
  test("native menus receive right click, double click and scroll at the shown frame position", () => {
    state.manual = true; state.fullWindow = true; state.extendedInput = true;
    mount();
    const canvas = screen.getByLabelText("Live browser of Scout") as HTMLCanvasElement;
    canvas.width = 1600; canvas.height = 1000;
    canvas.dataset.browserGeometryId = "profile-popup";
    vi.spyOn(canvas, "getBoundingClientRect").mockReturnValue({ left: 0, top: 0, width: 1600, height: 1000 } as DOMRect);
    fireEvent.contextMenu(canvas, { clientX: 1500, clientY: 70 });
    expect(control).toHaveBeenLastCalledWith("click", { x: 1500, y: 70, geometry_id: "profile-popup", button: "right" });
    fireEvent.click(canvas, { clientX: 1500, clientY: 70, detail: 2 });
    expect(control).toHaveBeenLastCalledWith("click", { x: 1500, y: 70, geometry_id: "profile-popup", count: 2 });
    fireEvent.wheel(canvas, { clientX: 1500, clientY: 180, deltaY: 80 });
    expect(control).toHaveBeenLastCalledWith("scroll", { x: 1500, y: 180, geometry_id: "profile-popup", dx: 0, dy: 80 });
  });
  test("an older running worker never receives hover disguised as a click or unsupported mouse buttons", () => {
    state.manual = true; state.fullWindow = true;
    mount();
    const canvas = screen.getByLabelText("Live browser of Scout") as HTMLCanvasElement;
    vi.spyOn(canvas, "getBoundingClientRect").mockReturnValue({ left: 0, top: 0, width: 1280, height: 800 } as DOMRect);
    fireEvent.mouseMove(canvas, { clientX: 100, clientY: 70 });
    fireEvent.contextMenu(canvas, { clientX: 100, clientY: 70 });
    fireEvent.click(canvas, { clientX: 100, clientY: 70, detail: 2 });
    expect(control.mock.calls).toEqual([["click", { x: 100, y: 70 }]]);
  });
});
