import type { CSSProperties } from "react";

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
  exiting?: boolean;
  onExited?: () => void;
}

export function BootSplash({ name, status, shift, exiting = false, onExited }: BootSplashProps) {
  return (
    <div
      id="jarvis-boot-splash"
      className={exiting ? "jbs-exit" : undefined}
      style={{ "--jbs-shift": shift } as CSSProperties}
      onAnimationEnd={(event) => {
        if (exiting && event.target === event.currentTarget) onExited?.();
      }}
    >
      <div className="jbs-stage" aria-hidden="true">
        <span className="jbs-aura" />
        <div className="jbs-mark">
          <img src="/jarvis-gigi-256.png" alt="" width={256} height={256} />
          <span className="jbs-sheen" />
        </div>
      </div>
      <div className="name">{name}</div>
      <div className="jbs-progress" aria-hidden="true">
        <span />
      </div>
      <div className="sub" role="status" aria-live="polite">
        {status}
      </div>
    </div>
  );
}
