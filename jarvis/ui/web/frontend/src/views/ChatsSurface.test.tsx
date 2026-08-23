import { describe, it, expect, afterEach, beforeEach, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ChatsSurface } from "@/views/ChatsSurface";
import { SurfaceSwitch } from "@/components/home/SurfaceSwitch";
import { useHomeStore } from "@/store/home";
import { readHomeSurface } from "@/lib/homeSurface";

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

const STORAGE_KEY = "jarvis.home.surface.v1";

describe("ChatsSurface (the front page)", () => {
  beforeEach(() => {
    window.localStorage.clear();
    useHomeStore.setState({ surface: readHomeSurface() });
  });

  afterEach(cleanup);

  it("opens on the voice stage by default", () => {
    render(<ChatsSurface />);
    expect(screen.getByTestId("voice")).toBeTruthy();
    expect(screen.queryByTestId("chat")).toBeNull();
    expect(screen.getByTestId("home-view").getAttribute("data-surface")).toBe("voice");
  });

  it("switches to the chat stage from the sidebar switch and remembers it", () => {
    render(
      <>
        <SurfaceSwitch />
        <ChatsSurface />
      </>,
    );

    fireEvent.click(screen.getByTestId("home-surface-chat"));

    expect(screen.getByTestId("chat")).toBeTruthy();
    expect(screen.queryByTestId("voice")).toBeNull();
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe("chat");
    expect(screen.getByTestId("home-surface-chat").getAttribute("aria-selected")).toBe("true");
  });

  it("opens on the chat stage when that is the stored choice", () => {
    window.localStorage.setItem(STORAGE_KEY, "chat");
    useHomeStore.setState({ surface: readHomeSurface() });
    render(<ChatsSurface />);
    expect(screen.getByTestId("chat")).toBeTruthy();
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

  it("lands on voice when the stored value is corrupt", () => {
    window.localStorage.setItem(STORAGE_KEY, "garbage");
    useHomeStore.setState({ surface: readHomeSurface() });
    render(<ChatsSurface />);
    expect(screen.getByTestId("voice")).toBeTruthy();
  });
});
