import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { useLayoutEffect, useState } from "react";

import { NEAR_END_PX, isNearEnd, scrollViewportOf, useStickToBottom } from "@/hooks/useStickToBottom";

afterEach(cleanup);

/** jsdom lays nothing out, so the scroller is told how tall it "is". */
function measure(el: HTMLElement, scrollTop: number, scrollHeight: number, clientHeight: number) {
  Object.defineProperty(el, "scrollHeight", { value: scrollHeight, configurable: true });
  Object.defineProperty(el, "clientHeight", { value: clientHeight, configurable: true });
  el.scrollTop = scrollTop;
}

/** A minimal surface that grows, like a chat thread does. */
function Thread({ initial = 3 }: { initial?: number }) {
  const [count, setCount] = useState(initial);
  const { rootRef, contentRef, atEnd, jumpToEnd, follow } = useStickToBottom();
  useLayoutEffect(follow, [follow, count]);
  return (
    <div>
      <div ref={rootRef} data-testid="scroller" style={{ overflowY: "auto" }}>
        <div ref={contentRef}>
          {Array.from({ length: count }, (_, i) => (
            <p key={i}>message {i}</p>
          ))}
        </div>
      </div>
      <button type="button" onClick={() => setCount((n) => n + 1)}>
        add
      </button>
      {!atEnd && (
        <button type="button" data-testid="back" onClick={jumpToEnd}>
          back
        </button>
      )}
    </div>
  );
}

describe("isNearEnd", () => {
  it("treats the last stretch above the bottom as being at the end", () => {
    expect(isNearEnd(900, 1000, 100)).toBe(true);
    expect(isNearEnd(900 - NEAR_END_PX, 1000, 100)).toBe(true);
    expect(isNearEnd(900 - NEAR_END_PX - 1, 1000, 100)).toBe(false);
    expect(isNearEnd(0, 1000, 100)).toBe(false);
  });

  it("calls content shorter than its viewport already at the end", () => {
    expect(isNearEnd(0, 100, 400)).toBe(true);
  });
});

describe("scrollViewportOf", () => {
  it("finds the Radix viewport inside a root, and falls back to the root itself", () => {
    const root = document.createElement("div");
    expect(scrollViewportOf(root)).toBe(root);
    const viewport = document.createElement("div");
    viewport.setAttribute("data-radix-scroll-area-viewport", "");
    root.appendChild(viewport);
    expect(scrollViewportOf(root)).toBe(viewport);
    expect(scrollViewportOf(null)).toBeNull();
  });
});

describe("useStickToBottom", () => {
  it("follows new content while the view sits at the end", () => {
    render(<Thread />);
    const scroller = screen.getByTestId("scroller");
    measure(scroller, 950, 1000, 50);
    act(() => {
      fireEvent.scroll(scroller);
    });

    measure(scroller, 950, 2000, 50);
    act(() => {
      fireEvent.click(screen.getByText("add"));
    });
    expect(scroller.scrollTop).toBe(2000);
  });

  it("leaves a reader who scrolled up exactly where they are", () => {
    render(<Thread />);
    const scroller = screen.getByTestId("scroller");
    measure(scroller, 100, 1000, 50);
    act(() => {
      fireEvent.scroll(scroller);
    });

    act(() => {
      fireEvent.click(screen.getByText("add"));
    });
    expect(scroller.scrollTop).toBe(100);
  });

  it("offers the way back only while the reader is away from the end", () => {
    render(<Thread />);
    const scroller = screen.getByTestId("scroller");
    expect(screen.queryByTestId("back")).toBeNull();

    measure(scroller, 100, 1000, 50);
    act(() => {
      fireEvent.scroll(scroller);
    });
    expect(screen.getByTestId("back")).toBeTruthy();

    // Taking it resumes following, so the button retires itself.
    act(() => {
      fireEvent.click(screen.getByTestId("back"));
    });
    expect(scroller.scrollTop).toBe(1000);
    expect(screen.queryByTestId("back")).toBeNull();

    measure(scroller, 1000, 2000, 50);
    act(() => {
      fireEvent.click(screen.getByText("add"));
    });
    expect(scroller.scrollTop).toBe(2000);
  });
});
