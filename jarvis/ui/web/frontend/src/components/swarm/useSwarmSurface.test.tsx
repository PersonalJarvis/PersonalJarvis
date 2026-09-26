import { useRef } from "react";
import { act, cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useSwarmSurface } from "./useSwarmSurface";

function Surface({ id, failed }: { id: string; failed: () => void }) {
  const host = useRef<HTMLDivElement>(null);
  const generation = useSwarmSurface(host, failed);
  return <div ref={host} data-testid={id}><canvas key={generation} data-generation={generation} /></div>;
}
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.useRealTimers(); });
describe("independent Swarm WebGL lifecycle", () => {
  it("rebuilds two lost contexts, degrades only the failing world, and releases contexts", () => {
    vi.useFakeTimers();
    const lose = vi.fn();
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(() => ({ getExtension: () => ({ loseContext: lose }) }) as unknown as WebGLRenderingContext);
    const alphaFailure = vi.fn(); const betaFailure = vi.fn();
    const view = render(<><Surface id="alpha" failed={alphaFailure} /><Surface id="beta" failed={betaFailure} /></>);
    const canvas = (id: string) => view.getByTestId(id).querySelector("canvas")!;
    for (let generation = 1; generation <= 2; generation++) {
      const event = new Event("webglcontextlost", { cancelable: true });
      fireEvent(canvas("alpha"), event);
      expect(event.defaultPrevented).toBe(true);
      act(() => { vi.advanceTimersByTime(250); });
      expect(canvas("alpha").dataset.generation).toBe(String(generation));
      expect(canvas("beta").dataset.generation).toBe("0");
    }
    fireEvent(canvas("alpha"), new Event("webglcontextlost", { cancelable: true }));
    expect(alphaFailure).toHaveBeenCalledOnce(); expect(betaFailure).not.toHaveBeenCalled();
    view.unmount();
    expect(lose).toHaveBeenCalledTimes(4);
  });
});
