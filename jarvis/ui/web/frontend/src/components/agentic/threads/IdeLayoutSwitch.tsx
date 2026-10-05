import { useRef } from "react";
import { Building2, LayoutGrid, MessagesSquare } from "lucide-react";
import { cn } from "@/lib/utils";
import { useIdeSidePanelStore, type SidePanelTabId } from "@/store/ideSidePanel";
import { useIdeThreadsStore, type IdeLayout } from "@/store/ideThreads";

type Choice = IdeLayout | "verse";

const CHOICES: { choice: Choice; label: string; Icon: typeof LayoutGrid }[] = [
  { choice: "grid", label: "Terminal grid", Icon: LayoutGrid },
  { choice: "threads", label: "Threads", Icon: MessagesSquare },
  { choice: "verse", label: "Jarvis Verse", Icon: Building2 },
];

/** Width of one segment; the sliding thumb moves by exactly this much. */
const SEGMENT_PX = 40;

/** How the side panel stood before the Verse took the whole view. */
interface PanelBefore {
  open: boolean;
  active: SidePanelTabId;
  hadOffice: boolean;
}

/**
 * Grid, threads or the Jarvis Verse — the three faces of the Agentic IDE. Sits
 * in the middle of the window caption: a rounded track with one thumb that
 * slides under the face that is on. Separate buttons rather than one that
 * cycles, so each says where it leads.
 *
 * The Verse is the side panel's office tab, maximized over the whole view: the
 * terminals keep running behind it, and leaving it puts the panel back the way
 * it was.
 */
export function IdeLayoutSwitch({ className }: { className?: string }) {
  const layout = useIdeThreadsStore((state) => state.layout);
  const setLayout = useIdeThreadsStore((state) => state.setLayout);
  const verseOn = useIdeSidePanelStore((state) => state.open && state.maximized && state.active === "office");
  const before = useRef<PanelBefore | null>(null);
  const current: Choice = verseOn ? "verse" : layout;
  const index = Math.max(0, CHOICES.findIndex((item) => item.choice === current));

  const enterVerse = () => {
    const panel = useIdeSidePanelStore.getState();
    before.current = { open: panel.open, active: panel.active, hadOffice: panel.tabs.includes("office") };
    panel.openTab("office");
    useIdeSidePanelStore.getState().setMaximized(true);
  };

  const leaveVerse = () => {
    const panel = useIdeSidePanelStore.getState();
    panel.setMaximized(false);
    const prior = before.current;
    before.current = null;
    // Entered some other way (the panel's own maximize button): just restore.
    if (!prior) return;
    if (!prior.hadOffice) panel.closeTab("office");
    const after = useIdeSidePanelStore.getState();
    if (!prior.open) after.setOpen(false);
    else after.select(prior.active);
  };

  const pick = (choice: Choice) => {
    if (choice === "verse") {
      if (!verseOn) enterVerse();
      return;
    }
    if (verseOn) leaveVerse();
    setLayout(choice);
  };

  return <div role="radiogroup" aria-label="IDE layout" data-testid="ide-layout-switch"
    className={cn("relative flex h-7 items-center rounded-full border border-border bg-secondary/70 p-[3px] shadow-rim dark:bg-card", className)}>
    <span aria-hidden
      style={{ width: SEGMENT_PX, transform: `translateX(${index * SEGMENT_PX}px)` }}
      className="absolute inset-y-[3px] left-[3px] rounded-full border border-border-strong/60 bg-background shadow-[0_1px_2px_rgb(var(--scrim-rgb)/0.25)] dark:border-border-strong dark:bg-surface-raised transition-transform duration-200 ease-out motion-reduce:transition-none" />
    {CHOICES.map(({ choice, label, Icon }) => <button key={choice} type="button" role="radio"
      data-testid={`ide-layout-${choice}`} aria-checked={current === choice} aria-label={label} title={label}
      onClick={() => pick(choice)}
      style={{ width: SEGMENT_PX }}
      className={cn(
        "relative z-[1] flex h-full items-center justify-center rounded-full transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        current === choice ? "text-foreground-strong" : "text-muted-foreground hover:text-foreground",
      )}>
      <Icon aria-hidden className="h-4 w-4" strokeWidth={1.9} />
    </button>)}
  </div>;
}
