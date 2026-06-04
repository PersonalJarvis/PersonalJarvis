import { useCallback, useEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";
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
        aria-label="Jarvis-Maskottchen Gigi"
        title="Gigi"
      >
        <svg viewBox="0 0 256 256" xmlns="http://www.w3.org/2000/svg" className="gigi-svg">
          <defs>
            <filter id="gigiYGlow" x="-50%" y="-50%" width="200%" height="200%">
              <feGaussianBlur stdDeviation="3" result="b" />
              <feMerge>
                <feMergeNode in="b" />
                <feMergeNode in="SourceGraphic" />
              </feMerge>
            </filter>
            <filter id="gigiSoftGlow" x="-50%" y="-50%" width="200%" height="200%">
              <feGaussianBlur stdDeviation="6" result="b" />
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
            d="M 58 90 Q 58 36 128 36 Q 198 36 198 90 L 198 208 L 180 186 L 160 208 L 140 186 L 120 208 L 100 186 L 80 208 L 58 186 Z"
            fill="#FFE500"
            opacity="0.28"
            filter="url(#gigiSoftGlow)"
          />

          {/* Body */}
          <path
            className="gigi-body"
            d="M 58 90 Q 58 36 128 36 Q 198 36 198 90 L 198 208 L 180 186 L 160 208 L 140 186 L 120 208 L 100 186 L 80 208 L 58 186 Z"
            fill="url(#gigiBody)"
            stroke="#FFE500"
            strokeWidth="1.8"
            strokeOpacity="0.85"
          />

          {/* Scanlines */}
          <g className="gigi-scanlines">
            <rect x="58" y="132" width="140" height="2.4" fill="#FFE500" opacity="0.55" />
            <rect x="58" y="160" width="140" height="1.4" fill="#FFF200" opacity="0.3" />
          </g>

          {/* Glitch pixels right */}
          <g className="gigi-glitch-right" fill="#FFE500" filter="url(#gigiYGlow)">
            <rect x="200" y="104" width="6" height="6" />
            <rect x="208" y="128" width="4" height="4" />
            <rect x="202" y="146" width="9" height="3" />
            <rect x="197" y="168" width="3" height="5" />
            <rect x="206" y="176" width="5" height="3" />
          </g>
          {/* Glitch pixels left */}
          <g className="gigi-glitch-left" fill="#FFB800" filter="url(#gigiYGlow)">
            <rect x="44" y="96" width="6" height="4" />
            <rect x="48" y="124" width="4" height="6" />
            <rect x="40" y="148" width="8" height="3" />
            <rect x="50" y="170" width="3" height="5" />
          </g>

          {/* Chromatic displacement slices */}
          <rect x="64" y="118" width="18" height="10" fill="#FFE500" opacity="0.32" />
          <rect x="170" y="118" width="18" height="10" fill="#FFE500" opacity="0.32" />

          {/* Eye glows */}
          <ellipse cx="102" cy="108" rx="13" ry="17" fill="#FFE500" opacity="0.35" filter="url(#gigiSoftGlow)" />
          <ellipse cx="154" cy="108" rx="13" ry="17" fill="#FFE500" opacity="0.35" filter="url(#gigiSoftGlow)" />

          {/* Eye sockets — auto-blinkend */}
          <g className="gigi-eyes">
            <ellipse cx="102" cy="108" rx="10" ry="14" fill="url(#gigiYAccent)" filter="url(#gigiYGlow)" />
            <ellipse cx="154" cy="108" rx="10" ry="14" fill="url(#gigiYAccent)" filter="url(#gigiYGlow)" />
          </g>

          {/* Pupils — driften sanft */}
          <g className="gigi-pupils">
            <ellipse className="gigi-pupil gigi-pupil-left" cx="104" cy="112" rx="4" ry="6" fill="#050505" />
            <ellipse className="gigi-pupil gigi-pupil-right" cx="156" cy="112" rx="4" ry="6" fill="#050505" />
          </g>

          {/* Eye sparkle */}
          <g className="gigi-sparkle">
            <circle cx="106" cy="105" r="2" fill="#FFFFFF" />
            <circle cx="158" cy="105" r="2" fill="#FFFFFF" />
          </g>

          {/* Mouth — subtile Atmung */}
          <g className="gigi-mouth">
            <ellipse cx="128" cy="146" rx="7" ry="10" fill="url(#gigiYAccent)" filter="url(#gigiYGlow)" />
            <ellipse cx="128" cy="146" rx="3" ry="5" fill="#050505" />
          </g>

          {/* Linker Arm — winkt (Drehpunkt an der Schulter via fill-box) */}
          <path
            className="gigi-arm gigi-arm-left"
            d="M 58 140 Q 40 148 42 162"
            stroke="#FFE500"
            strokeWidth="5.5"
            fill="none"
            strokeLinecap="round"
            filter="url(#gigiYGlow)"
          />
          <path
            className="gigi-arm gigi-arm-right"
            d="M 198 140 Q 216 148 214 162"
            stroke="#FFE500"
            strokeWidth="5.5"
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

const SECTION_COMMENTS: Partial<Record<SectionId, string[]>> = {
  chats: ["bereit wenn du bist.", "ich höre.", "sag mal was.", "schreib oder rede — egal."],
  agents: ["die Agents sind meine Kollegen.", "wer ist dein Liebling?"],
  skills: ["Skills sind mein Lieblings-Feature.", "was sollen wir lernen?"],
  mcps: ["mehr MCPs = mehr Power.", "was sollen wir dazuholen?"],
  terminal: ["mutig, das Terminal.", "viel Spaß in der Shell."],
  languages: ["ich spreche DE und EN.", "Sprachwechsel? Einfach sagen."],
  apikeys: ["pass auf die Keys auf.", "nicht in Git committen!"],
  settings: ["was stört dich?", "tweaken wir was?"],
  debug: ["uuuh, Debug-View.", "schau mal in die Events rein."],
};

const VOICE_COMMENTS: Partial<Record<VoiceState, string[]>> = {
  listening: ["ich höre!", "raus damit.", "ja?"],
  thinking: ["mal schauen …", "Moment.", "hmm …"],
  speaking: ["jetzt rede ich.", "kurz mal zuhören."],
  error: ["oha.", "ups.", "das war nicht ich!"],
};

const TIME_COMMENTS = {
  morning: ["guten Morgen!", "auf, auf."],
  night: ["noch wach?", "es ist spät.", "schlaf genug gekriegt?"],
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

  // Section-Wechsel triggert einen Kommentar.
  useEffect(() => {
    if (!mountedRef.current) return;
    if (activeSection === lastSectionRef.current) return;
    lastSectionRef.current = activeSection;
    const pool = SECTION_COMMENTS[activeSection];
    if (pool) show(pickRandom(pool));
  }, [activeSection, show]);

  // Voice-State-Wechsel triggert einen Kommentar.
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

  // Verbindung verloren/wiederhergestellt.
  useEffect(() => {
    if (!mountedRef.current) return;
    if (connected === lastConnectedRef.current) return;
    lastConnectedRef.current = connected;
    show(connected ? "wieder online!" : "Verbindung weg …");
  }, [connected, show]);

  // Mount: begrüßen nach Tageszeit.
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

  // Random-Idle-Chatter: alle 25–60s mit 60 % Wahrscheinlichkeit.
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
