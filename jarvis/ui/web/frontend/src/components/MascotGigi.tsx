import { useCallback, useEffect, useRef, useState } from "react";
import { JARVIS_LOGO_PATH } from "@/components/brand/jarvisLogoPath";
import { cn } from "@/lib/utils";
import { translate, useT } from "@/i18n";
import { useEventStore, type VoiceState, type SectionId } from "@/store/events";

export type MascotAction =
  | "idle"
  | "blink"
  | "wave"
  | "spin"
  | "jump"
  | "shake"
  | "look-left"
  | "look-right"
  | "glitch";

const RANDOM_ACTIONS: MascotAction[] = [
  "blink",
  "blink",
  "wave",
  "wave",
  "jump",
  "shake",
  "look-left",
  "look-right",
  "glitch",
  "spin",
];

const ACTION_DURATION_MS: Record<MascotAction, number> = {
  idle: 0,
  blink: 320,
  wave: 1800,
  spin: 900,
  jump: 700,
  shake: 600,
  "look-left": 900,
  "look-right": 900,
  glitch: 450,
};

type Props = {
  size?: number;
  className?: string;
  reactToVoice?: boolean;
  enableComments?: boolean;
  /**
   * A one-shot move asked for by the parent — the first-run guide greets each
   * step with one. A new `key` replays the move even when the action repeats.
   */
  cue?: { action: MascotAction; key: string | number };
};

export function MascotGigi({
  size = 56,
  className,
  reactToVoice = true,
  enableComments = true,
  cue,
}: Props) {
  const t = useT();
  const [action, setAction] = useState<MascotAction>("idle");
  const voiceState = useEventStore((s) => s.voiceState);
  const transcription = useEventStore((s) => s.transcription);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const comment = useMascotComments(enableComments);
  const listeningText =
    voiceState === "listening" ? transcription.trim() : "";

  useEffect(() => {
    let cancelled = false;

    const scheduleNext = () => {
      if (cancelled) return;
      const delay = 4500 + Math.random() * 9000;
      timerRef.current = setTimeout(() => {
        if (cancelled) return;
        const next = RANDOM_ACTIONS[Math.floor(Math.random() * RANDOM_ACTIONS.length)];
        setAction(next);
        const back = setTimeout(() => {
          if (cancelled) return;
          setAction("idle");
          scheduleNext();
        }, ACTION_DURATION_MS[next]);
        timerRef.current = back;
      }, delay);
    };

    scheduleNext();
    return () => {
      cancelled = true;
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  const cueAction = cue?.action;
  const cueKey = cue?.key;
  useEffect(() => {
    if (!cueAction || cueAction === "idle") return;
    setAction(cueAction);
    const back = setTimeout(() => setAction("idle"), ACTION_DURATION_MS[cueAction]);
    return () => clearTimeout(back);
  }, [cueAction, cueKey]);

  const voiceClass = reactToVoice ? voiceClassFor(voiceState) : "";
  // Small marks hold still: a floating 20px figure only reads as jitter.
  const compact = size < 80;

  return (
    <div
      className={cn("gigi-container", compact && "gigi-compact")}
      style={{ width: size, height: size }}
    >
      <div
        className={cn("gigi-root", `gigi-${action}`, voiceClass, className)}
        aria-label={t("mascot_gigi.aria_label")}
        title="Gigi"
      >
        {/* The Personal Jarvis mark: ink on paper, paper on charcoal. */}
        <svg viewBox="0 0 256 256" xmlns="http://www.w3.org/2000/svg" className="gigi-svg">
          <path className="gigi-body" d={JARVIS_LOGO_PATH} fillRule="evenodd" fill="hsl(var(--gigi-trim))" />
        </svg>
      </div>

      {enableComments && listeningText && (
        <GigiBubble text={listeningText} variant="listening" />
      )}
      {enableComments && voiceState !== "listening" && comment && (
        <GigiBubble text={comment} variant="comment" />
      )}
    </div>
  );
}

function voiceClassFor(state: VoiceState): string {
  switch (state) {
    case "listening":
      return "gigi-voice-listening";
    case "thinking":
      return "gigi-voice-thinking";
    case "speaking":
      return "gigi-voice-speaking";
    case "error":
      return "gigi-voice-error";
    default:
      return "";
  }
}

// ============================================================================
// Comment-Bubble + Kontext-Hook
// ============================================================================

function GigiBubble({
  text,
  variant,
}: {
  text: string;
  variant: "comment" | "listening";
}) {
  return (
    <div
      className={cn(
        "gigi-bubble",
        variant === "listening" && "gigi-bubble-listening",
      )}
      role="status"
    >
      <span
        className={cn(
          "gigi-bubble-text",
          variant === "listening" && "gigi-bubble-text-listening",
        )}
      >
        {text}
      </span>
    </div>
  );
}

const IDLE_COMMENTS = [
  "hm …",
  "alles ruhig.",
  "noch da?",
  "was geht so?",
  "ich beobachte dich.",
  "konzentriert, was?",
  "mach doch mal Pause.",
  "arbeitest du heute was Cooles?",
  "bisschen langweilig grad.",
  "ich hab gute Ohren. falls du was brauchst.",
];

// The comment pools below are mascot chat-bubble text spoken to the user —
// runtime product-surface output, not developer-facing strings.
const SECTION_COMMENTS: Partial<Record<SectionId, string[]>> = {
  chats: ["bereit wenn du bist.", "ich höre.", "sag mal was.", "schreib oder rede — egal."],  // i18n-allow: mascot chat-bubble output shown to the user
  agents: ["die Agents sind meine Kollegen.", "wer ist dein Liebling?"],  // i18n-allow: mascot chat-bubble output shown to the user
  skills: ["Skills sind mein Lieblings-Feature.", "was sollen wir lernen?"],  // i18n-allow: mascot chat-bubble output shown to the user
  mcps: ["mehr MCPs = mehr Power.", "was sollen wir dazuholen?"],  // i18n-allow: mascot chat-bubble output shown to the user
  languages: ["ich spreche DE und EN.", "Sprachwechsel? Einfach sagen."],  // i18n-allow: mascot chat-bubble output shown to the user
  apikeys: ["pass auf die Keys auf.", "nicht in Git committen!"],  // i18n-allow: mascot chat-bubble output shown to the user
  settings: ["was stört dich?", "tweaken wir was?"],  // i18n-allow: mascot chat-bubble output shown to the user
};

const VOICE_COMMENTS: Partial<Record<VoiceState, string[]>> = {
  listening: ["ich höre!", "raus damit.", "ja?"],  // i18n-allow: mascot chat-bubble output shown to the user
  thinking: ["mal schauen …", "Moment.", "hmm …"],
  speaking: ["jetzt rede ich.", "kurz mal zuhören."],  // i18n-allow: mascot chat-bubble output shown to the user
  error: ["oha.", "ups.", "das war nicht ich!"],  // i18n-allow: mascot chat-bubble output shown to the user
};

const TIME_COMMENTS = {
  morning: ["guten Morgen!", "auf, auf."],  // i18n-allow: mascot chat-bubble output shown to the user
  night: ["noch wach?", "es ist spät.", "schlaf genug gekriegt?"],  // i18n-allow: mascot chat-bubble output shown to the user
};

function pickRandom<T>(arr: readonly T[]): T {
  return arr[Math.floor(Math.random() * arr.length)];
}

function greetByHour(): string | null {
  const h = new Date().getHours();
  if (h >= 5 && h < 11) return pickRandom(TIME_COMMENTS.morning);
  if (h >= 22 || h < 5) return pickRandom(TIME_COMMENTS.night);
  return null;
}

function useMascotComments(enabled: boolean): string | null {
  const [comment, setComment] = useState<string | null>(null);

  const activeSection = useEventStore((s) => s.activeSection);
  const voiceState = useEventStore((s) => s.voiceState);
  const brainProvider = useEventStore((s) => s.brainProvider);
  const connected = useEventStore((s) => s.connected);

  const lastSectionRef = useRef(activeSection);
  const lastVoiceRef = useRef(voiceState);
  const lastProviderRef = useRef(brainProvider);
  const lastConnectedRef = useRef(connected);
  const mountedRef = useRef(false);
  const dismissTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const idleTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Show a comment with auto-dismiss. Jeder neue show() ersetzt den alten.
  const show = useCallback((text: string, duration = 4200) => {
    if (!enabled) return;
    setComment(text);
    if (dismissTimerRef.current) clearTimeout(dismissTimerRef.current);
    dismissTimerRef.current = setTimeout(() => setComment(null), duration);
  }, [enabled]);

  // A section change triggers a comment.
  useEffect(() => {
    if (!mountedRef.current) return;
    if (activeSection === lastSectionRef.current) return;
    lastSectionRef.current = activeSection;
    const pool = SECTION_COMMENTS[activeSection];
    if (pool) show(pickRandom(pool));
  }, [activeSection, show]);

  // A voice-state change triggers a comment.
  useEffect(() => {
    if (!mountedRef.current) return;
    if (voiceState === lastVoiceRef.current) return;
    lastVoiceRef.current = voiceState;
    const pool = VOICE_COMMENTS[voiceState];
    if (pool) show(pickRandom(pool), 2800);
  }, [voiceState, show]);

  // Provider-Wechsel.
  useEffect(() => {
    if (!mountedRef.current) return;
    if (brainProvider === lastProviderRef.current) return;
    const prev = lastProviderRef.current;
    lastProviderRef.current = brainProvider;
    if (prev && brainProvider) {
      show(`wechsel auf ${brainProvider}. ok!`);
    }
  }, [brainProvider, show]);

  // Connection lost/restored.
  useEffect(() => {
    if (!mountedRef.current) return;
    if (connected === lastConnectedRef.current) return;
    lastConnectedRef.current = connected;
    show(
      connected
        ? translate("mascot_gigi.back_online")
        : translate("mascot_gigi.connection_lost"),
    );
  }, [connected, show]);

  // Mount: greet based on time of day.
  useEffect(() => {
    mountedRef.current = true;
    const greet = greetByHour();
    if (greet) {
      const t = setTimeout(() => show(greet), 2500);
      return () => {
        mountedRef.current = false;
        clearTimeout(t);
        if (dismissTimerRef.current) clearTimeout(dismissTimerRef.current);
        if (idleTimerRef.current) clearTimeout(idleTimerRef.current);
      };
    }
    return () => {
      mountedRef.current = false;
      if (dismissTimerRef.current) clearTimeout(dismissTimerRef.current);
      if (idleTimerRef.current) clearTimeout(idleTimerRef.current);
    };
  }, [show]);

  // Random idle chatter: every 25–60s with a 60% probability.
  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    const scheduleNext = () => {
      if (cancelled) return;
      const delay = 25000 + Math.random() * 35000;
      idleTimerRef.current = setTimeout(() => {
        if (cancelled) return;
        if (Math.random() < 0.6) show(pickRandom(IDLE_COMMENTS));
        scheduleNext();
      }, delay);
    };
    scheduleNext();
    return () => {
      cancelled = true;
      if (idleTimerRef.current) clearTimeout(idleTimerRef.current);
    };
  }, [enabled, show]);

  return comment;
}
