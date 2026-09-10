import { useRef } from "react";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { CanvasActivity, useCanvasAwake } from "./useCanvasAwake";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function Surface() {
  const ref = useRef<HTMLDivElement>(null);
  const awake = useCanvasAwake(ref);
  return <div ref={ref} data-testid="surface" data-awake={awake} />;
}

test("covered canvases sleep immediately and resume only when also in the viewport", () => {
  const observers: FakeObserver[] = [];
  class FakeObserver {
    disconnected = false;
    constructor(readonly callback: IntersectionObserverCallback) { observers.push(this); }
    observe() {}
    disconnect() { this.disconnected = true; }
    emit(isIntersecting: boolean) {
      this.callback([{ isIntersecting } as IntersectionObserverEntry], this as unknown as IntersectionObserver);
    }
  }
  vi.stubGlobal("IntersectionObserver", FakeObserver);
  const tree = (active: boolean) => <CanvasActivity.Provider value={active}><Surface /></CanvasActivity.Provider>;
  const view = render(tree(true));
  expect(screen.getByTestId("surface").dataset.awake).toBe("true");
  view.rerender(tree(false));
  expect(screen.getByTestId("surface").dataset.awake).toBe("false");
  act(() => observers[0].emit(false));
  view.rerender(tree(true));
  expect(screen.getByTestId("surface").dataset.awake).toBe("false");
  act(() => observers[0].emit(true));
  expect(screen.getByTestId("surface").dataset.awake).toBe("true");
  expect(observers).toHaveLength(1);
  expect(observers[0].disconnected).toBe(false);
  view.unmount();
  expect(observers[0].disconnected).toBe(true);
});

test("explicit occlusion also works in shells without IntersectionObserver", () => {
  vi.stubGlobal("IntersectionObserver", undefined);
  const view = render(<CanvasActivity.Provider value={false}><Surface /></CanvasActivity.Provider>);
  expect(screen.getByTestId("surface").dataset.awake).toBe("false");
  view.rerender(<CanvasActivity.Provider value={true}><Surface /></CanvasActivity.Provider>);
  expect(screen.getByTestId("surface").dataset.awake).toBe("true");
});
