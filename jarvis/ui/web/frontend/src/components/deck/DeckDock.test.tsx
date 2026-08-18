import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { DECK_DOCK_GEOMETRY, DeckDock } from "@/components/deck/DeckDock";
import { NAV_GROUPS } from "@/components/layout/navGroups";
import { useEventStore } from "@/store/events";

// usePluginAttention polls /api/marketplace/plugins; the dock's attention pip
// is not under test here, so keep it quiet.
vi.mock("@/hooks/usePluginAttention", () => ({
  usePluginAttention: () => ({ count: 0, names: [] }),
}));

// The tick is WebAudio; here only WHEN it fires matters.
const soundMock = vi.hoisted(() => ({ playDockTick: vi.fn() }));
vi.mock("@/lib/sound", () => ({ playDockTick: soundMock.playDockTick }));

const ITEMS = NAV_GROUPS.flat();
const { BASE, GAP, PAD_TOP } = DECK_DOCK_GEOMETRY;

/** clientY that lands on the centre of icon `i` (jsdom rects sit at 0,0). */
function centreOf(i: number): number {
  return PAD_TOP + GAP + i * (BASE + GAP) + BASE / 2;
}

/** jsdom has no PointerEvent; a MouseEvent under the pointer name carries the
 *  clientY the dock reads. */
function moveTo(rail: HTMLElement, clientY: number) {
  act(() => {
    rail.dispatchEvent(new MouseEvent("pointermove", { bubbles: true, clientY }));
  });
}
function leave(rail: HTMLElement) {
  act(() => {
    // React derives onPointerLeave from pointerout + relatedTarget.
    rail.dispatchEvent(
      new MouseEvent("pointerout", { bubbles: true, relatedTarget: document.body }),
    );
  });
}

function renderDock() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <DeckDock />
    </QueryClientProvider>,
  );
}

describe("DeckDock", () => {
  beforeEach(() => {
    soundMock.playDockTick.mockClear();
    useEventStore.setState({ activeSection: "chats", conversations: [] });
  });
  afterEach(() => cleanup());

  test("shows every section of the sidebar's NAV_GROUPS as one named button", () => {
    renderDock();
    for (const item of ITEMS) {
      expect(screen.getByTestId(`deck-dock-${item.id}`)).toBeTruthy();
    }
    expect(screen.getAllByRole("button")).toHaveLength(ITEMS.length);
    expect(screen.getByTestId("deck-dock-chats").getAttribute("aria-current")).toBe("page");
    // No native title: the dock draws its own label, and a browser tooltip on
    // top of it would be a second, late label.
    expect(screen.getByTestId("deck-dock-chats").getAttribute("title")).toBeNull();
  });

  test("hovering names exactly one icon, and crossing to the next one ticks once", () => {
    renderDock();
    const rail = screen.getByTestId("deck-dock-rail");

    expect(screen.queryByTestId("deck-dock-label")).toBeNull();

    moveTo(rail, centreOf(2));
    let labels = screen.getAllByTestId("deck-dock-label");
    expect(labels).toHaveLength(1);
    expect(labels[0].textContent).toContain(
      screen.getByTestId(`deck-dock-${ITEMS[2].id}`).getAttribute("aria-label"),
    );
    expect(soundMock.playDockTick).toHaveBeenCalledTimes(1);
    expect(soundMock.playDockTick).toHaveBeenLastCalledWith("hover");

    // Moving WITHIN the same icon is not a notch.
    moveTo(rail, centreOf(2) + 6);
    expect(soundMock.playDockTick).toHaveBeenCalledTimes(1);

    moveTo(rail, centreOf(3));
    labels = screen.getAllByTestId("deck-dock-label");
    expect(labels).toHaveLength(1);
    expect(labels[0].textContent).toContain(
      screen.getByTestId(`deck-dock-${ITEMS[3].id}`).getAttribute("aria-label"),
    );
    expect(soundMock.playDockTick).toHaveBeenCalledTimes(2);
  });

  test("leaving the rail takes the label away without a tick", async () => {
    renderDock();
    const rail = screen.getByTestId("deck-dock-rail");
    moveTo(rail, centreOf(1));
    expect(screen.getAllByTestId("deck-dock-label")).toHaveLength(1);
    leave(rail);
    await waitFor(() => expect(screen.queryByTestId("deck-dock-label")).toBeNull(), {
      timeout: 2000,
    });
    expect(soundMock.playDockTick).toHaveBeenCalledTimes(1);
  });

  test("picking an icon jumps to its section with the firmer tick", () => {
    renderDock();
    act(() => {
      fireEvent.click(screen.getByTestId("deck-dock-tasks"));
    });
    expect(useEventStore.getState().activeSection).toBe("tasks");
    expect(soundMock.playDockTick).toHaveBeenLastCalledWith("select");
  });

  test("keyboard focus names the icon too, so the rail is not a row of blank glyphs", () => {
    renderDock();
    act(() => {
      fireEvent.focus(screen.getByTestId("deck-dock-docs"));
    });
    const labels = screen.getAllByTestId("deck-dock-label");
    expect(labels).toHaveLength(1);
    expect(labels[0].textContent).toContain(
      screen.getByTestId("deck-dock-docs").getAttribute("aria-label"),
    );
    // Focus is not a notch.
    expect(soundMock.playDockTick).not.toHaveBeenCalled();
  });
});
