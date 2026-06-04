import { cn } from "@/lib/utils";
import type { DocDiataxis } from "@/hooks/useDocs";

const DIATAXIS_VARIANTS: Record<DocDiataxis, string> = {
  tutorial:
    "bg-violet-500/15 text-violet-300 border-violet-500/30",
  howto:
    "bg-blue-500/15 text-blue-300 border-blue-500/30",
  reference:
    "bg-slate-500/15 text-slate-300 border-slate-500/30",
  explanation:
    "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  troubleshooting:
    "bg-amber-500/15 text-amber-300 border-amber-500/30",
  adr:
    "bg-rose-500/15 text-rose-300 border-rose-500/30",
  unclassified:
    "bg-neutral-500/15 text-neutral-400 border-neutral-500/30",
};

const DIATAXIS_LABEL: Record<DocDiataxis, string> = {
  tutorial: "Tutorial",
  howto: "How-To",
  reference: "Reference",
  explanation: "Concept",
  troubleshooting: "Trouble",
  adr: "ADR",
  unclassified: "Legacy",
};

interface Props {
  diataxis: DocDiataxis;
  className?: string;
}

export function DocTypeBadge({ diataxis, className }: Props) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-md border px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide",
        DIATAXIS_VARIANTS[diataxis],
        className,
      )}
    >
      {DIATAXIS_LABEL[diataxis]}
    </span>
  );
}
