import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

interface StatsCardProps {
  label: string;
  value: string | number;
  sublabel?: string;
  icon?: ReactNode;
  tone?: "default" | "success" | "warn";
  className?: string;
}

const TONE: Record<NonNullable<StatsCardProps["tone"]>, string> = {
  default: "border-border bg-card/40 text-foreground",
  success: "border-emerald-500/30 bg-emerald-500/5 text-emerald-200",
  warn: "border-amber-500/30 bg-amber-500/5 text-amber-200",
};

/** Compact large number + label for board metrics. */
export function StatsCard({
  label,
  value,
  sublabel,
  icon,
  tone = "default",
  className,
}: StatsCardProps) {
  return (
    <div
      className={cn(
        "flex flex-col gap-2 rounded-xl border px-5 py-4 backdrop-blur",
        TONE[tone],
        className,
      )}
    >
      <div className="flex items-center gap-2 text-[10px] uppercase tracking-wider text-muted-foreground">
        {icon}
        <span>{label}</span>
      </div>
      <div className="font-display text-3xl font-semibold leading-none">
        {value}
      </div>
      {sublabel && (
        <div className="text-xs text-muted-foreground">{sublabel}</div>
      )}
    </div>
  );
}
