import { type CSSProperties, useEffect, useState } from "react";

/**
 * The boot splash, as React renders it.
 *
 * `index.html` paints the identical markup before the bundle loads; its inline
 * `<style>` owns every rule (the `#jarvis-boot-splash` / `jbs-*` classes), so
 * both copies look the same and the style is there before any JavaScript runs.
 * Keep the markup of the two in lockstep.
 *
 * `shift` is a negative animation delay: the time the HTML splash has already
 * been on screen, so React's copy continues the same choreography instead of
 * replaying the intro from zero when createRoot swaps the DOM.
 */
interface BootSplashProps {
  name: string;
  status: string;
  shift: string;
  /** Fill of the progress line, 0–1, set per boot milestone by the caller. */
  progress: number;
  /** Seconds the line takes to reach `progress` — long for a slow approach. */
  settle: number;
  exiting?: boolean;
  onExited?: () => void;
}

/** Where the static HTML's own creep animation leaves the line. */
const HANDOFF_PROGRESS = 0.3;

export function BootSplash({
  name,
  status,
  shift,
  progress,
  settle,
  exiting = false,
  onExited,
}: BootSplashProps) {
  // First paint at the HTML splash's hand-off point, then move to the target
  // on the next frame so the transition animates the step instead of jumping.
  const [shown, setShown] = useState(HANDOFF_PROGRESS);
  useEffect(() => {
    const frame = requestAnimationFrame(() => setShown(progress));
    return () => cancelAnimationFrame(frame);
  }, [progress]);

  return (
    <div
      id="jarvis-boot-splash"
      className={exiting ? "jbs-exit" : undefined}
      style={{ "--jbs-shift": shift } as CSSProperties}
      onAnimationEnd={(event) => {
        if (exiting && event.target === event.currentTarget) onExited?.();
      }}
    >
      <div className="jbs-group">
        <div className="jbs-mark" aria-hidden="true">
          <img src="/jarvis-gigi-256.png" alt="" width={256} height={256} />
        </div>
        <div className="name">{name}</div>
        <div
          className="jbs-progress jbs-live"
          aria-hidden="true"
          style={{ "--jbs-p": shown, "--jbs-pt": `${settle}s` } as CSSProperties}
        >
          <i />
        </div>
        <div className="sub" role="status" aria-live="polite">
          {status}
        </div>
      </div>
    </div>
  );
}
