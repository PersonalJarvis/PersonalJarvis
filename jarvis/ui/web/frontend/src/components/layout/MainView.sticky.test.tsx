/**
 * The Agentic IDE survives a section change; every other section does not.
 *
 * Why this test exists (2026-07-29): sections were all switched the same way —
 * one mounted, the rest thrown away — and for a section whose contents are
 * live terminals that is destructive rather than economical. Leaving the IDE
 * disposed a dozen xterm instances and their sockets; coming back built new
 * ones and had the backend replay every pane's screen into them, and until
 * that finished the freshly mounted view knew of no workspace at all and drew
 * its ONBOARDING WIZARD in front of a workspace that had never stopped running
 * (maintainer report, ~3 s of "start over" on the way back).
 *
 * So the assertions here are about identity, not appearance: the same instance
 * must still be there afterwards, un-remounted, merely hidden — and it must be
 * TOLD it is hidden, because a `display: none` subtree cannot measure that for
 * itself and its polling would otherwise run for nobody.
 */
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MainView } from "@/components/layout/MainView";
import { useEventStore } from "@/store/events";

/**
 * Mount/render bookkeeping for the stubbed IDE.
 *
 * `mounts` is what proves the instance survived: a remount is invisible in the
 * DOM (the same markup comes back) and shows up only as a second mount.
 */
const ide = vi.hoisted(() => ({ mounts: 0, lastOnScreen: undefined as unknown }));

vi.mock("@/views/AgenticIdeView", async () => {
  const { useEffect } = await vi.importActual<typeof import("react")>("react");
  return {
    AgenticIdeView: ({ onScreen }: { onScreen?: boolean }) => {
      ide.lastOnScreen = onScreen;
      useEffect(() => {
        ide.mounts += 1;
      }, []);
      return <div data-testid="ide-stub">workspace</div>;
    },
  };
});

// The default section, statically imported by MainView — stubbed so this test
// pays for none of the chat surface.
vi.mock("@/views/ChatsView", () => ({
  ChatsView: () => <div data-testid="chats-stub">chats</div>,
  ViewHeader: () => null,
}));

vi.mock("@/views/OutputsView", () => ({
  OutputsView: () => <div data-testid="outputs-stub">outputs</div>,
}));

function goTo(section: string) {
  act(() => {
    useEventStore.getState().setActiveSection(section as never);
  });
}

beforeEach(() => {
  ide.mounts = 0;
  ide.lastOnScreen = undefined;
  goTo("chats");
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("MainView — the classic terminal grid is sticky", () => {
  it("does not mount the IDE for a user who never opens it", async () => {
    render(<MainView />);
    await screen.findByTestId("chats-stub");

    // Sticky, not always-mounted: the section is only worth keeping once it has
    // something to keep.
    expect(screen.queryByTestId("sticky-agentic-ide")).toBeNull();
    expect(ide.mounts).toBe(0);
  });

  it("keeps the same instance mounted across a section change", async () => {
    render(<MainView />);
    goTo("agentic-ide-classic");
    await screen.findByTestId("ide-stub");
    expect(ide.mounts).toBe(1);

    goTo("outputs");
    await screen.findByTestId("outputs-stub");

    // Still there, and still the FIRST one: a second mount would mean a dozen
    // terminals were torn down and rebuilt for a section change.
    expect(screen.getByTestId("ide-stub")).toBeTruthy();
    expect(ide.mounts).toBe(1);

    goTo("agentic-ide-classic");
    await waitFor(() =>
      expect(screen.queryByTestId("outputs-stub")).toBeNull(),
    );
    expect(ide.mounts).toBe(1);
  });

  it("hides the IDE while another section is on screen", async () => {
    render(<MainView />);
    goTo("agentic-ide-classic");
    await screen.findByTestId("ide-stub");

    const sticky = screen.getByTestId("sticky-agentic-ide");
    expect(sticky.className).not.toContain("hidden");
    expect(sticky.getAttribute("aria-hidden")).toBe("false");

    goTo("outputs");
    await screen.findByTestId("outputs-stub");

    expect(sticky.className).toContain("hidden");
    expect(sticky.getAttribute("aria-hidden")).toBe("true");
  });

  it("tells the IDE whether it is the section on screen", async () => {
    render(<MainView />);
    goTo("agentic-ide-classic");
    await screen.findByTestId("ide-stub");
    expect(ide.lastOnScreen).toBe(true);

    goTo("outputs");
    await screen.findByTestId("outputs-stub");

    // The view cannot measure this itself once it is `display: none`, and its
    // pollers need the answer to stop asking the backend about panes nobody is
    // looking at.
    await waitFor(() => expect(ide.lastOnScreen).toBe(false));
  });

  it("shows only the IDE while it is the active section", async () => {
    render(<MainView />);
    goTo("agentic-ide-classic");
    await screen.findByTestId("ide-stub");

    // Two live copies of a workspace would fight over every pane's output
    // stream, so the ordinary switch must render nothing at all here.
    expect(screen.queryByTestId("chats-stub")).toBeNull();
    expect(screen.queryByTestId("outputs-stub")).toBeNull();
  });
});
