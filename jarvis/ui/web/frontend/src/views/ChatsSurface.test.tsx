import { describe, it, expect, afterEach, beforeEach, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ChatsSurface } from "@/views/ChatsSurface";
import { useHomeStore } from "@/store/home";
import { readHomeSurface } from "@/lib/homeSurface";

/**
 * The front page is ONE chat with a voice mode inside it (2026-10-01). What
 * is under test is the shell: it opens on the chat, the top bar's voice-mode
 * button swaps in the spoken stage and brings the typing back, and the mode
 * is remembered. Both stages and the assistant card are stubbed.
 */
vi.mock("@/components/home/VoiceStage", () => ({
  VoiceStage: () => <div data-testid="voice">voice</div>,
}));
vi.mock("@/components/home/ChatStage", () => ({
  ChatStage: () => <div data-testid="chat">chat</div>,
}));
vi.mock("@/components/home/AssistantProfilePanel", () => ({
  AssistantProfilePanel: () => <div data-testid="assistant-panel-stub" />,
}));

const STORAGE_KEY = "jarvis.home.surface.v2";

describe("ChatsSurface (the front page)", () => {
  beforeEach(() => {
    window.localStorage.clear();
    useHomeStore.setState({ surface: readHomeSurface() });
  });

  afterEach(cleanup);

  it("opens on the chat by default", async () => {
    render(<ChatsSurface />);
    expect(await screen.findByTestId("chat")).toBeTruthy();
    expect(screen.queryByTestId("voice")).toBeNull();
    expect(screen.getByTestId("home-view").getAttribute("data-surface")).toBe("chat");
  });

  it("ignores the retired Voice | Chat switch's stored choice", async () => {
    window.localStorage.setItem("jarvis.home.surface.v1", "voice");
    useHomeStore.setState({ surface: readHomeSurface() });
    render(<ChatsSurface />);
    expect(await screen.findByTestId("chat")).toBeTruthy();
  });

  it("enters voice mode from the top bar and remembers it", async () => {
    render(<ChatsSurface />);
    await screen.findByTestId("chat");
    fireEvent.click(screen.getByTestId("chat-voice-mode"));
    expect(screen.getByTestId("voice")).toBeTruthy();
    expect(screen.queryByTestId("chat")).toBeNull();
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe("voice");
    expect(screen.getByTestId("chat-voice-mode").getAttribute("aria-pressed")).toBe("true");
  });

  it("goes back to typing from voice mode", async () => {
    window.localStorage.setItem(STORAGE_KEY, "voice");
    useHomeStore.setState({ surface: readHomeSurface() });
    render(<ChatsSurface />);
    expect(screen.getByTestId("voice")).toBeTruthy();
    fireEvent.click(screen.getByTestId("chat-voice-mode"));
    expect(await screen.findByTestId("chat")).toBeTruthy();
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe("chat");
  });

  it("lands on the chat when the stored value is corrupt", async () => {
    window.localStorage.setItem(STORAGE_KEY, "garbage");
    useHomeStore.setState({ surface: readHomeSurface() });
    render(<ChatsSurface />);
    expect(await screen.findByTestId("chat")).toBeTruthy();
  });

  it("hides and shows the assistant card from the top bar", async () => {
    render(<ChatsSurface />);
    await screen.findByTestId("chat");
    expect(screen.getByTestId("assistant-panel-stub")).toBeTruthy();
    fireEvent.click(screen.getByTestId("chat-panel-toggle"));
    expect(screen.queryByTestId("assistant-panel-stub")).toBeNull();
    fireEvent.click(screen.getByTestId("chat-top-assistant"));
    expect(screen.getByTestId("assistant-panel-stub")).toBeTruthy();
  });
});
