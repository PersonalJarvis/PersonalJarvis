import { useCallback, useEffect, useId, useRef, useState } from "react";
import { cn } from "@/lib/utils";
import { fill, translate, useT } from "@/i18n";
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
  const uid = `gigi${useId().replace(/:/g, "")}`;
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
  // Below ~80px blur-halos turn into a muddy disc. The mark is then just
  // ink: black body, white eyes and outline, no plate behind it.
  const compact = size < 80;
  const glow = compact ? undefined : `url(#${uid}YGlow)`;
  const soft = compact ? undefined : `url(#${uid}SoftGlow)`;

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
        <svg viewBox="0 0 256 256" xmlns="http://www.w3.org/2000/svg" className="gigi-svg">
          <defs>
            <filter id={`${uid}YGlow`} x="-50%" y="-50%" width="200%" height="200%">
              <feGaussianBlur stdDeviation="3" result="b" />
              <feMerge>
                <feMergeNode in="b" />
                <feMergeNode in="SourceGraphic" />
              </feMerge>
            </filter>
            <filter id={`${uid}SoftGlow`} x="-50%" y="-50%" width="200%" height="200%">
              <feGaussianBlur stdDeviation="6" result="b" />
              <feMerge>
                <feMergeNode in="b" />
                <feMergeNode in="SourceGraphic" />
              </feMerge>
            </filter>
            <radialGradient id={`${uid}Body`} cx="50%" cy="35%">
              <stop offset="0%" stopColor="#232323" />
              <stop offset="55%" stopColor="#0E0E0E" />
              <stop offset="100%" stopColor="#050505" />
            </radialGradient>
            <linearGradient id={`${uid}YAccent`} x1="0%" y1="0%" x2="0%" y2="100%">
              <stop offset="0%" stopColor="hsl(var(--gigi-on-body))" />
              <stop offset="100%" stopColor="hsl(var(--gigi-on-body))" stopOpacity="0.78" />
            </linearGradient>
          </defs>

          {!compact && (
            <path
              className="gigi-halo"
              d="M 58 90 Q 58 36 128 36 Q 198 36 198 90 L 198 208 L 180 186 L 160 208 L 140 186 L 120 208 L 100 186 L 80 208 L 58 186 Z"
              fill="hsl(var(--gigi-trim))"
              opacity="0.18"
              filter={soft}
            />
          )}

          {/* Body */}
          <path
            className="gigi-body"
            d="M 58 90 Q 58 36 128 36 Q 198 36 198 90 L 198 208 L 180 186 L 160 208 L 140 186 L 120 208 L 100 186 L 80 208 L 58 186 Z"
            fill={`url(#${uid}Body)`}
            stroke="hsl(var(--gigi-trim))"
            strokeWidth={compact ? 3.2 : 2.2}
            strokeOpacity="1"
          />

          {/* Scanlines */}
          <g className="gigi-scanlines">
            <rect x="58" y="132" width="140" height="2.4" fill="hsl(var(--gigi-on-body))" opacity="0.55" />
            <rect x="58" y="160" width="140" height="1.4" fill="hsl(var(--gigi-on-body))" opacity="0.3" />
          </g>

          {/* Glitch pixels right */}
          <g className="gigi-glitch-right" fill="hsl(var(--gigi-trim))" filter={glow}>
            <rect x="200" y="104" width="6" height="6" />
            <rect x="208" y="128" width="4" height="4" />
            <rect x="202" y="146" width="9" height="3" />
            <rect x="197" y="168" width="3" height="5" />
            <rect x="206" y="176" width="5" height="3" />
          </g>
          {/* Glitch pixels left */}
          <g className="gigi-glitch-left" fill="hsl(var(--gigi-trim))" opacity="0.7" filter={glow}>
            <rect x="44" y="96" width="6" height="4" />
            <rect x="48" y="124" width="4" height="6" />
            <rect x="40" y="148" width="8" height="3" />
            <rect x="50" y="170" width="3" height="5" />
          </g>

          {/* Chromatic displacement slices */}
          <rect x="64" y="118" width="18" height="10" fill="hsl(var(--gigi-on-body))" opacity="0.32" />
          <rect x="170" y="118" width="18" height="10" fill="hsl(var(--gigi-on-body))" opacity="0.32" />

          {!compact && (
            <>
              <ellipse cx="102" cy="108" rx="13" ry="17" fill="hsl(var(--gigi-on-body))" opacity="0.35" filter={soft} />
              <ellipse cx="154" cy="108" rx="13" ry="17" fill="hsl(var(--gigi-on-body))" opacity="0.35" filter={soft} />
            </>
          )}

          {/* Eye sockets — auto-blinkend */}
          <g className="gigi-eyes">
            <ellipse cx="102" cy="108" rx="10" ry="14" fill={`url(#${uid}YAccent)`} filter={glow} />
            <ellipse cx="154" cy="108" rx="10" ry="14" fill={`url(#${uid}YAccent)`} filter={glow} />
          </g>

          {/* Pupils — driften sanft */}
          <g className="gigi-pupils">
            <ellipse className="gigi-pupil gigi-pupil-left" cx="104" cy="112" rx="4" ry="6" fill="#050505" />
            <ellipse className="gigi-pupil gigi-pupil-right" cx="156" cy="112" rx="4" ry="6" fill="#050505" />
          </g>

          {/* Eye sparkle */}
          <g className="gigi-sparkle">
            <circle cx="106" cy="105" r="2" fill="hsl(var(--gigi-on-body))" />
            <circle cx="158" cy="105" r="2" fill="hsl(var(--gigi-on-body))" />
          </g>

          {/* Mouth — subtile Atmung */}
          <g className="gigi-mouth">
            <ellipse cx="128" cy="146" rx="7" ry="10" fill={`url(#${uid}YAccent)`} filter={glow} />
            <ellipse cx="128" cy="146" rx="3" ry="5" fill="#050505" />
          </g>

          {/* Left arm — waves (pivot point at the shoulder via fill-box) */}
          <path
            className="gigi-arm gigi-arm-left"
            d="M 58 140 Q 40 148 42 162"
            stroke="hsl(var(--gigi-trim))"
            strokeWidth="5.5"
            fill="none"
            strokeLinecap="round"
            filter={glow}
          />
          <path
            className="gigi-arm gigi-arm-right"
            d="M 198 140 Q 216 148 214 162"
            stroke="hsl(var(--gigi-trim))"
            strokeWidth="5.5"
            fill="none"
            strokeLinecap="round"
            filter={glow}
          />
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

// The comment pools below are i18n keys for mascot chat-bubble text spoken to
// the user. They are translated when shown, never at import time, so a UI
// language switch takes effect on the next bubble.
const IDLE_COMMENTS = [
  "mascot_gigi.idle.hm",
  "mascot_gigi.idle.all_quiet",
  "mascot_gigi.idle.still_there",
  "mascot_gigi.idle.whats_up",
  "mascot_gigi.idle.watching",
  "mascot_gigi.idle.focused",
  "mascot_gigi.idle.take_break",
  "mascot_gigi.idle.cool_work",
  "mascot_gigi.idle.bit_boring",
  "mascot_gigi.idle.good_ears",
];

const SECTION_COMMENTS: Partial<Record<SectionId, string[]>> = {
  chats: [
    "mascot_gigi.section.chats_ready",
    "mascot_gigi.section.chats_listening",
    "mascot_gigi.section.chats_say_something",
    "mascot_gigi.section.chats_type_or_talk",
  ],
  agents: ["mascot_gigi.section.agents_colleagues", "mascot_gigi.section.agents_favourite"],
  skills: ["mascot_gigi.section.skills_favourite", "mascot_gigi.section.skills_learn"],
  mcps: ["mascot_gigi.section.mcps_power", "mascot_gigi.section.mcps_add"],
  languages: ["mascot_gigi.section.languages_speak", "mascot_gigi.section.languages_switch"],
  apikeys: ["mascot_gigi.section.apikeys_careful", "mascot_gigi.section.apikeys_no_git"],
  settings: ["mascot_gigi.section.settings_bother", "mascot_gigi.section.settings_tweak"],
};

const VOICE_COMMENTS: Partial<Record<VoiceState, string[]>> = {
  listening: ["mascot_gigi.voice.listening_1", "mascot_gigi.voice.listening_2", "mascot_gigi.voice.listening_3"],
  thinking: ["mascot_gigi.voice.thinking_1", "mascot_gigi.voice.thinking_2", "mascot_gigi.voice.thinking_3"],
  speaking: ["mascot_gigi.voice.speaking_1", "mascot_gigi.voice.speaking_2"],
  error: ["mascot_gigi.voice.error_1", "mascot_gigi.voice.error_2", "mascot_gigi.voice.error_3"],
};

const TIME_COMMENTS = {
  morning: ["mascot_gigi.time.morning_1", "mascot_gigi.time.morning_2"],
  night: ["mascot_gigi.time.night_1", "mascot_gigi.time.night_2", "mascot_gigi.time.night_3"],
};

function pickRandom<T>(arr: readonly T[]): T {
  return arr[Math.floor(Math.random() * arr.length)];
}

function greetByHour(): string | null {
  const h = new Date().getHours();
  if (h >= 5 && h < 11) return translate(pickRandom(TIME_COMMENTS.morning));
  if (h >= 22 || h < 5) return translate(pickRandom(TIME_COMMENTS.night));
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

  // Show a comment with auto-dismiss. Every new show() replaces the previous one.
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
    if (pool) show(translate(pickRandom(pool)));
  }, [activeSection, show]);

  // A voice-state change triggers a comment.
  useEffect(() => {
    if (!mountedRef.current) return;
    if (voiceState === lastVoiceRef.current) return;
    lastVoiceRef.current = voiceState;
    const pool = VOICE_COMMENTS[voiceState];
    if (pool) show(translate(pickRandom(pool)), 2800);
  }, [voiceState, show]);

  // Provider switch.
  useEffect(() => {
    if (!mountedRef.current) return;
    if (brainProvider === lastProviderRef.current) return;
    const prev = lastProviderRef.current;
    lastProviderRef.current = brainProvider;
    if (prev && brainProvider) {
      show(fill(translate("mascot.brain_switched"), { "0": brainProvider }));
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
        if (Math.random() < 0.6) show(translate(pickRandom(IDLE_COMMENTS)));
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
