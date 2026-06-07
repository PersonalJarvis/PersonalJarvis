import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

interface BoardCardProps {
  children: ReactNode;
  className?: string;
  /** Faint primary glow in the corner — for the hero / signature surface. */
  glow?: boolean;
}

/**
 * The board's single elevated surface. Depth comes from light, not from hard
 * borders: a subtle top-lit gradient lifts the card off the near-black page,
 * a hairline white border replaces the harsh grey, and an inset highlight +
 * soft drop shadow give it a physical edge. This is the whole "premium, not
 * AI-slop" move applied consistently.
 */
export function BoardCard({ children, className, glow }: BoardCardProps) {
  return (
    <div
      className={cn(
        "relative overflow-hidden rounded-[20px]",
        "bg-gradient-to-b from-white/[0.055] to-white/[0.015]",
        "border border-white/[0.07]",
        "shadow-[inset_0_1px_0_0_rgba(255,255,255,0.06),0_16px_40px_-20px_rgba(0,0,0,0.8)]",
        className,
      )}
    >
      {glow && (
        <div className="pointer-events-none absolute -right-16 -top-20 h-56 w-56 rounded-full bg-primary/[0.12] blur-3xl" />
      )}
      {children}
    </div>
  );
}
