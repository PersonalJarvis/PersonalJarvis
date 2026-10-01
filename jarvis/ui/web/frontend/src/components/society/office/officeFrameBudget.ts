/** Keep a secondary 3D view from spending a full render on every display tick. */
export function startOfficeFrameBudget(
  invalidate: () => void,
  fps: number,
  request: (callback: FrameRequestCallback) => number = requestAnimationFrame,
  cancel: (handle: number) => void = cancelAnimationFrame,
): () => void {
  const interval = 1000 / fps;
  let last: number | undefined;
  let stopped = false;
  let frame: number;
  const tick: FrameRequestCallback = (now) => {
    if (stopped) return;
    if (last === undefined || now - last >= interval - 0.1) {
      // Retain fractional frame time without trying to repay missed frames
      // after a stall. At 120/144 Hz the scene still receives at most 30 fps.
      last = last === undefined
        ? now
        : last + Math.max(1, Math.floor((now - last + 0.1) / interval)) * interval;
      invalidate();
    }
    frame = request(tick);
  };
  frame = request(tick);
  return () => {
    stopped = true;
    cancel(frame);
  };
}
