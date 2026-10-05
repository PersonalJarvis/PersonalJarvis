import { LayoutGrid, MessagesSquare } from "lucide-react";
import { cn } from "@/lib/utils";
import { useIdeThreadsStore, type IdeLayout } from "@/store/ideThreads";

const CHOICES: { layout: IdeLayout; label: string; Icon: typeof LayoutGrid }[] = [
  { layout: "grid", label: "Terminal grid", Icon: LayoutGrid },
  { layout: "threads", label: "Threads", Icon: MessagesSquare },
];

/** Width of one segment; the sliding thumb moves by exactly this much. */
const SEGMENT_PX = 40;

/**
 * Grid or threads — the two ways the Agentic IDE is laid out. Sits in the
 * middle of the window caption: a rounded track with one thumb that slides
 * under the layout that is on. Two buttons rather than one that cycles, so
 * each says where it leads.
 */
export function IdeLayoutSwitch({ className }: { className?: string }) {
  const layout = useIdeThreadsStore((state) => state.layout);
  const setLayout = useIdeThreadsStore((state) => state.setLayout);
  const index = Math.max(0, CHOICES.findIndex((choice) => choice.layout === layout));
  return <div role="radiogroup" aria-label="IDE layout" data-testid="ide-layout-switch"
    className={cn("relative flex h-7 items-center rounded-full border border-border bg-secondary/70 p-[3px] shadow-rim dark:bg-card", className)}>
    <span aria-hidden
      style={{ width: SEGMENT_PX, transform: `translateX(${index * SEGMENT_PX}px)` }}
      className="absolute inset-y-[3px] left-[3px] rounded-full border border-border-strong/60 bg-background shadow-[0_1px_2px_rgb(var(--scrim-rgb)/0.25)] dark:border-border-strong dark:bg-surface-raised transition-transform duration-200 ease-out motion-reduce:transition-none" />
    {CHOICES.map(({ layout: choice, label, Icon }) => <button key={choice} type="button" role="radio"
      data-testid={`ide-layout-${choice}`} aria-checked={layout === choice} aria-label={label} title={label}
      onClick={() => setLayout(choice)}
      style={{ width: SEGMENT_PX }}
      className={cn(
        "relative z-[1] flex h-full items-center justify-center rounded-full transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        layout === choice ? "text-foreground-strong" : "text-muted-foreground hover:text-foreground",
      )}>
      <Icon aria-hidden className="h-4 w-4" strokeWidth={1.9} />
    </button>)}
  </div>;
}
