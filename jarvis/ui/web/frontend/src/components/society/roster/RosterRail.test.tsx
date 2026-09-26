import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { SocietyAgent } from "@/components/society/data";
import { useRetireStore } from "@/components/society/world/retireStore";
import { useEventStore } from "@/store/events";
import { RosterRail } from "./RosterRail";

vi.mock("@/i18n", () => ({ useT: () => (key: string) => key }));

afterEach(() => {
  useRetireStore.getState().cutShort();
  cleanup();
  localStorage.clear();
  vi.unstubAllGlobals();
  useEventStore.setState({ assistantName: "Assistant" });
});

function agent(over: Partial<SocietyAgent> & Pick<SocietyAgent, "agentId" | "name">): SocietyAgent {
  return {
    title: "Title",
    description: "",
    tier: "specialist",
    provider: "",
    providerLabel: "",
    model: "",
    effort: "",
    figure: null,
    palette: { primary: "#000", secondary: "#111", accent: "#222" },
    grantMode: "all",
    toolGrants: [],
    focus: [],
    denies: [],
    approvalRules: { requireApproval: [], alwaysAllow: [] },
    permissionCeiling: "ask",
    dailyBudgetUsd: 0,
    checkpoint: "idle",
    state: "idle",
    lifecycle: "active",
    createdMs: 0,
    maxConcurrentRuns: 1,
    workspaceDir: "",
    wikiNamespace: "",
    chatSessionId: null,
    routines: [],
    stats: { runs: 0, totalCostUsd: 0, spentTodayUsd: 0, lastActiveMs: null },
    ...over,
  };
}

const baseProps = {
  loading: false,
  sample: false,
  onOpen: () => undefined,
  onCreate: () => undefined,
};

describe("RosterRail status", () => {
  it("shows a loading spinner while an agent is thinking", () => {
    render(
      <RosterRail
        {...baseProps}
        agents={[agent({ agentId: "a", name: "A", state: "working" })]}
        activeAgentId={null}
      />,
    );
    expect(screen.getByRole("status", { name: "society.roster.thinking" })).toBeTruthy();
    expect(screen.queryByLabelText("society.roster.unread")).toBeNull();
  });

  it("shows a grey idle dot with no unseen results", () => {
    render(
      <RosterRail
        {...baseProps}
        agents={[agent({ agentId: "a", name: "A", state: "idle" })]}
        activeAgentId={null}
      />,
    );
    expect(screen.getByLabelText("society.state.idle")).toBeTruthy();
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("marks a finished agent green until it is opened", () => {
    const working = [agent({ agentId: "a", name: "A", state: "working" })];
    const done = [agent({ agentId: "a", name: "A", state: "idle" })];
    const { rerender } = render(
      <RosterRail {...baseProps} agents={working} activeAgentId={null} />,
    );
    rerender(<RosterRail {...baseProps} agents={done} activeAgentId={null} />);
    expect(screen.getByLabelText("society.roster.unread")).toBeTruthy();

    rerender(<RosterRail {...baseProps} agents={done} activeAgentId="a" />);
    expect(screen.queryByLabelText("society.roster.unread")).toBeNull();
    expect(screen.getByLabelText("society.state.idle")).toBeTruthy();
  });

  it("does not mark the open agent unread when it finishes in front of you", () => {
    const working = [agent({ agentId: "a", name: "A", state: "working" })];
    const done = [agent({ agentId: "a", name: "A", state: "idle" })];
    const { rerender } = render(
      <RosterRail {...baseProps} agents={working} activeAgentId="a" />,
    );
    rerender(<RosterRail {...baseProps} agents={done} activeAgentId="a" />);
    expect(screen.queryByLabelText("society.roster.unread")).toBeNull();
  });
});

describe("RosterRail agent actions", () => {
  const renderRail = (agents: SocietyAgent[]) => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(<QueryClientProvider client={client}>
      <RosterRail {...baseProps} agents={agents} activeAgentId={null} />
    </QueryClientProvider>);
  };

  it("opens exactly the three requested actions on right click", () => {
    renderRail([agent({ agentId: "a", name: "A" })]);
    fireEvent.contextMenu(screen.getByText("A"));
    expect(screen.getAllByRole("menuitem")).toHaveLength(3);
    expect(screen.getByRole("menuitem", { name: "society.roster.rename" })).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: "society.roster.hide" })).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: "society.roster.delete" })).toBeTruthy();
    expect(screen.queryByText(/copy|kopieren/i)).toBeNull();
  });

  it("hides an agent persistently and restores it from the hidden list", () => {
    const agents = [agent({ agentId: "a", name: "A" })];
    const { unmount } = renderRail(agents);
    fireEvent.contextMenu(screen.getByText("A"));
    fireEvent.click(screen.getByRole("menuitem", { name: "society.roster.hide" }));
    expect(screen.queryByText("A")).toBeNull();
    unmount();

    renderRail(agents);
    expect(screen.queryByText("A")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "society.roster.show_hidden" }));
    fireEvent.contextMenu(screen.getByText("A"));
    fireEvent.click(screen.getByRole("menuitem", { name: "society.roster.show" }));
    expect(JSON.parse(localStorage.getItem("society.roster.hidden-agent-ids") ?? "[]")).toEqual([]);
    expect(screen.getByText("A")).toBeTruthy();
  });

  it("renames via the existing roster API and confirms before deleting", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true });
    vi.stubGlobal("fetch", fetchMock);
    renderRail([agent({ agentId: "a", name: "A" })]);
    fireEvent.contextMenu(screen.getByText("A"));
    fireEvent.click(screen.getByRole("menuitem", { name: "society.roster.rename" }));
    fireEvent.change(screen.getByRole("textbox", { name: "society.roster.name" }), { target: { value: "Renamed" } });
    fireEvent.click(screen.getByRole("button", { name: "society.roster.save" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/society/agents/a", expect.objectContaining({ method: "PATCH", body: JSON.stringify({ name: "Renamed" }) })));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    fireEvent.contextMenu(screen.getByText("A"));
    fireEvent.click(screen.getByRole("menuitem", { name: "society.roster.delete" }));
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "society.roster.cancel" }));
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("archives only after the delete confirmation", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true });
    vi.stubGlobal("fetch", fetchMock);
    renderRail([agent({ agentId: "a", name: "A" })]);
    fireEvent.contextMenu(screen.getByText("A"));
    fireEvent.click(screen.getByRole("menuitem", { name: "society.roster.delete" }));
    expect(fetchMock).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "society.roster.delete" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/society/agents/a", { method: "DELETE" }));
  });
});

describe("RosterRail reorder", () => {
  const ORDER_KEY = "society.roster.order";
  const renderRail = (agents: SocietyAgent[]) => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(<QueryClientProvider client={client}>
      <RosterRail {...baseProps} agents={agents} activeAgentId={null} />
    </QueryClientProvider>);
  };

  const trio = () => [
    agent({ agentId: "a", name: "A" }),
    agent({ agentId: "b", name: "B" }),
    agent({ agentId: "c", name: "C" }),
  ];

  const rowOrder = () =>
    [...document.querySelectorAll("div[data-agent-id]")].map((el) =>
      el.getAttribute("data-agent-id"),
    );

  const rowOf = (name: string): HTMLElement => {
    const row = screen.getByText(name).closest("div[data-agent-id]");
    if (!(row instanceof HTMLElement)) throw new Error(`no row for ${name}`);
    return row;
  };

  const dragData = () => ({ setData: vi.fn(), getData: vi.fn().mockReturnValue(""), effectAllowed: "", dropEffect: "" });

  it("files a dragged row before the drop target and persists the order", () => {
    renderRail(trio());
    expect(rowOrder()).toEqual(["a", "b", "c"]);
    fireEvent.dragStart(rowOf("A"), { dataTransfer: dragData() });
    fireEvent.dragOver(rowOf("C"), { dataTransfer: dragData() });
    fireEvent.drop(rowOf("C"), { dataTransfer: dragData() });
    expect(rowOrder()).toEqual(["b", "a", "c"]);
    expect(JSON.parse(localStorage.getItem(ORDER_KEY) ?? "[]")).toEqual(["b", "a", "c"]);
  });

  it("restores the persisted order on mount", () => {
    localStorage.setItem(ORDER_KEY, JSON.stringify(["c", "a", "b"]));
    renderRail(trio());
    expect(rowOrder()).toEqual(["c", "a", "b"]);
  });

  it("moves a row with Alt + Arrow keys", () => {
    renderRail(trio());
    fireEvent.keyDown(rowOf("A"), { key: "ArrowDown", altKey: true });
    expect(rowOrder()).toEqual(["b", "a", "c"]);
    expect(JSON.parse(localStorage.getItem(ORDER_KEY) ?? "[]")).toEqual(["b", "a", "c"]);
    fireEvent.keyDown(rowOf("A"), { key: "ArrowUp", altKey: true });
    expect(rowOrder()).toEqual(["a", "b", "c"]);
  });

  it("ignores plain arrow keys without Alt", () => {
    renderRail(trio());
    fireEvent.keyDown(rowOf("A"), { key: "ArrowDown" });
    expect(rowOrder()).toEqual(["a", "b", "c"]);
    expect(localStorage.getItem(ORDER_KEY)).toBeNull();
  });

  it("locks rows while searching so the excerpt cannot file the order", () => {
    renderRail(trio());
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "a" } });
    expect(rowOf("A").getAttribute("draggable")).toBe("false");
    expect(localStorage.getItem(ORDER_KEY)).toBeNull();
  });
});

describe("RosterRail press-drag", () => {
  const ORDER_KEY = "society.roster.order";
  const renderRail = (agents: SocietyAgent[], props: Partial<typeof baseProps> = {}) => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(<QueryClientProvider client={client}>
      <RosterRail {...baseProps} {...props} agents={agents} activeAgentId={null} />
    </QueryClientProvider>);
  };

  const trio = () => [
    agent({ agentId: "a", name: "A" }),
    agent({ agentId: "b", name: "B" }),
    agent({ agentId: "c", name: "C" }),
  ];

  const rowOrder = () =>
    [...document.querySelectorAll("div[data-agent-id]")].map((el) =>
      el.getAttribute("data-agent-id"),
    );

  const rowOf = (name: string): HTMLElement => {
    const row = screen.getByText(name).closest("div[data-agent-id]");
    if (!(row instanceof HTMLElement)) throw new Error(`no row for ${name}`);
    return row;
  };

  /** Fake vertical layout so pointer heights resolve to real rows. */
  const mockLayout = (tops: Record<string, number>, height = 48) => {
    document.querySelectorAll("div[data-agent-id]").forEach((el) => {
      const id = el.getAttribute("data-agent-id") ?? "";
      const top = tops[id] ?? 0;
      vi.spyOn(el, "getBoundingClientRect").mockReturnValue({
        x: 0,
        y: top,
        top,
        left: 0,
        bottom: top + height,
        right: 200,
        width: 200,
        height,
        toJSON: () => ({}),
      } as DOMRect);
    });
  };

  /**
   * jsdom has no PointerEvent constructor, so testing-library drops
   * coordinates on pointer events. Dispatch real mouse events typed as
   * pointer events instead — the rail reads button/clientX/clientY off
   * them exactly like the browser does.
   */
  const pressRow = (el: Element, x: number, y: number) => {
    act(() => {
      el.dispatchEvent(new MouseEvent("pointerdown", { bubbles: true, cancelable: true, button: 0, clientX: x, clientY: y }));
    });
  };
  const movePointer = (x: number, y: number) => {
    act(() => {
      window.dispatchEvent(new MouseEvent("pointermove", { bubbles: true, cancelable: true, button: 0, clientX: x, clientY: y }));
    });
  };
  const releasePointer = (x: number, y: number) => {
    act(() => {
      window.dispatchEvent(new MouseEvent("pointerup", { bubbles: true, cancelable: true, button: 0, clientX: x, clientY: y }));
    });
  };

  it("starts from a press on the name itself and files the row at the drop point", () => {
    const onOpen = vi.fn();
    renderRail(trio(), { onOpen });
    mockLayout({ a: 0, b: 48, c: 96 });
    // Press on the inner name button, not the grip: this is the gesture that
    // did nothing before press-drag existed.
    pressRow(screen.getByText("A"), 10, 10);
    movePointer(10, 80);
    releasePointer(10, 80);
    expect(rowOrder()).toEqual(["b", "a", "c"]);
    expect(JSON.parse(localStorage.getItem(ORDER_KEY) ?? "[]")).toEqual(["b", "a", "c"]);
    // The click that follows a drag must not open the chat.
    fireEvent.click(screen.getByText("A"));
    expect(onOpen).not.toHaveBeenCalled();
  });

  it("drops after the last row when released below the list", () => {
    renderRail(trio());
    mockLayout({ a: 0, b: 48, c: 96 });
    pressRow(rowOf("B"), 10, 60);
    movePointer(10, 220);
    releasePointer(10, 220);
    expect(rowOrder()).toEqual(["a", "c", "b"]);
    expect(JSON.parse(localStorage.getItem(ORDER_KEY) ?? "[]")).toEqual(["a", "c", "b"]);
  });

  it("treats a tiny press as a click so the chat still opens", () => {
    const onOpen = vi.fn();
    renderRail(trio(), { onOpen });
    mockLayout({ a: 0, b: 48, c: 96 });
    pressRow(rowOf("A"), 10, 10);
    releasePointer(11, 11);
    fireEvent.click(screen.getByText("A"));
    expect(onOpen).toHaveBeenCalledWith("a");
    expect(localStorage.getItem(ORDER_KEY)).toBeNull();
  });

  it("renders no grip handles; the whole row is the drag surface", () => {
    const { container } = renderRail(trio());
    expect(container.querySelector(".lucide-grip-vertical")).toBeNull();
    expect(rowOf("A").className).toContain("cursor-grab");
  });
});

describe("RosterRail lead display name", () => {
  const renderRail = (agents: SocietyAgent[]) => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(<QueryClientProvider client={client}>
      <RosterRail {...baseProps} agents={agents} activeAgentId={null} />
    </QueryClientProvider>);
  };

  it("shows the wake-word name for the lead instead of the backend name", () => {
    useEventStore.setState({ assistantName: "Hanna" });
    renderRail([agent({ agentId: "jarvis", name: "Jarvis", tier: "lead", title: "Lead" })]);
    expect(screen.getByTestId("society-lead-hero")).toBeTruthy();
    expect(screen.getByText("Hanna")).toBeTruthy();
    expect(screen.queryByText("Jarvis")).toBeNull();
  });

  it("finds the lead by display name and by stored name", () => {
    useEventStore.setState({ assistantName: "Hanna" });
    const lead = agent({ agentId: "jarvis", name: "Jarvis", tier: "lead", title: "Lead" });
    const { unmount } = renderRail([lead]);
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "hanna" } });
    expect(screen.getByText("Hanna")).toBeTruthy();
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "jarvis" } });
    expect(screen.getByText("Hanna")).toBeTruthy();
    unmount();
  });
});
