import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, Square } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * Hold-to-abort: a stop button that must be HELD for `holdMs` to fire.
 *
 * Design contract (Outputs view, RUNNING mission cards):
 * - No confirm dialog — deliberate friction without confirmation fatigue.
 * - An SVG ring fills around the stop square while holding (pure CSS
 *   transition on stroke-dashoffset; a single setTimeout decides the
 *   logic, so no rAF loop and no per-frame re-renders).
 * - Releasing or leaving early snaps the ring back and arms nothing.
 * - `pending` (cancel request in flight) shows a spinner and ignores input.
 */
export function HoldToAbortButton({
  onConfirm,
  holdMs = 1200,
  pending = false,
  size = "sm",
  label,
  className,
}: {
  onConfirm: () => void;
  holdMs?: number;
  pending?: boolean;
  size?: "sm" | "md";
  label: string;
  className?: string;
}) {
  const [holding, setHolding] = useState(false);
  const timerRef = useRef<number | null>(null);

  const stop = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    setHolding(false);
  }, []);

  const start = useCallback(() => {
    if (pending || timerRef.current !== null) return;
    setHolding(true);
    timerRef.current = window.setTimeout(() => {
      timerRef.current = null;
      setHolding(false);
      onConfirm();
    }, holdMs);
  }, [pending, holdMs, onConfirm]);

  // Unmount cleanup — a dangling timeout must never fire onConfirm.
  useEffect(() => stop, [stop]);

  const box = size === "sm" ? "h-6 w-6" : "h-8 w-8";
  const icon = size === "sm" ? "h-2 w-2" : "h-2.5 w-2.5";
  // viewBox is 32x32; ring radius leaves room for the 3px stroke.
  const radius = 13.5;
  const circumference = 2 * Math.PI * radius;

  return (
    <button
      type="button"
      disabled={pending}
      aria-label={label}
      title={label}
      data-holding={holding ? "true" : "false"}
      onPointerDown={(e) => {
        e.stopPropagation();
        if (e.button === 0 || e.pointerType !== "mouse") start();
      }}
      onPointerUp={(e) => {
        e.stopPropagation();
        stop();
      }}
      onPointerLeave={stop}
      onPointerCancel={stop}
      onClick={(e) => e.stopPropagation()}
      onKeyDown={(e) => {
        if ((e.key === " " || e.key === "Enter") && !e.repeat) {
          e.preventDefault();
          start();
        }
      }}
      onKeyUp={(e) => {
        if (e.key === " " || e.key === "Enter") stop();
      }}
      className={cn(
        "group relative inline-flex shrink-0 select-none items-center justify-center",
        "rounded-full text-destructive/70 transition-colors",
        "hover:text-destructive focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-destructive/60",
        holding && "text-destructive",
        pending && "cursor-wait opacity-70",
        box,
        className,
      )}
    >
      <svg
        viewBox="0 0 32 32"
        aria-hidden="true"
        className="absolute inset-0 h-full w-full -rotate-90"
      >
        <circle
          cx="16"
          cy="16"
          r={radius}
          fill="none"
          strokeWidth="3"
          className="stroke-destructive/25"
        />
        <circle
          cx="16"
          cy="16"
          r={radius}
          fill="none"
          strokeWidth="3"
          strokeLinecap="round"
          className="stroke-destructive"
          strokeDasharray={circumference}
          strokeDashoffset={holding ? 0 : circumference}
          style={{
            transition: holding
              ? `stroke-dashoffset ${holdMs}ms linear`
              : "stroke-dashoffset 200ms ease-out",
          }}
        />
      </svg>
      {pending ? (
        <Loader2 className={cn(icon, "animate-spin")} />
      ) : (
        <Square className={icon} fill="currentColor" strokeWidth={0} />
      )}
    </button>
  );
}
