import { cn } from "@/lib/utils";

const DOT = 6;
const SLOT = 16;
const GAP = 6;

/**
 * Where the guide stands: one dot per beat and a pill that SLIDES to the
 * current one rather than a new dot lighting up. Done dots are ink, the ones
 * ahead are hairline — the pill is the only accent on the card's foot.
 */
export function ProgressDots({ count, index }: { count: number; index: number }) {
  return (
    <div
      aria-hidden
      className="relative flex items-center"
      style={{ gap: GAP, height: DOT }}
      data-testid="onboarding-dots"
    >
      {Array.from({ length: count }, (_, i) => (
        <span key={i} className="flex justify-center" style={{ width: SLOT }}>
          <span
            className={cn(
              "rounded-full transition-colors duration-300",
              i < index ? "bg-muted-foreground" : "bg-border-strong",
              i === index && "opacity-0",
            )}
            style={{ width: DOT, height: DOT }}
          />
        </span>
      ))}
      <span
        className="absolute left-0 top-1/2 rounded-full bg-accent transition-transform duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] motion-reduce:transition-none"
        style={{
          width: SLOT,
          height: DOT,
          transform: `translate(${index * (SLOT + GAP)}px, -50%)`,
        }}
      />
    </div>
  );
}
