import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { DOCK_RAIL_GEOMETRY, DockRail } from "@/components/layout/DockRail";
import { NAV_GROUPS } from "@/components/layout/navGroups";
import { useEventStore } from "@/store/events";

// usePluginAttention polls /api/marketplace/plugins; the test drives it.
const pluginAttentionMock = vi.hoisted(() => ({ needsReconnect: false }));
vi.mock("@/hooks/usePluginAttention", () => ({
  usePluginAttention: () =>
    pluginAttentionMock.needsReconnect
      ? { count: 1, names: ["Cloudflare"] }
      : { count: 0, names: [] },
}));

const ITEMS = NAV_GROUPS.flat();
const { BASE, GAP, PAD_TOP } = DOCK_RAIL_GEOMETRY;

/** clientY that lands on the centre of icon `i` (jsdom rects sit at 0,0). */
function centreOf(i: number): number {
  return PAD_TOP + GAP + i * (BASE + GAP) + BASE / 2;
}

/** jsdom has no PointerEvent; a MouseEvent under the pointer name carries the
 *  clientY the rail reads. */
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

function renderRail() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <DockRail />
    </QueryClientProvider>,
  );
}

describe("DockRail", () => {
  beforeEach(() => {
    pluginAttentionMock.needsReconnect = false;
    useEventStore.setState({ activeSection: "chats", conversations: [] });
  });
  afterEach(() => cleanup());

  test("shows every section of the sidebar's NAV_GROUPS as one named button", () => {
    renderRail();
    for (const item of ITEMS) {
      expect(screen.getByTestId(`nav-row-${item.id}`)).toBeTruthy();
    }
    expect(screen.getAllByRole("button")).toHaveLength(ITEMS.length);
    expect(screen.getByTestId("nav-row-chats").getAttribute("aria-current")).toBe("page");
    // No native title: the rail draws its own label, and a browser tooltip on
    // top of it would be a second, late label.
    expect(screen.getByTestId("nav-row-chats").getAttribute("title")).toBeNull();
  });

  test("keeps the active control on the app's glass surface", () => {
    renderRail();
    expect(screen.getByTestId("nav-row-chats").classList).toContain("jarvis-message-surface");
    expect(screen.getByTestId("nav-row-tasks").classList).not.toContain(
      "jarvis-message-surface",
    );
  });

  test("hovering names exactly one icon, and crossing to the next one renames it", () => {
    renderRail();
    const rail = screen.getByTestId("dock-rail");

    expect(screen.queryByTestId("dock-label")).toBeNull();

    moveTo(rail, centreOf(2));
    let labels = screen.getAllByTestId("dock-label");
    expect(labels).toHaveLength(1);
    expect(labels[0].textContent).toContain(
      screen.getByTestId(`nav-row-${ITEMS[2].id}`).getAttribute("aria-label"),
    );

    moveTo(rail, centreOf(3));
    labels = screen.getAllByTestId("dock-label");
    expect(labels).toHaveLength(1);
    expect(labels[0].textContent).toContain(
      screen.getByTestId(`nav-row-${ITEMS[3].id}`).getAttribute("aria-label"),
    );
  });

  test("leaving the rail takes the label away", async () => {
    renderRail();
    const rail = screen.getByTestId("dock-rail");
    moveTo(rail, centreOf(1));
    expect(screen.getAllByTestId("dock-label")).toHaveLength(1);
    leave(rail);
    await waitFor(() => expect(screen.queryByTestId("dock-label")).toBeNull(), {
      timeout: 2000,
    });
  });

  test("picking an icon jumps to its section", () => {
    renderRail();
    act(() => {
      fireEvent.click(screen.getByTestId("nav-row-tasks"));
    });
    expect(useEventStore.getState().activeSection).toBe("tasks");
  });

  test("keyboard focus names the icon too, so the rail is not a row of blank glyphs", () => {
    renderRail();
    act(() => {
      fireEvent.focus(screen.getByTestId("nav-row-docs"));
    });
    const labels = screen.getAllByTestId("dock-label");
    expect(labels).toHaveLength(1);
    expect(labels[0].textContent).toContain(
      screen.getByTestId("nav-row-docs").getAttribute("aria-label"),
    );
  });

  test("a plugin that needs a reconnect lights Skills amber and sends the click to Plugins", () => {
    pluginAttentionMock.needsReconnect = true;
    renderRail();
    const pip = screen.getByTestId("nav-warn-skills");
    expect(pip.getAttribute("aria-label")).toContain("Cloudflare");
    act(() => {
      fireEvent.click(screen.getByTestId("nav-row-skills"));
    });
    expect(useEventStore.getState().activeSection).toBe("plugins");
  });

  test("no amber pip when every plugin is healthy, and Skills lands on Skills", () => {
    renderRail();
    expect(screen.queryByTestId("nav-warn-skills")).toBeNull();
    act(() => {
      fireEvent.click(screen.getByTestId("nav-row-skills"));
    });
    expect(useEventStore.getState().activeSection).toBe("skills");
  });
});
