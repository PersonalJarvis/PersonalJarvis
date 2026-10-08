import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SectionWindowButton } from "./SectionWindowButton";
import { useEventStore, type SectionId } from "@/store/events";
import { DETACHABLE_SECTIONS } from "@/lib/sectionWindows";

const bridge = vi.hoisted(() => ({ native: true }));
vi.mock("@/lib/embeddedDesktop", () => ({ hasEmbeddedDesktopBridge: () => bridge.native }));

beforeEach(() => {
  bridge.native = true;
  window.history.replaceState(null, "", "/");
  useEventStore.setState({ activeSection: "agents", solo: false, detachedViews: [], toasts: [] });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("section windows", () => {
  it.each([...DETACHABLE_SECTIONS, "profile", "apikeys", "mcps", "skills", "cli-test-hub", "agentic-ide-classic", "chat-workspace"] as SectionId[])(
    "shows a text button on the detachable %s section", (activeSection) => {
      useEventStore.setState({ activeSection });
      render(<SectionWindowButton />);
      expect(screen.getByRole("button", { name: "Detach window" }).textContent).toBe("Detach window");
    },
  );
  it("waits for the desktop bridge before offering the IDE handoff", () => {
    bridge.native = false;
    useEventStore.setState({ activeSection: "agentic-ide" });
    render(<SectionWindowButton />);
    expect(screen.queryByRole("button")).toBeNull();
    bridge.native = true;
    act(() => window.dispatchEvent(new Event("jarvis-token-ready")));
    expect(screen.getByRole("button")).toBeTruthy();
  });
  it.each(["chats", "dictation", "dictionary", "voice-shortcuts", "voice-language", "voice-api-keys"] as SectionId[])(
    "leaves %s in the main window", (activeSection) => {
      useEventStore.setState({ activeSection });
      render(<SectionWindowButton />);
      expect(screen.queryByRole("button")).toBeNull();
    },
  );

  it("opens the selected settings tab and lets the backend own its family", async () => {
    useEventStore.setState({ activeSection: "profile" });
    const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ok: true }) });
    vi.stubGlobal("fetch", fetcher);
    render(<SectionWindowButton />);
    fireEvent.click(screen.getByRole("button"));
    await waitFor(() => expect(screen.getByRole("button").hasAttribute("disabled")).toBe(false));
    expect(fetcher).toHaveBeenCalledWith("/api/window/detach", expect.objectContaining({ body: '{"view":"profile"}' }));
  });

  it("focuses the existing family owner after changing tabs", async () => {
    useEventStore.setState({ activeSection: "apikeys", detachedViews: ["settings"] });
    const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ok: true }) });
    vi.stubGlobal("fetch", fetcher);
    render(<SectionWindowButton />);
    fireEvent.click(screen.getByRole("button", { name: "Show window" }));
    await waitFor(() => expect(fetcher).toHaveBeenCalledWith("/api/window/detach", expect.objectContaining({ body: '{"view":"settings"}' })));
  });

  it("restores main before returning a solo window after a tab change", async () => {
    window.history.replaceState(null, "", "/?view=apikeys&solo=1&window=settings");
    useEventStore.setState({ activeSection: "apikeys", solo: true });
    const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ok: true }) });
    vi.stubGlobal("fetch", fetcher);
    render(<SectionWindowButton />);
    fireEvent.click(screen.getByRole("button", { name: "Back to main window" }));
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
    expect(fetcher.mock.calls[0][0]).toBe("/api/window/focus");
    expect(fetcher.mock.calls[1]).toEqual(["/api/window/reattach", expect.objectContaining({ body: '{"view":"settings"}' })]);
  });

  it("keeps return available after navigating to a section that cannot detach", async () => {
    window.history.replaceState(null, "", "/?view=dictation&solo=1&window=settings");
    useEventStore.setState({ activeSection: "dictation", solo: true });
    const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ok: true }) });
    vi.stubGlobal("fetch", fetcher);
    render(<SectionWindowButton />);
    fireEvent.click(screen.getByRole("button", { name: "Back to main window" }));
    await waitFor(() => expect(fetcher).toHaveBeenCalledWith("/api/window/reattach", expect.objectContaining({ body: '{"view":"settings"}' })));
  });

  it("keeps the detached window open if main cannot be restored", async () => {
    window.history.replaceState(null, "", "/?view=agents&solo=1");
    useEventStore.setState({ activeSection: "agents", solo: true });
    vi.spyOn(console, "warn").mockImplementation(() => {});
    const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ok: false }) });
    vi.stubGlobal("fetch", fetcher);
    render(<SectionWindowButton />);
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Back to main window" })));
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0][0]).toBe("/api/window/focus");
    expect(useEventStore.getState().toasts.at(-1)?.message).toBe("Could not bring the view back");
  });

  it("keeps the current view usable and reports a rejected operation", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ok: false, reason: "no_live_window" }) }));
    render(<SectionWindowButton />);
    await act(async () => fireEvent.click(screen.getByRole("button")));
    expect(useEventStore.getState().activeSection).toBe("agents");
    expect(useEventStore.getState().toasts.at(-1)?.message).toBe("Could not open a separate window");
    expect(screen.getByRole("button").hasAttribute("disabled")).toBe(false);
  });

  it("opens a named browser tab synchronously without calling the native shell", () => {
    bridge.native = false;
    useEventStore.setState({ activeSection: "profile" });
    const open = vi.spyOn(window, "open").mockReturnValue(window);
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    render(<SectionWindowButton />);
    fireEvent.click(screen.getByRole("button"));
    expect(open).toHaveBeenCalledWith("/?view=profile&solo=1&window=settings", "jarvis-section-settings");
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("closes a browser pop-out and focuses its original main window", () => {
    bridge.native = false;
    useEventStore.setState({ solo: true });
    const focus = vi.fn();
    vi.stubGlobal("opener", { closed: false, location: { origin: window.location.origin }, focus });
    const close = vi.spyOn(window, "close").mockImplementation(() => {});
    render(<SectionWindowButton />);
    fireEvent.click(screen.getByRole("button", { name: "Back to main window" }));
    expect(focus).toHaveBeenCalledOnce();
    expect(close).toHaveBeenCalledOnce();
    expect(focus.mock.invocationCallOrder[0]).toBeLessThan(close.mock.invocationCallOrder[0]);
  });
});
