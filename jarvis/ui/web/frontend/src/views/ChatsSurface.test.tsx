import { describe, it, expect, afterEach, beforeEach, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ChatsSurface } from "@/views/ChatsSurface";

/**
 * The promise this change is built on: shipping the mission deck takes NOTHING
 * away. The classic chat view keeps the conversation list, thread resume,
 * delete and "speak in this conversation" — features the deck does not have —
 * so it has to stay one click away, and the choice has to stick.
 *
 * Both surfaces are stubbed: what is under test is the shell's switching and
 * persistence, not what either view renders.
 */
vi.mock("@/views/ChatsView", () => ({
  ChatsView: ({ headerAccessory }: { headerAccessory?: React.ReactNode }) => (
    <div data-testid="classic">
      classic
      {headerAccessory}
    </div>
  ),
}));

vi.mock("@/views/MissionDeckView", async () => {
  const actual = await vi.importActual<typeof import("@/views/MissionDeckView")>(
    "@/views/MissionDeckView",
  );
  return {
    // The real switch — it owns the persistence this test is about.
    SurfaceSwitch: actual.SurfaceSwitch,
    MissionDeckView: ({ headerAccessory }: { headerAccessory?: React.ReactNode }) => (
      <div data-testid="deck">
        deck
        {headerAccessory}
      </div>
    ),
  };
});

const STORAGE_KEY = "chats.surface.v1";

describe("ChatsSurface", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  afterEach(cleanup);

  it("shows the deck by default", () => {
    render(<ChatsSurface />);
    expect(screen.getByTestId("deck")).toBeTruthy();
    expect(screen.queryByTestId("classic")).toBeNull();
  });

  it("falls back to the classic chat view and remembers it", () => {
    render(<ChatsSurface />);

    fireEvent.click(screen.getByRole("button"));

    // The classic view is on screen — nothing about it was removed.
    expect(screen.getByTestId("classic")).toBeTruthy();
    expect(screen.queryByTestId("deck")).toBeNull();
    // ...and the choice survives the next start.
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe("classic");
  });

  it("opens on the classic view when that is the stored choice", () => {
    window.localStorage.setItem(STORAGE_KEY, "classic");
    render(<ChatsSurface />);
    expect(screen.getByTestId("classic")).toBeTruthy();
  });

  it("switches back to the deck", () => {
    window.localStorage.setItem(STORAGE_KEY, "classic");
    render(<ChatsSurface />);

    fireEvent.click(screen.getByRole("button"));

    expect(screen.getByTestId("deck")).toBeTruthy();
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe("deck");
  });

  it("lands on the deck when the stored value is corrupt", () => {
    // A broken preference must resolve to a usable surface, never a blank one.
    window.localStorage.setItem(STORAGE_KEY, "not-a-surface");
    render(<ChatsSurface />);
    expect(screen.getByTestId("deck")).toBeTruthy();
  });
});
