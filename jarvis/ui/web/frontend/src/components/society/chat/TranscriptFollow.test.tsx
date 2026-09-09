import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Transcript } from "@/components/society/chat/AgentChatPanel";
import { EMPTY_TIMELINE, reduceEvents } from "@/components/agentchat/reduce";
import type { AgentChatEvent } from "@/lib/agentChatApi";
import type { SocietyAgent } from "@/components/society/data";

/**
 * The society chat's transcript must follow the conversation down.
 *
 * The complaint: the view sat still while an answer streamed in — the reader
 * scrolled by hand after every reasoning trace. The transcript scrolled a
 * bottom sentinel into view on `[items.length, busy]` only, so growth that
 * arrives WITHOUT a new item (a streaming reasoning trace, a streamed
 * answer) never pulled the view along. It now shares the rule every other
 * conversation surface follows (hooks/useStickToBottom): new output pulls
 * the view along only while it already sits at the end.
 */

const agent = { agentId: "a1", name: "Scout", tier: "worker" } as unknown as SocietyAgent;

let seq = 0;
function ev(kind: string, payload: Record<string, unknown>): AgentChatEvent {
  seq += 1;
  return { seq, ts_ms: 1_000, kind, payload } as AgentChatEvent;
}

const TURN = { provider: "openai", model: "gpt-x", effort: "", runner: "brain" };

function runningTurn(reasoning: string) {
  return [
    ev("user_message", { text: "look into it" }),
    ev("turn_started", { turn_id: "t1", ...TURN }),
    ev("reasoning_started", { turn_id: "t1", message_id: "r1" }),
    ev("reasoning_delta", { turn_id: "t1", message_id: "r1", text: reasoning }),
  ];
}

function draw(events: AgentChatEvent[]) {
  const { items } = reduceEvents(EMPTY_TIMELINE, events);
  return render(<Transcript items={items} agent={agent} roster={[]} onDecide={async () => {}} />);
}

/** jsdom lays nothing out, so the scroller is told how tall it "is". */
function measure(el: HTMLElement, scrollTop: number, scrollHeight: number, clientHeight: number) {
  Object.defineProperty(el, "scrollHeight", { value: scrollHeight, configurable: true });
  Object.defineProperty(el, "clientHeight", { value: clientHeight, configurable: true });
  el.scrollTop = scrollTop;
}

/** Stand in for the layout engine: the test fires content growth by hand. */
let roFire: (() => void) | null = null;
class FakeResizeObserver {
  constructor(cb: () => void) {
    roFire = cb;
  }
  observe() {}
  unobserve() {}
  disconnect() {}
}

function scroller() {
  return screen.getByTestId("society-transcript");
}

afterEach(() => {
  cleanup();
  seq = 0;
  roFire = null;
  vi.unstubAllGlobals();
});

describe("society transcript follow", () => {
  it("follows a reasoning trace that streams without any new item", () => {
    vi.stubGlobal("ResizeObserver", FakeResizeObserver);
    const events = runningTurn("First I will check the inbox");
    const { rerender } = draw(events);
    const el = scroller();
    measure(el, 950, 1000, 50);
    act(() => {
      fireEvent.scroll(el);
    });

    // More words, same items: the trace grew in place.
    const grown = reduceEvents(EMPTY_TIMELINE, [
      ...events,
      ev("reasoning_delta", { turn_id: "t1", message_id: "r1", text: ", then the calendar, then the drive." }),
    ]).items;
    expect(grown).toHaveLength(reduceEvents(EMPTY_TIMELINE, events).items.length);
    rerender(<Transcript items={grown} agent={agent} roster={[]} onDecide={async () => {}} />);
    measure(el, 950, 2000, 50);
    act(() => {
      roFire?.();
    });
    expect(el.scrollTop).toBe(2000);
  });

  it("pulls new turns along while the view sits at the end", () => {
    const events = runningTurn("First I will check the inbox");
    const { rerender } = draw(events);
    const el = scroller();
    measure(el, 950, 1000, 50);
    act(() => {
      fireEvent.scroll(el);
    });

    const more = reduceEvents(EMPTY_TIMELINE, [
      ...events,
      ev("assistant_text", { turn_id: "t1", message_id: "m1", text: "Done looking." }),
      ev("turn_finished", { turn_id: "t1", status: "done", duration_ms: 4000, usage: {} }),
      ev("user_message", { text: "And then?" }),
      ev("turn_started", { turn_id: "t2", ...TURN }),
    ]).items;
    expect(more.length).toBeGreaterThan(reduceEvents(EMPTY_TIMELINE, events).items.length);
    // Measured before the new turn lands, like a taller page under the view.
    measure(el, 950, 3000, 50);
    rerender(<Transcript items={more} agent={agent} roster={[]} onDecide={async () => {}} />);
    // The layout effect follows on the new item.
    expect(el.scrollTop).toBe(3000);
  });

  it("leaves a reader who scrolled up alone and offers the way back", () => {
    vi.stubGlobal("ResizeObserver", FakeResizeObserver);
    const events = runningTurn("First I will check the inbox");
    const { rerender } = draw(events);
    const el = scroller();
    measure(el, 100, 1000, 50);
    act(() => {
      fireEvent.scroll(el);
    });
    expect(screen.getByTestId("society-scroll-end")).toBeTruthy();

    const grown = reduceEvents(EMPTY_TIMELINE, [
      ...events,
      ev("reasoning_delta", { turn_id: "t1", message_id: "r1", text: ", then the calendar." }),
    ]).items;
    rerender(<Transcript items={grown} agent={agent} roster={[]} onDecide={async () => {}} />);
    measure(el, 100, 2000, 50);
    act(() => {
      roFire?.();
    });
    expect(el.scrollTop).toBe(100);

    // Taking the way back resumes following, so the button retires itself.
    act(() => {
      fireEvent.click(screen.getByTestId("society-scroll-end"));
    });
    expect(el.scrollTop).toBe(2000);
    expect(screen.queryByTestId("society-scroll-end")).toBeNull();
  });
});
