import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { useT } from "@/i18n";
import type { Presence } from "../messengerPresence";
import { DONE_MS, MIN_SCENE_MS, THINK_ROTATE_MS, sceneFor, type Scene } from "./scenes";
import { bubbleTint } from "@/lib/bubbleTint";
import "./botStage.css";

/**
 * The agent at work, the way a messenger shows someone typing: its own face
 * and a word ("Thinking…", "Typing…"). The face carries the scene — its eyes
 * look up and around, squint, widen at an idea, wink, smile, read line by
 * line, turn to dashes while it types — and a tool scene adds one tiny prop
 * beside it (a quill writing, a magnifier, a gear). The face must be drawn
 * with `expressive` eyes (AgentSwatch) for the expressions to play.
 *
 * A scene never flashes: it plays at least MIN_SCENE_MS before the next one
 * takes over, so a call that lasts 80 ms does not flicker. A long think
 * rotates through the thinking expressions every THINK_ROTATE_MS. Changing
 * scene never cuts: the outgoing face, prop and word stay mounted, still
 * playing their scene, and fade off over the incoming ones (Crossfade).
 */
export function BotStage({ presence, seed, avatar, color }: {
  presence: Presence;
  /** Stable per turn: picks which scene of a family this turn plays. */
  seed: string;
  /** The agent's face (AgentSwatch with `expressive` eyes). */
  avatar: ReactNode;
  /** The agent's colour, for the accents of the props. */
  color: string;
}) {
  const t = useT();
  const scene = useSceneDirector(presence, seed);
  const label = t(`bot_stage.${scene}`);
  // The tick on the finished turn's badge takes the ink that reads on the agent's colour.
  const ink = bubbleTint(color)?.color ?? "#ffffff";
  return <div className="bot-stage" data-scene={scene} style={{ "--bot": color, "--bot-ink": ink } as CSSProperties}
    role="status" aria-label={label} data-testid="messenger-presence" data-presence={presence}>
    <span className="bs-set" aria-hidden>
      <Crossfade id={scene} className="bs-face">
        <span className="bs-body">{avatar}</span>
        {OVER[scene] && <svg className="bs-over" viewBox="0 0 28 28" width="28" height="28">{OVER[scene]}</svg>}
      </Crossfade>
    </span>
    <span className="bs-props" data-empty={PROP[scene] ? undefined : ""} aria-hidden>
      <Crossfade id={scene}>
        {PROP[scene] && <svg className="bs-prop" viewBox="0 0 20 20" width="20" height="20">{PROP[scene]}</svg>}
      </Crossfade>
    </span>
    <span className="bs-label"><Crossfade id={scene} className="bs-word">{label}</Crossfade></span>
  </div>;
}

/** How long an outgoing layer stays mounted; the CSS fades it within `--bs-swap`. */
const SWAP_MS = 480;

interface Layer { id: string; node: ReactNode }

/**
 * Renders `children` as the layer for `id`, and keeps the layers of the last
 * ids mounted for SWAP_MS after `id` changes, marked `is-out`, so the CSS can
 * fade them over the incoming one. The outgoing layer keeps its element (one
 * keyed list), so its animations play on instead of restarting. The incoming
 * layer is marked `is-in` only after the first change: the stage's own entry
 * already covers its first scene.
 */
function Crossfade({ id, className, children }: { id: string; className?: string; children: ReactNode }) {
  const [current, setCurrent] = useState(id);
  const [leaving, setLeaving] = useState<Layer[]>([]);
  const [swapped, setSwapped] = useState(false);
  const shown = useRef(children);
  if (current !== id) {
    // Adjusting state while rendering: the outgoing layer must be in this very
    // frame, or it would vanish for one paint before the fade begins.
    setCurrent(id);
    setSwapped(true);
    setLeaving((gone) => [...gone.filter((layer) => layer.id !== id && layer.id !== current), { id: current, node: shown.current }].slice(-2));
  }
  useEffect(() => { shown.current = children; });
  useEffect(() => {
    if (leaving.length === 0) return;
    const timer = window.setTimeout(() => setLeaving([]), SWAP_MS);
    return () => window.clearTimeout(timer);
  }, [leaving]);
  const layers = [...leaving.filter((layer) => layer.id !== id).map((layer) => ({ ...layer, out: true })), { id, node: children, out: false }];
  return <>{layers.map((layer) => <span key={layer.id} data-scene={layer.id} aria-hidden={layer.out || undefined}
    className={["bs-scene", className, layer.out ? "is-out" : swapped ? "is-in" : ""].filter(Boolean).join(" ")}>{layer.node}</span>)}</>;
}

/** Holds each scene long enough to read, and rotates a long think. */
function useSceneDirector(presence: Presence, seed: string): Scene {
  const latest = useRef(presence);
  latest.current = presence;
  const [shown, setShown] = useState({ presence, step: 0, phase: 0, at: Date.now() });
  useEffect(() => {
    if (presence === shown.presence) return;
    const wait = Math.max(0, MIN_SCENE_MS - (Date.now() - shown.at));
    const id = window.setTimeout(() => setShown((s) => s.presence === latest.current
      ? s : { presence: latest.current, step: 0, phase: s.phase + 1, at: Date.now() }), wait);
    return () => window.clearTimeout(id);
  }, [presence, shown]);
  useEffect(() => {
    if (shown.presence !== "thinking" && shown.presence !== "working") return;
    const id = window.setInterval(() => setShown((s) => ({ ...s, step: s.step + 1, at: Date.now() })), THINK_ROTATE_MS);
    return () => window.clearInterval(id);
  }, [shown.presence, shown.phase]);
  return sceneFor(shown.presence, `${seed}:${shown.phase}`, shown.step);
}

/** Keeps a finished turn's flourish on stage for DONE_MS after it ends live. */
export function useDoneFlourish(running: boolean, finishedWell: boolean): boolean {
  const was = useRef(running);
  const [show, setShow] = useState(false);
  useEffect(() => {
    if (was.current && !running && finishedWell) {
      setShow(true);
      const id = window.setTimeout(() => setShow(false), DONE_MS);
      was.current = running;
      return () => window.clearTimeout(id);
    }
    was.current = running;
    return undefined;
  }, [running, finishedWell]);
  return show;
}

/** "Typing" only while the words are still arriving; a pause is thinking again. */
export function useTypingPresence(presence: Presence | null, textLength: number): Presence | null {
  const [grewAt, setGrewAt] = useState(0);
  const [, tick] = useState(0);
  const last = useRef(textLength);
  useEffect(() => {
    if (textLength !== last.current) {
      last.current = textLength;
      setGrewAt(Date.now());
    }
  }, [textLength]);
  const fresh = Date.now() - grewAt < 1200;
  useEffect(() => {
    if (presence !== "typing" || !fresh) return;
    const id = window.setTimeout(() => tick((n) => n + 1), 1250);
    return () => window.clearTimeout(id);
  }, [presence, fresh, grewAt]);
  return presence === "typing" && !fresh ? "thinking" : presence;
}

const FEATHER = "M12.67 19a2 2 0 0 0 1.416-.588l6.154-6.172a6 6 0 0 0-8.49-8.49L5.586 9.914A2 2 0 0 0 5 11.328V18a1 1 0 0 0 1 1z M16 8 2 22 M17.5 15H9";
const POINTER = "M4.037 4.688a.495.495 0 0 1 .651-.651l16 6.5a.5.5 0 0 1-.063.947l-6.124 1.58a2 2 0 0 0-1.438 1.435l-1.579 6.126a.5.5 0 0 1-.947.063z";
const GEAR = "M10 2.2l1.2 1.9 2.2-.5.5 2.2 1.9 1.2-1.2 1.9 1.2 1.9-1.9 1.2-.5 2.2-2.2-.5L10 17.8l-1.2-1.9-2.2.5-.5-2.2-1.9-1.2 1.2-1.9-1.2-1.9 1.9-1.2.5-2.2 2.2.5z";

/** One tiny prop beside the face, in a 20 x 20 box: the tool, never a scene of its own. */
const PROP: Partial<Record<Scene, ReactNode>> = {
  note: <>
    <path className="bs-ink" d="M2 16.5c2-2 3.5 1.5 5.5-.5s3.5 1.5 5.5-.5" />
    <g className="bs-quill"><path className="bs-line" d={FEATHER} transform="scale(.62)" /></g>
  </>,
  jot: <>
    <rect className="bs-paper" x="3.5" y="2.5" width="13" height="15" rx="2" />
    <path className="bs-jot bs-j1" d="M6.5 7H13.5" /><path className="bs-jot bs-j2" d="M6.5 10.5H12.5" /><path className="bs-jot bs-j3" d="M6.5 14H10.5" />
  </>,
  scan: <g className="bs-lens"><circle cx="8.5" cy="8.5" r="4.6" /><path d="M12 12l4.5 4.5" /></g>,
  flip: <>
    <rect className="bs-paper" x="5" y="3" width="11" height="14" rx="1.6" />
    <g className="bs-page"><rect x="5" y="3" width="11" height="14" rx="1.6" /><path d="M7.5 7h6M7.5 10h5M7.5 13h6" /></g>
  </>,
  browser: <>
    <rect className="bs-paper" x="1.5" y="3" width="17" height="13" rx="2" />
    <path className="bs-line" d="M1.5 6.5h17" />
    <rect className="bs-button" x="9" y="10.5" width="7" height="3" rx="1.5" />
    <g className="bs-pointer"><path className="bs-pointer-shape" d={POINTER} transform="scale(.42)" /></g>
  </>,
  setup: <g className="bs-gear"><path d={GEAR} /><circle cx="10" cy="10" r="2.3" /></g>,
  tune: <>
    <path className="bs-track" d="M2 7H18M2 13H18" />
    <circle className="bs-knob bs-knob1" cx="0" cy="7" r="2.2" /><circle className="bs-knob bs-knob2" cx="0" cy="13" r="2.2" />
  </>,
  command: <>
    <path className="bs-prompt" d="M2.5 6.5l3 3-3 3" />
    <rect className="bs-typed" x="7.5" y="8.4" width="9" height="2.2" rx="1.1" />
    <rect className="bs-caret" x="0" y="7.6" width="1.8" height="3.8" rx=".4" />
  </>,
  build: <>
    <rect className="bs-paper" x="8" y="13" width="10" height="5" rx="1" />
    <g className="bs-hammer"><path d="M3 8.5H13" /><rect x="11" y="5" width="4" height="7" rx="1" /></g>
    <g className="bs-spark"><path d="M15.5 10.5l2-2M17 12.5l2.4-.6" /></g>
  </>,
};

/** Drawn over the face (28 x 28, the face's own box): what happens on or above its head. */
const OVER: Partial<Record<Scene, ReactNode>> = {
  juggle: <>
    <circle className="bs-ball bs-b1" cx="0" cy="0" r="2.2" />
    <circle className="bs-ball bs-b2" cx="0" cy="0" r="2.2" />
  </>,
  recall: <>
    <circle className="bs-puff bs-p1" cx="24" cy="5.5" r="1" />
    <circle className="bs-puff bs-p2" cx="26.5" cy="2" r="1.6" />
  </>,
  done: <g className="bs-check"><circle cx="23.5" cy="23.5" r="5" /><path d="M21.2 23.6l1.6 1.6 3-3.2" /></g>,
};
