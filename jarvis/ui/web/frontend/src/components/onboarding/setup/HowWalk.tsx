/**
 * "How it works": the first-run explanation of what the assistant is, told
 * by the user's pet while it walks the REAL app.
 *
 * New users went through setup without understanding the product. Before
 * anything is set up, the window dims and the pet moves from place to place
 * in the actual interface — the composer, the sidebar entries for tools,
 * agents, the coding workspace and Artifacts — and says in a speech bubble
 * how each part connects to the one assistant the user talks to. Lines with
 * no single place (memory, safety, the summary) are said from the middle.
 *
 * Purely explanatory: it only navigates, never presses or writes anything.
 */
import { ArrowLeft } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { FOCUS_RING } from "@/components/agentic/controls";
import { fill, useT } from "@/i18n";
import type { PetState } from "@/lib/petStates";
import { cn } from "@/lib/utils";
import type { SectionId } from "@/store/events";
import { useEventStore } from "@/store/events";
import { useHomeStore } from "@/store/home";
import { PetSays, useGuidePet } from "../pet/GuidePet";
import { Spotlight } from "../tour/Spotlight";
import type { TourPlacement } from "../tour/tourSteps";
import { QuietAction } from "../ui";
import { useAnchorRect } from "./useAnchorRect";

export interface HowBeat {
  /** Also the i18n key: `first_run.how.beats.<id>`. */
  id: string;
  /** The real element the pet stands next to; omitted = said from the middle. */
  anchor?: string;
  /** Where the app goes first, so the anchor is on screen. */
  section?: SectionId;
  placement: TourPlacement;
  /** How the pet looks once the line is said. */
  pet: PetState;
}

export const HOW_BEATS: readonly HowBeat[] = [
  { id: "hello", placement: "inside", pet: "success" },
  { id: "talk", anchor: "chat-composer", section: "chats", placement: "above", pet: "listening" },
  { id: "brain", anchor: "settings", placement: "right", pet: "thinking" },
  { id: "tools", anchor: "nav-plugins", placement: "right", pet: "working" },
  { id: "agents", anchor: "nav-agents", placement: "right", pet: "working" },
  { id: "ide", anchor: "nav-agentic-ide", placement: "right", pet: "searching" },
  { id: "results", anchor: "nav-visualization", placement: "right", pet: "success" },
  { id: "memory", placement: "inside", pet: "thinking" },
  { id: "safety", placement: "inside", pet: "idle" },
  { id: "done", placement: "inside", pet: "success" },
];

const BUBBLE_W = 500;

export function HowWalk({ onDone }: { onDone: () => void }) {
  const t = useT();
  const pet = useGuidePet();
  const [index, setIndex] = useState(0);
  const beat = HOW_BEATS[index];
  const last = index === HOW_BEATS.length - 1;

  // Bring the beat's place on screen first: the composer lives on the chat.
  useEffect(() => {
    if (!beat.section) return;
    const nav = useEventStore.getState();
    if (nav.activeSection !== beat.section) nav.setActiveSection(beat.section);
    if (beat.anchor === "chat-composer" && useHomeStore.getState().surface !== "chat") {
      useHomeStore.getState().setSurface("chat");
    }
  }, [beat]);

  const rect = useAnchorRect(beat.anchor, false, beat.id);

  const next = useCallback(() => {
    if (last) onDone();
    else setIndex((i) => i + 1);
  }, [last, onDone]);

  // Arrow keys move through the walk.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "ArrowRight") next();
      else if (event.key === "ArrowLeft") setIndex((i) => Math.max(0, i - 1));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [next]);

  const text = fill(t(`first_run.how.beats.${beat.id}`), { pet: pet?.name ?? "Gigi" });

  return (
    <Spotlight rect={rect} placement={beat.placement} blocking cardWidth={BUBBLE_W}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label={t("first_run.how.title")}
        data-testid="setup-card"
        data-step="how"
        data-beat={beat.id}
      >
        <PetSays key={beat.id} text={text} state={beat.pet} testId="how-say">
          <div className="mt-3 flex items-center gap-3">
            <button
              type="button"
              onClick={next}
              data-testid="how-next"
              className={cn(
                "h-8 rounded-lg bg-accent px-3.5 text-sm font-medium text-accent-foreground transition-opacity hover:opacity-90",
                FOCUS_RING,
              )}
            >
              {last ? t("first_run.how.finish") : t("first_run.how.next")}
            </button>
            {index > 0 && (
              <QuietAction
                onClick={() => setIndex((i) => i - 1)}
                className="inline-flex items-center gap-1 text-xs"
                testId="how-prev"
              >
                <ArrowLeft aria-hidden className="h-3 w-3" />
                {t("first_run.how.prev")}
              </QuietAction>
            )}
            <span className="ml-auto text-xs text-muted-foreground">
              {fill(t("first_run.how.beat_of"), { current: index + 1, total: HOW_BEATS.length })}
            </span>
            {!last && (
              <QuietAction onClick={onDone} className="text-xs" testId="how-skip">
                {t("first_run.how.skip")}
              </QuietAction>
            )}
          </div>
        </PetSays>
      </div>
    </Spotlight>
  );
}
