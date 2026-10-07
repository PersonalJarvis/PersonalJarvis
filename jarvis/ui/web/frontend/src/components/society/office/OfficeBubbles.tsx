/**
 * Comic bubbles over heads in the office: a thought cloud while an agent
 * works, a speech bubble with its reply, a "?" bubble when it waits for the
 * person — and the person's own words over their character.
 *
 * In-world, like the nameplates: one dark look in both themes, scaled by
 * camera distance. Ambient bubbles (agents nobody is talking to) hide when
 * the camera is far away, so the overview never turns into a wall of text.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Html } from "@react-three/drei";
import { useFrame } from "@react-three/fiber";
import { AlertTriangle, Check, LoaderCircle } from "lucide-react";
import { Vector3, type Group } from "three";
import { useT } from "@/i18n";
import { useEventStore } from "@/store/events";
import { memoryNoteFor, useMemoryWrites, type MemoryWrite } from "@/store/memoryWrites";
import type { SocietyAgent } from "../data";
import type { ChatLine } from "./deskChat";
import { player } from "./officeStore";
import { plateScale } from "./OfficeAgents";
import { useGigiErrands } from "./gigiErrands";
import { memoryLine, type MemoryLabels, type MemoryLine } from "./memoryBubble";
import {
  ambientBubble, bubbleText, conversationBubble, itemsFor, PLAYER_LINE_MS, REPLY_LINGER_MS, talkStoreFor, useOfficeTalk,
  type Bubble, type BubbleLabels,
} from "./officeTalk";

/** Ambient bubbles only show within this camera distance (metres). */
const AMBIENT_RANGE_M = 20;

export function useBubbleLabels(): BubbleLabels {
  const t = useT();
  return useMemo(() => ({
    thinking: t("society.office.bubble_thinking"),
    tool: t("society.office.bubble_tool"),
    failed: t("society.office.bubble_failed"),
    cancelled: t("society.office.bubble_cancelled"),
    done: t("society.office.bubble_done"),
    approval: t("society.office.bubble_approval"),
    waiting: t("society.office.bubble_waiting"),
  }), [t]);
}

export function useMemoryLabels(): MemoryLabels {
  const t = useT();
  return useMemo(() => ({
    updating: t("society.office.memory_updating"),
    updatingFile: t("society.office.memory_updating_file"),
    saved: t("society.office.memory_saved"),
    savedFile: t("society.office.memory_saved_file"),
    failed: t("society.office.memory_failed"),
    failedFile: t("society.office.memory_failed_file"),
  }), [t]);
}

const NO_WRITES: Record<string, MemoryWrite> = {};

/**
 * The lead's memory receipt (George writing MEMORY.md, USER.md, SOUL.md …)
 * as one bubble line. Only the lead shows it; other agents keep their bubbles.
 */
function useMemoryLine(agent: SocietyAgent): MemoryLine | null {
  const labels = useMemoryLabels();
  const writes = useMemoryWrites((s) => (agent.tier === "lead" ? s.writes : NO_WRITES));
  const own = useMemo(() => Object.values(writes).filter((w) => w.agentId === agent.agentId), [writes, agent.agentId]);
  const now = useClock(own.length > 0);
  if (own.length === 0) return null;
  // A receipt that arrived after the last tick is newer than `now`: it counts as just now.
  const note = memoryNoteFor(writes, agent.agentId, Math.max(now, ...own.map((w) => w.atMs)));
  return note ? memoryLine(note, labels) : null;
}

const MEMORY_ICON = { pending: LoaderCircle, saved: Check, failed: AlertTriangle } as const;

/** Gigi's side of an errand ("on my way to Nora", then the task) and the recipient's "Got it". */
function useErrandBubble(agent: SocietyAgent): Bubble | null {
  const t = useT();
  const errand = useGigiErrands((s) => s.current);
  if (!errand) return null;
  if (agent.tier === "lead") {
    return errand.phase === "fly"
      ? { kind: "thought", text: t("society.office.errand_flying").replace("{0}", errand.toName || "…"), live: true, atMs: 0 }
      : { kind: "speech", text: bubbleText(errand.text || t("society.office.errand_task"), 180), live: false, atMs: 0 };
  }
  if (agent.agentId === errand.to && errand.phase === "deliver") {
    return { kind: "speech", text: t("society.office.errand_ack"), live: false, atMs: 0 };
  }
  return null;
}

/** Re-renders once a second while `active`, so a settled bubble can expire. */
function useClock(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [active]);
  return now;
}

/** The bubble itself, anchored `height` metres above its parent group. */
export function BubbleView({ bubble, height, range = Infinity, onClick, memory }: {
  bubble: Bubble; height: number; range?: number; onClick?: () => void;
  /** The lead's memory receipt, shown as its own line under the bubble text. */
  memory?: MemoryLine | null;
}) {
  const anchor = useRef<Group>(null);
  const box = useRef<HTMLDivElement>(null);
  const world = useMemo(() => new Vector3(), []);
  const last = useRef({ scale: 0, hidden: false });
  useFrame(({ camera }) => {
    if (!anchor.current || !box.current) return;
    const distance = camera.position.distanceTo(anchor.current.getWorldPosition(world));
    const hidden = distance > range;
    const scale = plateScale(distance);
    if (hidden !== last.current.hidden) {
      last.current.hidden = hidden;
      box.current.style.visibility = hidden ? "hidden" : "visible";
    }
    if (Math.abs(scale - last.current.scale) < 0.02) return;
    last.current.scale = scale;
    box.current.style.transform = `translateX(-50%) scale(${scale.toFixed(2)})`;
  });
  return (
    <group ref={anchor} position={[0, height, 0]}>
      <Html zIndexRange={[22, 0]} style={{ pointerEvents: "none" }}>
        <div ref={box} className="office-bubble" data-kind={bubble.kind} data-live={bubble.live || undefined} data-office-ui
          role={onClick ? "button" : undefined} onClick={onClick ? (e) => { e.stopPropagation(); onClick(); } : undefined}
          style={{ pointerEvents: onClick ? "auto" : "none" }}>
          {bubble.text
            ? <p>{bubble.text}{bubble.live && bubble.kind !== "ask" ? <span className="office-bubble-dots" aria-hidden><i /><i /><i /></span> : null}</p>
            : null}
          {memory ? <MemoryRow line={memory} /> : null}
          {bubble.kind === "thought"
            ? <span className="office-bubble-puffs" aria-hidden><i /><i /></span>
            : <span className="office-bubble-tail" aria-hidden />}
        </div>
      </Html>
    </group>
  );
}

function MemoryRow({ line }: { line: MemoryLine }) {
  const Icon = MEMORY_ICON[line.phase];
  return (
    <span className="office-bubble-memory" data-phase={line.phase} role="status" aria-live="polite">
      <Icon aria-hidden strokeWidth={2.4} />
      <span className="office-bubble-memory-text">{line.text}</span>
    </span>
  );
}

/**
 * The agent's bubble: its live conversation when the person talks to it,
 * otherwise what it is busy with (from the desk-monitor tail).
 */
export function AgentBubble({ agent, lines, selected, height, onSelect }: {
  agent: SocietyAgent; lines: readonly ChatLine[] | undefined; selected: boolean; height: number; onSelect: (id: string) => void;
}) {
  const labels = useBubbleLabels();
  const talking = useOfficeTalk((s) => s.agentId === agent.agentId);
  const useStore = talkStoreFor(agent);
  const timeline = useStore((s) => (talking ? s.timeline : null));
  const activeSessionId = useStore((s) => (talking ? s.activeSessionId : null));
  const talk = useMemo(() => {
    if (!timeline) return null;
    return conversationBubble(itemsFor(agent, activeSessionId, timeline), timeline.pendingApprovals.length, labels);
  }, [timeline, activeSessionId, agent, labels]);
  const lingering = !!talk && !talk.live && !selected;
  const now = useClock(lingering);
  const open = () => onSelect(agent.agentId);
  const errand = useErrandBubble(agent);
  const memory = useMemoryLine(agent);
  if (errand) return <BubbleView bubble={errand} height={height} onClick={open} memory={memory} />;
  if (talk && (talk.live || selected || now - talk.atMs < REPLY_LINGER_MS)) {
    return <BubbleView bubble={talk} height={height} onClick={open} memory={memory} />;
  }
  const ambient = ambientBubble(agent.state, lines, labels);
  if (ambient) return <BubbleView bubble={ambient} height={height} range={memory ? Infinity : AMBIENT_RANGE_M} onClick={open} memory={memory} />;
  // Nothing else to say: the memory receipt alone fills the thought cloud.
  return memory
    ? <BubbleView bubble={{ kind: "thought", text: "", live: false, atMs: 0 }} height={height} onClick={open} memory={memory} />
    : null;
}

/** The person's own words over their character: the live transcript while speaking, then the sent line. */
export function PlayerBubble({ height }: { height: number }) {
  const t = useT();
  const group = useRef<Group>(null);
  const listening = useOfficeTalk((s) => s.listening);
  const line = useOfficeTalk((s) => s.playerLine);
  const interim = useEventStore((s) => (listening ? s.dictationText : ""));
  const now = useClock(!!line && !listening);
  useFrame(() => { group.current?.position.set(player.x, 0, player.z); });
  let bubble: Bubble | null = null;
  if (listening) bubble = { kind: "speech", text: interim || t("society.office.bubble_listening"), live: true, atMs: 0 };
  else if (line && now - line.atMs < PLAYER_LINE_MS) bubble = { kind: "speech", text: line.text, live: false, atMs: line.atMs };
  return (
    <group ref={group}>
      {bubble ? <BubbleView bubble={bubble} height={height} /> : null}
    </group>
  );
}
