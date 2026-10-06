import { Building2, LayoutGrid, MessagesSquare } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  CAPTION_ICON_CLASS, CAPTION_ICON_STROKE, CAPTION_SEGMENT, CAPTION_SEGMENT_OFF, CAPTION_SEGMENT_ON,
  CAPTION_SEGMENT_PX as SEGMENT_PX, CAPTION_THUMB, CAPTION_TRACK,
} from "@/components/layout/captionSwitch";
import { useIdeSidePanelStore, type SidePanelTabId } from "@/store/ideSidePanel";
import { useIdeThreadsStore, type IdeLayout } from "@/store/ideThreads";

/** One face of the Agentic IDE: a layout of the terminals, or the Verse over them. */
export type IdeFace = IdeLayout | "verse";

const CHOICES: { choice: IdeFace; label: string; Icon: typeof LayoutGrid }[] = [
  { choice: "grid", label: "Terminal grid", Icon: LayoutGrid },
  { choice: "threads", label: "Threads", Icon: MessagesSquare },
  { choice: "verse", label: "Jarvis Verse", Icon: Building2 },
];

/** How the side panel stood before the Verse took the whole view. */
interface PanelBefore {
  open: boolean;
  active: SidePanelTabId;
  hadOffice: boolean;
}

// Module-level, not per component: the caption switch and the command palette
// both change faces, and leaving the Verse must restore the panel whichever
// of them entered it.
let before: PanelBefore | null = null;

/** Is the Verse (the office, maximized over the whole view) on right now? */
function verseIsOn(): boolean {
  const panel = useIdeSidePanelStore.getState();
  return panel.open && panel.maximized && panel.active === "office";
}

function enterVerse(): void {
  const panel = useIdeSidePanelStore.getState();
  before = { open: panel.open, active: panel.active, hadOffice: panel.tabs.includes("office") };
  panel.openTab("office");
  useIdeSidePanelStore.getState().setMaximized(true);
}

function leaveVerse(): void {
  const panel = useIdeSidePanelStore.getState();
  panel.setMaximized(false);
  const prior = before;
  before = null;
  // Entered some other way (the panel's own maximize button): just restore.
  if (!prior) return;
  if (!prior.hadOffice) panel.closeTab("office");
  const after = useIdeSidePanelStore.getState();
  if (!prior.open) after.setOpen(false);
  else after.select(prior.active);
}

/** Show one face of the IDE, from the caption switch or anywhere else. */
export function pickIdeFace(choice: IdeFace): void {
  if (choice === "verse") {
    if (!verseIsOn()) enterVerse();
    return;
  }
  if (verseIsOn()) leaveVerse();
  useIdeThreadsStore.getState().setLayout(choice);
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
  const verseOn = useIdeSidePanelStore((state) => state.open && state.maximized && state.active === "office");
  const current: IdeFace = verseOn ? "verse" : layout;
  const index = Math.max(0, CHOICES.findIndex((item) => item.choice === current));

  return <div role="radiogroup" aria-label="IDE layout" data-testid="ide-layout-switch"
    className={cn(CAPTION_TRACK, className)}>
    <span aria-hidden
      style={{ width: SEGMENT_PX, transform: `translateX(${index * SEGMENT_PX}px)` }}
      className={CAPTION_THUMB} />
    {CHOICES.map(({ choice, label, Icon }) => <button key={choice} type="button" role="radio"
      data-testid={`ide-layout-${choice}`} aria-checked={current === choice} aria-label={label} title={label}
      onClick={() => pickIdeFace(choice)}
      style={{ width: SEGMENT_PX }}
      className={cn(CAPTION_SEGMENT, current === choice ? CAPTION_SEGMENT_ON : CAPTION_SEGMENT_OFF)}>
      <Icon aria-hidden className={CAPTION_ICON_CLASS} strokeWidth={CAPTION_ICON_STROKE} />
    </button>)}
  </div>;
}
