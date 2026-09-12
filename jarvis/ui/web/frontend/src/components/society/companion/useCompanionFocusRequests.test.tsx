import { useCallback, useEffect, useRef } from "react";
import { act, render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Point } from "./kinematics";
import { useCompanionFocusRequests } from "./useCompanionFocusRequests";
import { usePendingCompanionFocus } from "./usePendingCompanionFocus";

describe("host-owned companion focus", () => {
  it.each([false, true])("preserves intent but never replays consumed focus after renderer recreation (ready=%s)", (initiallyReady) => {
    const calls: number[] = [];
    let request: () => void = () => undefined;
    let cancel: () => void = () => undefined;
    function Renderer({ pending, ready, acknowledge }: { pending: number; ready: boolean; acknowledge: (id: number) => void }) {
      const pose = useRef<Point | null>(null);
      useEffect(() => { pose.current = ready ? [1, 2, 3] : null; }, [ready]);
      const apply = useCallback(() => { calls.push(pending); acknowledge(pending); return true; }, [pending, acknowledge]);
      usePendingCompanionFocus(pending, pose, Number(ready), apply);
      return null;
    }
    function Host({ generation, ready }: { generation: number; ready: boolean }) {
      const intent = useCompanionFocusRequests();
      request = intent.request; cancel = intent.cancel;
      return <Renderer key={generation} pending={intent.pending} acknowledge={intent.acknowledge} ready={ready} />;
    }
    const view = render(<Host generation={1} ready={initiallyReady} />);
    act(() => request());
    expect(calls).toEqual(initiallyReady ? [1] : []);
    view.rerender(<Host generation={2} ready={true} />);
    expect(calls).toEqual([1]);
    view.rerender(<Host generation={3} ready={true} />);
    expect(calls).toEqual([1]);
    act(() => request());
    expect(calls).toEqual([1, 2]);
    view.rerender(<Host generation={4} ready={false} />);
    act(() => request());
    act(() => cancel());
    view.rerender(<Host generation={5} ready={true} />);
    expect(calls).toEqual([1, 2]);
    view.unmount();
  });
});
