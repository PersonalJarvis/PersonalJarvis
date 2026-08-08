/**
 * A detached coding view must leave NO mounted IDE behind in the main window.
 *
 * The sticky mount (MainView.sticky.test.tsx) keeps the Agentic IDE alive
 * across section changes because unmounting it is destructive. Detaching
 * inverts the rule: the solo window's panes claim the PTY streams, and a
 * second mounted instance — even a hidden sticky one — steals every pane's
 * output. So while a coding view is in `detachedViews`, the main window must
 * genuinely unmount its instance and show the placeholder instead; the solo
 * window itself (store flag `solo`) is exempt because it IS the detached
 * instance. The remount rides the DetachedViewClosed event clearing the
 * registry — pinned here by clearing the store the same way.
 */
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MainView } from "@/components/layout/MainView";
import { useEventStore } from "@/store/events";

const ide = vi.hoisted(() => ({ mounts: 0, unmounts: 0 }));

vi.mock("@/views/AgenticIdeView", async () => {
  const { useEffect } = await vi.importActual<typeof import("react")>("react");
  return {
    AgenticIdeView: () => {
      useEffect(() => {
        ide.mounts += 1;
        return () => {
          ide.unmounts += 1;
        };
      }, []);
      return <div data-testid="ide-stub">workspace</div>;
    },
  };
});

vi.mock("@/views/ChatsView", () => ({
  ChatsView: () => <div data-testid="chats-stub">chats</div>,
  ViewHeader: () => null,
}));

function setStore(state: {
  activeSection?: string;
  solo?: boolean;
  detachedViews?: string[];
}) {
  act(() => {
    useEventStore.setState(state as never);
  });
}

beforeEach(() => {
  ide.mounts = 0;
  ide.unmounts = 0;
  setStore({ activeSection: "chats", solo: false, detachedViews: [] });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("MainView — detached coding view", () => {
  it("unmounts the sticky IDE when a coding view detaches", async () => {
    render(<MainView />);
    setStore({ activeSection: "agentic-ide" });
    await screen.findByTestId("ide-stub");
    expect(ide.mounts).toBe(1);

    // The desktop shell reports the view now lives in its own window.
    setStore({ detachedViews: ["agentic-ide"] });

    await waitFor(() => expect(screen.queryByTestId("ide-stub")).toBeNull());
    expect(ide.unmounts).toBe(1); // genuine unmount, not a CSS hide
    expect(screen.queryByTestId("sticky-agentic-ide")).toBeNull();
  });

  it("shows the placeholder with focus and bring-back actions", async () => {
    setStore({ activeSection: "agentic-ide", detachedViews: ["agentic-ide"] });
    render(<MainView />);

    await screen.findByTestId("detached-view-placeholder");
    expect(screen.getByTestId("detached-focus-button")).toBeTruthy();
    expect(screen.getByTestId("detached-reattach-button")).toBeTruthy();
  });

  it("covers the whole coding family while any coding view is detached", async () => {
    // agentic-ide detached, user opens the classic grid: same pane pool, so
    // the placeholder — not a second instance — must render.
    setStore({
      activeSection: "agentic-ide-classic",
      detachedViews: ["agentic-ide"],
    });
    render(<MainView />);

    await screen.findByTestId("detached-view-placeholder");
    expect(ide.mounts).toBe(0);
  });

  it("remounts through the sticky path once the registry clears", async () => {
    setStore({ activeSection: "agentic-ide", detachedViews: ["agentic-ide"] });
    render(<MainView />);
    await screen.findByTestId("detached-view-placeholder");

    // DetachedViewClosed → registry empty → the section returns here.
    setStore({ detachedViews: [] });

    await screen.findByTestId("ide-stub");
    expect(ide.mounts).toBe(1);
  });

  it("keeps non-coding sections rendering while the IDE is detached", async () => {
    setStore({ activeSection: "chats", detachedViews: ["agentic-ide"] });
    render(<MainView />);

    await screen.findByTestId("chats-stub");
    expect(screen.queryByTestId("detached-view-placeholder")).toBeNull();
  });

  it("mounts the IDE in the solo window despite its registry entry", async () => {
    // The solo window IS the detached instance the registry entry refers to.
    setStore({
      activeSection: "agentic-ide",
      solo: true,
      detachedViews: ["agentic-ide"],
    });
    render(<MainView />);

    await screen.findByTestId("ide-stub");
    expect(screen.queryByTestId("detached-view-placeholder")).toBeNull();
  });
});
