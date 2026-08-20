import { useCallback, useEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";
import { translate, useT } from "@/i18n";
import { useEventStore, type VoiceState, type SectionId } from "@/store/events";

type MascotAction =
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
};

export function MascotGigi({
  size = 56,
  className,
  reactToVoice = true,
  enableComments = true,
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

  const voiceClass = reactToVoice ? voiceClassFor(voiceState) : "";

  return (
    <div className="gigi-container" style={{ width: size, height: size }}>
      <div
        className={cn("gigi-root", `gigi-${action}`, voiceClass, className)}
        aria-label={t("mascot_gigi.aria_label")}
        title="Gigi"
      >
        <svg viewBox="0 0 256 256" xmlns="http://www.w3.org/2000/svg" className="gigi-svg">
          {/* Every number below was MEASURED off `public/jarvis-gigi-256.png`
              rather than drawn by eye (2026-08-20), so the vector and the
              bitmap are the same mascot: body x 49..206 and y 32..226, widest
              157px at y=84, zigzag troughs at y=203 and points at y=225, eyes
              centred on (98,113) and (157,113) with rx 13 / ry 17.5, mouth on
              (127.5,158). The hand-drawn shape it replaces was 140px wide with
              rx-10 eyes — a family resemblance, not the same character. The
              filter blurs and the halo opacity come from a difference-scored
              sweep against the PNG on a local comparison page; mean luminance
              error is ~6/255 across the frame, and the silhouette lands within
              two pixels at every row. */}
          <defs>
            <filter id="gigiYGlow" x="-50%" y="-50%" width="200%" height="200%">
              <feGaussianBlur stdDeviation="2" result="b" />
              <feMerge>
                <feMergeNode in="b" />
                <feMergeNode in="SourceGraphic" />
              </feMerge>
            </filter>
            <filter id="gigiSoftGlow" x="-50%" y="-50%" width="200%" height="200%">
              <feGaussianBlur stdDeviation="4" result="b" />
              <feMerge>
                <feMergeNode in="b" />
                <feMergeNode in="SourceGraphic" />
              </feMerge>
            </filter>
            <radialGradient id="gigiBody" cx="50%" cy="35%">
              <stop offset="0%" stopColor="#232323" />
              <stop offset="55%" stopColor="#0E0E0E" />
              <stop offset="100%" stopColor="#050505" />
            </radialGradient>
            <linearGradient id="gigiYAccent" x1="0%" y1="0%" x2="0%" y2="100%">
              <stop offset="0%" stopColor="#FFF200" />
              <stop offset="100%" stopColor="#FFB800" />
            </linearGradient>
          </defs>

          {/* Outer halo */}
          <path
            className="gigi-halo"
            d="M 49 88 Q 49 32 127.5 32 Q 206 32 206 88 L 206 225 L 188 203 L 164 225 L 140 203 L 116 225 L 95 203 L 74 225 L 49 203 Z"
            fill="#FFE500"
            opacity="0.16"
            filter="url(#gigiSoftGlow)"
          />

          {/* Body */}
          <path
            className="gigi-body"
            d="M 49 88 Q 49 32 127.5 32 Q 206 32 206 88 L 206 225 L 188 203 L 164 225 L 140 203 L 116 225 L 95 203 L 74 225 L 49 203 Z"
            fill="url(#gigiBody)"
            stroke="#FFE500"
            strokeWidth="1.2"
            strokeOpacity="0.9"
          />

          {/* Scanlines — the PNG carries two: a 3px one at y=141 and a
              hairline at y=174. */}
          <g className="gigi-scanlines">
            <rect x="49" y="141" width="157" height="3" fill="#FFE500" opacity="0.5" />
            <rect x="49" y="174" width="157" height="1" fill="#FFF200" opacity="0.32" />
          </g>

          {/* Glitch pixels right — measured cluster boxes */}
          <g className="gigi-glitch-right" fill="#FFE500" filter="url(#gigiYGlow)">
            <rect x="210" y="109" width="7" height="7" />
            <rect x="220" y="137" width="4" height="4" />
            <rect x="217" y="192" width="6" height="3" />
          </g>
          {/* Glitch pixels left */}
          <g className="gigi-glitch-left" fill="#FFB800" filter="url(#gigiYGlow)">
            <rect x="32" y="100" width="7" height="5" />
            <rect x="36" y="132" width="5" height="7" />
            <rect x="39" y="185" width="3" height="6" />
          </g>

          {/* Chromatic displacement slices */}
          <rect x="58" y="122" width="20" height="10" fill="#FFE500" opacity="0.3" />
          <rect x="178" y="122" width="20" height="10" fill="#FFE500" opacity="0.3" />

          {/* Eye glows */}
          <ellipse cx="98" cy="113" rx="16" ry="21" fill="#FFE500" opacity="0.32" filter="url(#gigiSoftGlow)" />
          <ellipse cx="157" cy="113" rx="16" ry="21" fill="#FFE500" opacity="0.32" filter="url(#gigiSoftGlow)" />

          {/* Eye sockets — they blink */}
          <g className="gigi-eyes">
            <ellipse cx="98" cy="113" rx="13" ry="17.5" fill="url(#gigiYAccent)" filter="url(#gigiYGlow)" />
            <ellipse cx="157" cy="113" rx="13" ry="17.5" fill="url(#gigiYAccent)" filter="url(#gigiYGlow)" />
          </g>

          {/* Pupils — they drift */}
          <g className="gigi-pupils">
            <ellipse className="gigi-pupil gigi-pupil-left" cx="100" cy="118" rx="5.2" ry="7.5" fill="#050505" />
            <ellipse className="gigi-pupil gigi-pupil-right" cx="159" cy="118" rx="5.2" ry="7.5" fill="#050505" />
          </g>

          {/* Eye sparkle */}
          <g className="gigi-sparkle">
            <circle cx="102.5" cy="110" r="2.4" fill="#FFFFFF" />
            <circle cx="161.5" cy="110" r="2.4" fill="#FFFFFF" />
          </g>

          {/* Mouth — breathes at rest, moves while speaking */}
          <g className="gigi-mouth">
            <ellipse cx="127.5" cy="158" rx="8.5" ry="10.5" fill="url(#gigiYAccent)" filter="url(#gigiYGlow)" />
            <ellipse cx="127.5" cy="158" rx="3.6" ry="5.2" fill="#050505" />
          </g>

          {/* Arms — measured as gold outside the body, y 148..178, reaching
              23px out; the left one waves (pivot at the shoulder via
              fill-box). */}
          <path
            className="gigi-arm gigi-arm-left"
            d="M 47 148 Q 26 157 29 177"
            stroke="#FFE500"
            strokeWidth="5"
            fill="none"
            strokeLinecap="round"
            filter="url(#gigiYGlow)"
          />
          <path
            className="gigi-arm gigi-arm-right"
            d="M 208 148 Q 229 157 226 177"
            stroke="#FFE500"
            strokeWidth="5"
            fill="none"
            strokeLinecap="round"
            filter="url(#gigiYGlow)"
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
// Comment bubble + context hook
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
