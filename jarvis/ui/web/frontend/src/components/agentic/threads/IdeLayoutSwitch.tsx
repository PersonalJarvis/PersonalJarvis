import { LayoutGrid, MessagesSquare } from "lucide-react";
import { cn } from "@/lib/utils";
import { useIdeThreadsStore, type IdeLayout } from "@/store/ideThreads";

const CHOICES: { layout: IdeLayout; label: string; Icon: typeof LayoutGrid }[] = [
  { layout: "grid", label: "Terminal grid", Icon: LayoutGrid },
  { layout: "threads", label: "Threads", Icon: MessagesSquare },
];

/**
 * Grid or threads — the two ways the Agentic IDE is laid out. Two buttons,
 * not one that cycles, so each says where it leads; the one that is on wears
 * the lift surface.
 */
export function IdeLayoutSwitch() {
  const layout = useIdeThreadsStore((state) => state.layout);
  const setLayout = useIdeThreadsStore((state) => state.setLayout);
  return <div role="group" aria-label="IDE layout" className="flex items-center gap-0.5 rounded-md bg-secondary/60 p-0.5">
    {CHOICES.map(({ layout: choice, label, Icon }) => <button key={choice} type="button"
      data-testid={`ide-layout-${choice}`} aria-pressed={layout === choice} aria-label={label} title={label}
      onClick={() => setLayout(choice)}
      className={cn(
        "flex h-6 w-6 items-center justify-center rounded text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        layout === choice && "bg-background text-foreground shadow-rim",
      )}>
      <Icon aria-hidden className="h-3.5 w-3.5" />
    </button>)}
  </div>;
}
