import { useRef } from "react";
import { Building2, LayoutGrid, MessagesSquare } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  CAPTION_ICON_CLASS, CAPTION_ICON_STROKE, CAPTION_SEGMENT, CAPTION_SEGMENT_OFF, CAPTION_SEGMENT_ON,
  CAPTION_SEGMENT_PX as SEGMENT_PX, CAPTION_THUMB, CAPTION_TRACK,
} from "@/components/layout/captionSwitch";
import { useIdeSidePanelStore, type SidePanelTabId } from "@/store/ideSidePanel";
import { useIdeThreadsStore, type IdeLayout } from "@/store/ideThreads";

type Choice = IdeLayout | "verse";

const CHOICES: { choice: Choice; label: string; Icon: typeof LayoutGrid }[] = [
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
    className={cn(CAPTION_TRACK, className)}>
    <span aria-hidden
      style={{ width: SEGMENT_PX, transform: `translateX(${index * SEGMENT_PX}px)` }}
      className={CAPTION_THUMB} />
    {CHOICES.map(({ choice, label, Icon }) => <button key={choice} type="button" role="radio"
      data-testid={`ide-layout-${choice}`} aria-checked={current === choice} aria-label={label} title={label}
      onClick={() => pick(choice)}
      style={{ width: SEGMENT_PX }}
      className={cn(CAPTION_SEGMENT, current === choice ? CAPTION_SEGMENT_ON : CAPTION_SEGMENT_OFF)}>
      <Icon aria-hidden className={CAPTION_ICON_CLASS} strokeWidth={CAPTION_ICON_STROKE} />
    </button>)}
  </div>;
}
