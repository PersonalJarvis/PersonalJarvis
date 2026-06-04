import { Brain, Flame, Rocket } from "lucide-react";
import type { Reaction } from "@/hooks/useFederation";
import { cn } from "@/lib/utils";

interface ReactionBarProps {
  counts: Record<string, number> | null;
  hasReactions: boolean;
  onReact: (r: Reaction) => void;
  disabled?: boolean;
}

const ICONS: Record<Reaction, React.ComponentType<{ className?: string }>> = {
  rocket: Rocket,
  brain: Brain,
  fire: Flame,
};

const LABELS: Record<Reaction, string> = {
  rocket: "rocket",
  brain: "brain",
  fire: "fire",
};

export function ReactionBar({ counts, hasReactions, onReact, disabled }: ReactionBarProps) {
  const isOwner = counts !== null;
  return (
    <div className="flex items-center gap-1.5">
      {(Object.keys(ICONS) as Reaction[]).map((r) => {
        const Icon = ICONS[r];
        const cnt = isOwner ? (counts![r] ?? 0) : null;
        return (
          <button
            key={r}
            type="button"
            disabled={disabled}
            onClick={() => onReact(r)}
            title={isOwner ? `${LABELS[r]} (${cnt})` : "Click to react"}
            className={cn(
              "inline-flex items-center gap-1 rounded-md border border-border/70 bg-background/40 px-2 py-1 text-[11px] transition-colors",
              "hover:border-primary/40 hover:bg-background/60",
              disabled && "cursor-not-allowed opacity-50",
            )}
          >
            <Icon className="h-3 w-3" />
            {isOwner ? <span className="font-mono">{cnt}</span> : null}
          </button>
        );
      })}
      {!isOwner && hasReactions && (
        <span className="text-[10px] text-muted-foreground" title="Others reacted">
          &middot;
        </span>
      )}
    </div>
  );
}
