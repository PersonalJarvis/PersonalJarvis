import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SectionWindowButton } from "./SectionWindowButton";
import { useEventStore, type SectionId } from "@/store/events";

const bridge = vi.hoisted(() => ({ native: true }));
vi.mock("@/components/voice/BrowserRealtimeControl", () => ({ hasEmbeddedDesktopBridge: () => bridge.native }));

beforeEach(() => {
  bridge.native = true;
  window.history.replaceState(null, "", "/");
  useEventStore.setState({ activeSection: "agents", solo: false, detachedViews: [], toasts: [] });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("section windows", () => {
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
    fireEvent.click(screen.getByRole("button", { name: "Bring it back" }));
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
    expect(fetcher.mock.calls[0][0]).toBe("/api/window/focus");
    expect(fetcher.mock.calls[1]).toEqual(["/api/window/reattach", expect.objectContaining({ body: '{"view":"settings"}' })]);
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
});
