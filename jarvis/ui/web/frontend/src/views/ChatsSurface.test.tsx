import { describe, it, expect, afterEach, beforeEach, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ChatsSurface } from "@/views/ChatsSurface";
import { SurfaceSwitch } from "@/components/home/SurfaceSwitch";
import { useHomeStore } from "@/store/home";
import { readHomeSurface } from "@/lib/homeSurface";
import { useEventStore } from "@/store/events";

/**
 * The front page is one section with one switch: Voice (the Jarvis bar) or
 * Chat (the typed column). What is under test is the shell's switching and
 * persistence, not what either stage renders — both stages and the header
 * are stubbed.
 */
vi.mock("@/components/home/HomeHeader", () => ({
  HomeHeader: () => <div data-testid="home-header-stub" />,
}));
vi.mock("@/components/home/VoiceStage", () => ({
  VoiceStage: () => <div data-testid="voice">voice</div>,
}));
vi.mock("@/components/home/ChatStage", () => ({
  ChatStage: () => <div data-testid="chat">chat</div>,
}));

const STORAGE_KEY = "jarvis.home.surface.v2";

describe("ChatsSurface (the front page)", () => {
  beforeEach(() => {
    window.localStorage.clear();
    useHomeStore.setState({ surface: readHomeSurface() });
    useEventStore.setState({ activeSection: "chats", voiceState: "idle" });
  });

  afterEach(cleanup);

  it("opens on the chat stage by default", async () => {
    render(<ChatsSurface />);
    expect(await screen.findByTestId("chat")).toBeTruthy();
    expect(screen.queryByTestId("voice")).toBeNull();
    expect(screen.getByTestId("home-view").getAttribute("data-surface")).toBe("chat");
  });

  it("shows voice mode when a wake-word call starts without a composer click", async () => {
    render(<><SurfaceSwitch /><ChatsSurface /></>);
    expect(await screen.findByTestId("chat")).toBeTruthy();
    act(() => useHomeStore.getState().ingest("VoiceSessionStarted", {
      session_id: "wake-call", wake_keyword: "jarvis",
    }, 1));
    expect(screen.getByTestId("voice")).toBeTruthy();
    expect(screen.queryByTestId("chat")).toBeNull();
    expect(screen.getByTestId("home-view").getAttribute("data-surface")).toBe("voice");
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe("voice");

    // Returning to typing stays explicit; normal voice updates cannot undo it.
    fireEvent.click(screen.getByTestId("home-surface-chat"));
    act(() => useHomeStore.getState().ingest("SystemStateChanged", {
      previous: "LISTENING", new_state: "THINKING",
    }, 2));
    expect(await screen.findByTestId("chat")).toBeTruthy();
  });

  it("switches to the chat stage from the sidebar switch and remembers it", async () => {
    useHomeStore.setState({ surface: "voice" });
    render(
      <>
        <SurfaceSwitch />
        <ChatsSurface />
      </>,
    );

    fireEvent.click(screen.getByTestId("home-surface-chat"));

    expect(await screen.findByTestId("chat")).toBeTruthy();
    expect(screen.queryByTestId("voice")).toBeNull();
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe("chat");
    expect(screen.getByTestId("home-surface-chat").getAttribute("aria-selected")).toBe("true");
  });

  it("opens on the chat stage when that is the stored choice", async () => {
    window.localStorage.setItem(STORAGE_KEY, "chat");
    useHomeStore.setState({ surface: readHomeSurface() });
    render(<ChatsSurface />);
    expect(await screen.findByTestId("chat")).toBeTruthy();
  });

  it("switches back to voice", () => {
    window.localStorage.setItem(STORAGE_KEY, "chat");
    useHomeStore.setState({ surface: readHomeSurface() });
    render(
      <>
        <SurfaceSwitch />
        <ChatsSurface />
      </>,
    );
    fireEvent.click(screen.getByTestId("home-surface-voice"));
    expect(screen.getByTestId("voice")).toBeTruthy();
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe("voice");
  });

  it("lands on chat when the stored value is corrupt", async () => {
    window.localStorage.setItem(STORAGE_KEY, "garbage");
    useHomeStore.setState({ surface: readHomeSurface() });
    render(<ChatsSurface />);
    expect(await screen.findByTestId("chat")).toBeTruthy();
  });

  it("migrates the old voice-first preference to chat once", () => {
    window.localStorage.setItem("jarvis.home.surface.v1", "voice");
    expect(readHomeSurface()).toBe("chat");
    window.localStorage.setItem(STORAGE_KEY, "voice");
    expect(readHomeSurface()).toBe("voice");
  });
});
