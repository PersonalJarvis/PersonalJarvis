import { motion, useReducedMotion } from "framer-motion";
import { Check, ChevronDown, ChevronUp, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { FOCUS_RING } from "@/components/agentic/controls";
import { fill, useLocaleChunk, useT } from "@/i18n";
import { sendChatMessage } from "@/lib/chat";
import type { PetState } from "@/lib/petStates";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";
import { useHomeStore } from "@/store/home";
import { GuidePetFigure, PetSays } from "../pet/GuidePet";
import { EASE_OUT, QuietAction } from "../ui";
import {
  nextOpenQuest,
  questDone,
  QUESTS,
  writeFirstSteps,
  type FirstStepsState,
  type Quest,
  type QuestId,
} from "./firstSteps";

/** How the pet looks while it waits for each quest. */
const PET: Record<QuestId, PetState> = {
  wake: "listening",
  ask: "idle",
  tool: "thinking",
  screen: "thinking",
  memory: "thinking",
  artifact: "thinking",
  agent: "thinking",
  team: "success",
  plugin: "thinking",
  ide: "thinking",
};

/** How often the guide re-checks the live state for the current quest. */
const CHECK_MS = 700;

/**
 * The "first steps" guide: the user's pet in the corner of the real app,
 * speaking in a bubble — never over the app.
 *
 * It walks ten real things to do with the assistant — wake it, ask, let it
 * use a tool, look at the screen, remember something, make an artifact,
 * hand a job to an agent, meet the agents, connect an app, open the coding
 * workspace. Each quest says one concrete example and waits until the app
 * reports it really happened; then the pet explains what went on behind the
 * scenes and where to see the result. The app stays fully usable the whole
 * time: the pet can be tucked into a pill, a quest skipped, or the guide
 * closed.
 */
export function FirstStepsGuide({
  initial,
  onClose,
}: {
  initial: FirstStepsState;
  onClose: () => void;
}) {
  const t = useT();
  const ready = useLocaleChunk("onboarding");
  const reduced = useReducedMotion() ?? false;
  const [state, setState] = useState<FirstStepsState>(initial);
  const [sent, setSent] = useState(false);
  // When the current quest was armed: only what happens after counts.
  const armedAt = useRef(Date.now());

  const quest = QUESTS.find((q) => q.id === state.current) ?? QUESTS[0];
  const index = QUESTS.indexOf(quest);
  const isDone = state.done.includes(quest.id);
  const finished = state.status === "finished";

  const update = useCallback((next: FirstStepsState) => {
    setState(next);
    writeFirstSteps(next);
  }, []);

  const goTo = useCallback(
    (id: QuestId) => {
      armedAt.current = Date.now();
      setSent(false);
      update({ ...state, current: id, collapsed: false });
    },
    [state, update],
  );

  // Watch the live app for the current quest.
  useEffect(() => {
    if (isDone || finished) return;
    const check = () => {
      const s = useEventStore.getState();
      const hit = questDone(quest, {
        events: s.events,
        messages: s.messages,
        voiceState: s.voiceState,
        activeSection: s.activeSection,
        since: armedAt.current,
      });
      if (hit) {
        update({
          ...state,
          done: state.done.includes(quest.id) ? state.done : [...state.done, quest.id],
          skipped: state.skipped.filter((q) => q !== quest.id),
        });
      }
    };
    check();
    const timer = window.setInterval(check, CHECK_MS);
    return () => window.clearInterval(timer);
  }, [quest, isDone, finished, state, update]);

  const advance = useCallback(
    (skipping: boolean) => {
      const base = skipping
        ? { ...state, skipped: state.skipped.includes(quest.id) ? state.skipped : [...state.skipped, quest.id] }
        : state;
      const next = nextOpenQuest(base, quest.id);
      if (next) {
        armedAt.current = Date.now();
        setSent(false);
        update({ ...base, current: next });
      } else {
        update({ ...base, status: "finished" });
      }
    },
    [state, quest.id, update],
  );

  const tryIt = useCallback(
    async (q: Quest) => {
      const nav = useEventStore.getState();
      if (q.action.kind === "open") {
        if (nav.activeSection !== q.action.section) nav.setActiveSection(q.action.section);
        return;
      }
      // Voice and messages both happen on the front page's chat.
      if (nav.activeSection !== "chats") nav.setActiveSection("chats");
      if (q.action.kind === "voice") {
        if (useHomeStore.getState().surface !== "voice") useHomeStore.getState().setSurface("voice");
        return;
      }
      if (useHomeStore.getState().surface !== "chat") useHomeStore.getState().setSurface("chat");
      // The user pressed "send it" for exactly this example: one message, once.
      const ok = await sendChatMessage(t(`first_steps.quests.${q.id}.prompt`));
      setSent(ok);
    },
    [t],
  );

  const show = (section: NonNullable<Quest["showSection"]>) => {
    const nav = useEventStore.getState();
    if (nav.activeSection !== section) nav.setActiveSection(section);
  };

  const close = () => {
    update({ ...state, status: finished ? "finished" : "dismissed" });
    onClose();
  };

  if (!ready) return null;

  const doneCount = state.done.length;
  const progress = fill(t("first_steps.progress"), { done: doneCount, total: QUESTS.length });

  if (state.collapsed && !finished) {
    return (
      <div className="fixed bottom-4 right-4 z-[100]" data-testid="first-steps-pill">
        <button
          type="button"
          onClick={() => update({ ...state, collapsed: false })}
          className={cn(
            "flex items-center gap-2 rounded-full border border-border bg-popover py-1 pl-1 pr-3 text-sm text-popover-foreground shadow-float transition-colors hover:bg-secondary",
            FOCUS_RING,
          )}
        >
          <GuidePetFigure state="idle" px={48} />
          <span className="font-medium">{t("first_steps.title")}</span>
          <span className="text-xs text-muted-foreground">{progress}</span>
          <ChevronUp aria-hidden className="h-3.5 w-3.5 text-muted-foreground" />
        </button>
      </div>
    );
  }

  const line = finished
    ? fill(t("first_steps.finished.body"), { done: doneCount, total: QUESTS.length })
    : isDone
      ? `${t(`first_steps.quests.${quest.id}.explain`)} ${t(`first_steps.quests.${quest.id}.how`)}`
      : t(`first_steps.quests.${quest.id}.ask`);

  const heading = (
    <div className="mb-1.5 flex items-center gap-2">
      <span className="inline-flex items-center gap-1.5 text-sm font-semibold text-foreground" id="first-steps-title">
        {!finished && isDone && <Check aria-hidden className="h-4 w-4 text-success" />}
        {finished ? t("first_steps.finished.title") : t(`first_steps.quests.${quest.id}.title`)}
      </span>
      <span className="ml-auto text-xs text-muted-foreground">{progress}</span>
      {!finished && (
        <IconAction label={t("first_steps.collapse")} onClick={() => update({ ...state, collapsed: true })}>
          <ChevronDown aria-hidden className="h-4 w-4" />
        </IconAction>
      )}
      <IconAction label={t("first_steps.close")} onClick={close} testId="first-steps-close">
        <X aria-hidden className="h-4 w-4" />
      </IconAction>
    </div>
  );

  return (
    <motion.section
      initial={reduced ? false : { opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.28, ease: EASE_OUT }}
      className="fixed bottom-4 right-4 z-[100] w-[min(460px,calc(100vw-32px))]"
      aria-labelledby="first-steps-title"
      data-testid="first-steps"
      data-quest={finished ? "finished" : quest.id}
      data-state={finished ? "finished" : isDone ? "done" : "open"}
    >
      <PetSays
        key={`${finished ? "finished" : quest.id}-${isDone}`}
        text={line}
        state={finished || isDone ? "success" : PET[quest.id]}
        heading={heading}
        px={96}
        testId="first-steps-say"
      >
        {!finished && !isDone && quest.action.kind === "send" && (
          <p className="mt-2 rounded-lg bg-secondary/70 px-3 py-2 text-sm text-foreground" data-testid="first-steps-prompt">
            “{t(`first_steps.quests.${quest.id}.prompt`)}”
          </p>
        )}
        {!finished && !isDone && sent && (
          <p className="mt-2 text-xs text-muted-foreground" role="status">
            {t("first_steps.waiting")}
          </p>
        )}
        <div className="mt-3 flex items-center gap-3">
          {finished ? (
            <SmallAction onClick={close} testId="first-steps-finish">
              {t("first_steps.finished.close")}
            </SmallAction>
          ) : isDone ? (
            <SmallAction onClick={() => advance(false)} testId="first-steps-next">
              {nextOpenQuest(state, quest.id) ? t("first_steps.next") : t("first_steps.finish")}
            </SmallAction>
          ) : (
            <SmallAction onClick={() => void tryIt(quest)} disabled={sent} testId="first-steps-try">
              {t(`first_steps.try.${quest.action.kind}`)}
            </SmallAction>
          )}
          {!finished && isDone && quest.showSection && (
            <QuietAction onClick={() => show(quest.showSection!)} className="text-xs" testId="first-steps-show">
              {t(`first_steps.show.${quest.id}`)}
            </QuietAction>
          )}
          {!finished && !isDone && (
            <QuietAction onClick={() => advance(true)} className="ml-auto text-xs" testId="first-steps-skip">
              {t("first_steps.skip")}
            </QuietAction>
          )}
        </div>
        {/* One mark per quest: filled when done. Click to jump. */}
        <div className="mt-3 flex gap-1" role="tablist" aria-label={t("first_steps.title")}>
          {QUESTS.map((q, i) => (
            <button
              key={q.id}
              type="button"
              role="tab"
              aria-selected={!finished && i === index}
              aria-label={t(`first_steps.quests.${q.id}.title`)}
              title={t(`first_steps.quests.${q.id}.title`)}
              onClick={() => goTo(q.id)}
              className={cn(
                "h-1 flex-1 rounded-full transition-colors",
                state.done.includes(q.id)
                  ? "bg-accent"
                  : state.skipped.includes(q.id)
                    ? "bg-foreground/25"
                    : "bg-border-strong",
                !finished && i === index && "outline outline-2 outline-offset-1 outline-accent/50",
                FOCUS_RING,
              )}
            />
          ))}
        </div>
      </PetSays>
    </motion.section>
  );
}

function SmallAction({
  children,
  onClick,
  disabled,
  testId,
}: {
  children: ReactNode;
  onClick: () => void;
  disabled?: boolean;
  testId?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      data-testid={testId}
      className={cn(
        "h-8 rounded-lg bg-accent px-3.5 text-sm font-medium text-accent-foreground transition-opacity hover:opacity-90 disabled:opacity-50",
        FOCUS_RING,
      )}
    >
      {children}
    </button>
  );
}

function IconAction({
  children,
  label,
  onClick,
  testId,
}: {
  children: ReactNode;
  label: string;
  onClick: () => void;
  testId?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      title={label}
      data-testid={testId}
      className={cn("rounded-md p-0.5 text-muted-foreground hover:bg-secondary hover:text-foreground", FOCUS_RING)}
    >
      {children}
    </button>
  );
}
