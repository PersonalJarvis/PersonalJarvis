/**
 * Two agents talking, drawn — the R3F half of `conversation.ts`.
 *
 * Everything it needs about WHERE the figures are it reads from
 * `walkerRegistry.ts`, the same per-frame map the minimap reads: positions
 * change 60 times a second and putting them through React would be absurd.
 * Everything it needs about WHO said WHAT it reads from the conversation
 * store, which changes a few times per sentence.
 *
 * What it writes back is one `SpeechPose` per participant: a heading to turn
 * toward and a flag for the `talk` clip. Never a position. A conversation
 * cannot move a figure (`world-behaviour-manual.md` §1) — the backend owns
 * where an agent is, and a figure that walked to another shop because it said
 * something would be the island inventing a third truth.
 *
 * Mounted OUTSIDE `<Shadowed>`: the ground pips are additive halos and a
 * translucent glow that casts a shadow reads as a hole in the grass.
 */
import { memo, useEffect, useMemo, useRef } from "react";
import { Html } from "@react-three/drei";
import { useFrame, useThree } from "@react-three/fiber";
import type { Group, Mesh } from "three";

import { fill, useT } from "@/i18n";
import type { SocietyAgent } from "../data";
import { WORLD_HERO_SCALE } from "./WalkerFigure";
import { useKit } from "./WorldKit";
import { useBuildingPoses } from "./buildingPoses";
import {
  MAX_CONCURRENT,
  MIN_FACE_M,
  ROOM_RING_R_M,
  bubbleSeconds,
  classify,
  driftedApart,
  facingBetween,
  pipScale,
  tangentFacing,
  type ConvoKind,
  type TalkKind,
} from "./conversation";
import {
  clearSpeechPoses,
  orderedTalks,
  setSpeechPose,
  useConversationStore,
  type Conversation,
} from "./conversationStore";
import { buildIsland, groundY, tileToWorld } from "./islandLayout";
import { walkerPins, type WalkerPin } from "./walkerRegistry";

/**
 * How high a bubble floats over a figure's own head, in metres.
 *
 * High enough to clear the nameplate: at 0.5 m the bubble sat exactly on it
 * and hid the one thing the viewer needs — WHO is speaking.
 */
const BUBBLE_LIFT_M = 1.75;
/** Stacked conversations lift their bubbles by this much each, so none overlap. */
const BUBBLE_STACK_M = 0.9;
/**
 * The listener's "…" rides lower than the speaker's line: above its nameplate,
 * well under the bubble. Two figures on one stand tile put both anchors at the
 * same screen point, and at the same height the dots sat on the words.
 */
const LISTEN_LIFT_M = 0.62;
/**
 * …and it is dropped entirely when the two stand this close, because at 64 m
 * across the stage four metres is ten pixels: the dots would sit inside the
 * speaker's own bubble whatever height they were given. Wider than
 * `MIN_FACE_M`, which only has to be wider than the lateral offset.
 */
const LISTEN_HIDE_M = 4;
/**
 * A room's sign clears the Town Hall's facade. Anything lower shares its anchor
 * with the speech bubble over the member standing there, and loses.
 */
const ROOM_SIGN_Y_M = 9.5;
/** The marker ring under a figure that is in a conversation, in metres. */
const SPEAK_RING_R_M = 1.15;
/** Head height for a figure whose recipe carries no height. */
const DEFAULT_HEAD_M = 2.0;

/** One participant, resolved: the roster row, the live pin, the head height. */
interface Party {
  agent: SocietyAgent;
  pin: WalkerPin;
  headY: number;
}

function headHeight(agent: SocietyAgent): number {
  if (!agent.figure) return DEFAULT_HEAD_M;
  return (agent.figure.heightM ?? 1.75) * WORLD_HERO_SCALE;
}

/**
 * A conversation with both ends resolved against the live island, or null when
 * there is nothing to draw. `partner` is null for the one-sided case: a message
 * from the scheduler, or to a row that has no figure yet.
 */
interface Staged {
  talk: Conversation;
  speaker: Party;
  partner: Party | null;
  kind: ConvoKind;
  distanceM: number;
  /** Index among the live conversations — decides the bubble's stack height. */
  slot: number;
}

function stage(
  talks: Conversation[],
  byId: Map<string, SocietyAgent>,
  pins: ReadonlyMap<string, WalkerPin>,
): Staged[] {
  const out: Staged[] = [];
  talks.forEach((talk, slot) => {
    const speakerAgent = byId.get(talk.speakerId);
    const speakerPin = pins.get(talk.speakerId);
    const partnerAgent = talk.partnerId ? byId.get(talk.partnerId) : undefined;
    const partnerPin = talk.partnerId ? pins.get(talk.partnerId) : undefined;

    // Whoever we can see is the anchor. When only the partner has a figure —
    // the scheduler messaging Scout — the bubble belongs over the partner.
    const speaker: Party | null =
      speakerAgent && speakerPin
        ? { agent: speakerAgent, pin: speakerPin, headY: headHeight(speakerAgent) }
        : null;
    const partner: Party | null =
      partnerAgent && partnerPin
        ? { agent: partnerAgent, pin: partnerPin, headY: headHeight(partnerAgent) }
        : null;
    const anchor = speaker ?? partner;
    if (!anchor) return; // neither end is on the island yet: nothing to draw

    const other = anchor === speaker ? partner : null;
    const distanceM = other
      ? Math.hypot(anchor.pin.x - other.pin.x, anchor.pin.z - other.pin.z)
      : 0;
    // Decided once and cached on the row: a wandering figure must not flip a
    // running conversation from a talk into a call mid-sentence.
    const kind: ConvoKind = talk.kind ?? (other ? classify(distanceM) : "talk");
    out.push({ talk, speaker: anchor, partner: other, kind, distanceM, slot });
  });
  return out;
}

export interface ConversationSceneProps {
  agents: SocietyAgent[];
  /** Reduced motion: the island holds still and so does every conversation. */
  paused: boolean;
}

export function ConversationScene({ agents, paused }: ConversationSceneProps) {
  const talks = useConversationStore((s) => s.talks);
  const room = useConversationStore((s) => s.room);
  const byId = useMemo(() => new Map(agents.map((a) => [a.agentId, a])), [agents]);
  const live = useMemo(() => orderedTalks(talks).slice(0, MAX_CONCURRENT), [talks]);

  // A conversation whose line has run its course hands the queue on. Kept out
  // of `useFrame` on purpose: it is a state change, not an animation.
  useEffect(() => {
    if (live.length === 0) return;
    const timers = live
      .filter((t) => t.line !== null)
      .map((t) => {
        const left = t.lineStartedMs + bubbleSeconds(t.line?.chars ?? 0) * 1000 - Date.now();
        return setTimeout(
          () => useConversationStore.getState().advance(t.key),
          Math.max(0, left),
        );
      });
    return () => timers.forEach(clearTimeout);
  }, [live]);

  // A conversation left with no line and nothing queued is over.
  useEffect(() => {
    const empty = live.filter((t) => t.line === null && t.queued.length === 0);
    if (empty.length === 0) return;
    const timers = empty.map((t) =>
      setTimeout(() => useConversationStore.getState().closeTalk(t.key), 800),
    );
    return () => timers.forEach(clearTimeout);
  }, [live]);

  // The scene is going away (unmount, a lost WebGL context): every figure it
  // was driving goes back to its own sim (AP-32 — nothing survives the loss).
  useEffect(() => clearSpeechPoses, []);

  if (live.length === 0 && !room) return null;

  return (
    <group>
      {live.map((talk) => (
        <ConversationView
          key={talk.key}
          talk={talk}
          byId={byId}
          paused={paused}
          slot={live.indexOf(talk)}
        />
      ))}
      {room && <RoomSign room={room} paused={paused} />}
    </group>
  );
}

/**
 * One conversation. The heavy work is per frame and writes to refs; the only
 * React state it reads is the line, which changes once per sentence.
 */
function ConversationView({
  talk,
  byId,
  paused,
  slot,
}: {
  talk: Conversation;
  byId: Map<string, SocietyAgent>;
  paused: boolean;
  slot: number;
}) {
  const kit = useKit();
  const invalidate = useThree((s) => s.invalidate);
  const anchorRef = useRef<Group>(null);
  const pipA = useRef<Mesh>(null);
  const pipB = useRef<Mesh>(null);

  useFrame(({ clock }) => {
    const pins = walkerPins();
    const [next] = stage([talk], byId, pins);
    const anchor = anchorRef.current;
    if (!next || !anchor) {
      if (pipA.current) pipA.current.visible = false;
      if (pipB.current) pipB.current.visible = false;
      return;
    }

    const { speaker, partner } = next;
    anchor.position.set(
      speaker.pin.x,
      speaker.headY + BUBBLE_LIFT_M + slot * BUBBLE_STACK_M,
      speaker.pin.z,
    );

    if (!paused && partner) {
      // Two figures on one stand tile have no usable vector between them: the
      // heading would jitter and they would turn INTO each other. Below the
      // floor they turn along the building's front instead, across each other.
      const close = next.distanceM < MIN_FACE_M;
      const facing = close
        ? tangentFacing(speaker.pin.x <= partner.pin.x ? 0 : Math.PI, 1)
        : facingBetween(speaker.pin.x, speaker.pin.z, partner.pin.x, partner.pin.z);
      const back = close
        ? tangentFacing(speaker.pin.x <= partner.pin.x ? 0 : Math.PI, -1)
        : facingBetween(partner.pin.x, partner.pin.z, speaker.pin.x, speaker.pin.z);
      // A talk that has drifted apart keeps its bubbles and drops the facing:
      // shouting across the island should not look like a quiet word.
      const holdFacing = next.kind === "talk" && !driftedApart(next.distanceM);
      setSpeechPose(talk.speakerId, {
        heading: holdFacing ? facing : null,
        talking: talk.line !== null,
      });
      setSpeechPose(partner.agent.agentId, {
        heading: holdFacing ? back : null,
        talking: false,
      });
    } else if (!paused) {
      setSpeechPose(talk.speakerId, { heading: null, talking: talk.line !== null });
    }

    // The ground pips: a soft breathing pool under whoever is in this
    // conversation. One draw call each, and they read at any zoom where the
    // bubble text has already become unreadable.
    const t = clock.getElapsedTime();
    const scale = paused ? 1 : pipScale(t);
    const { map } = buildIsland();
    if (pipA.current) {
      pipA.current.visible = true;
      pipA.current.position.set(speaker.pin.x, groundY(map, speaker.pin.x, speaker.pin.z) + 0.03, speaker.pin.z);
      pipA.current.scale.set(SPEAK_RING_R_M * scale, SPEAK_RING_R_M * scale, 1.4);
    }
    if (pipB.current) {
      pipB.current.visible = partner !== null;
      if (partner) {
        pipB.current.position.set(partner.pin.x, groundY(map, partner.pin.x, partner.pin.z) + 0.03, partner.pin.z);
        pipB.current.scale.set(SPEAK_RING_R_M * scale, SPEAK_RING_R_M * scale, 1.4);
      }
    }
    if (paused) invalidate();
  });

  // The figures go back to their own sims the moment this conversation ends.
  useEffect(() => {
    const speakerId = talk.speakerId;
    const partnerId = talk.partnerId;
    return () => {
      setSpeechPose(speakerId, { heading: null, talking: false });
      if (partnerId) setSpeechPose(partnerId, { heading: null, talking: false });
    };
  }, [talk.speakerId, talk.partnerId]);

  const lineSpeaker = talk.line ? byId.get(talk.line.speakerId) : undefined;
  const speakerAgent =
    lineSpeaker ?? byId.get(talk.speakerId) ?? (talk.partnerId ? byId.get(talk.partnerId) : undefined);
  const accent = speakerAgent?.palette.accent ?? "#1f2a3a";
  const speakerName = speakerAgent?.name ?? talk.speakerId;

  return (
    <group>
      {/* A ring, not a filled pool: the island is already covered in soft lamp
          light and a wash under a figure vanished into it. */}
      <mesh
        ref={pipA}
        geometry={kit.g.ring}
        material={kit.m.halo(accent, 0.75)}
        rotation={[Math.PI / 2, 0, 0]}
        visible={false}
      />
      <mesh
        ref={pipB}
        geometry={kit.g.ring}
        material={kit.m.halo(accent, 0.45)}
        rotation={[Math.PI / 2, 0, 0]}
        visible={false}
      />
      <group ref={anchorRef}>
        {talk.line && (
          <Html center zIndexRange={[34, 12]} style={{ pointerEvents: "none" }}>
            <SpeechBubble
              who={speakerName}
              text={talk.line.text}
              kind={talk.line.kind}
              accent={accent}
              skipped={talk.skipped}
              truncated={talk.line.truncated}
            />
          </Html>
        )}
      </group>
      {talk.partnerId && talk.line && (
        <ListeningBubble
          byId={byId}
          partnerId={talk.partnerId}
          speakerId={talk.speakerId}
          slot={slot}
        />
      )}
    </group>
  );
}

/** The three dots over whoever is being spoken to. */
function ListeningBubble({
  byId,
  partnerId,
  speakerId,
  slot,
}: {
  byId: Map<string, SocietyAgent>;
  partnerId: string;
  speakerId: string;
  slot: number;
}) {
  const t = useT();
  const group = useRef<Group>(null);
  const agent = byId.get(partnerId);

  useFrame(() => {
    const pin = walkerPins().get(partnerId);
    const node = group.current;
    if (!node) return;
    if (!pin || !agent) {
      node.visible = false;
      return;
    }
    const speaker = walkerPins().get(speakerId);
    // Standing on the same tile the two anchors coincide: the speaker's own
    // bubble already names both ends, so a second one there is only noise.
    const together =
      speaker !== undefined && Math.hypot(speaker.x - pin.x, speaker.z - pin.z) < LISTEN_HIDE_M;
    node.visible = !together;
    node.position.set(
      pin.x,
      headHeight(agent) + LISTEN_LIFT_M + slot * BUBBLE_STACK_M,
      pin.z,
    );
  });

  if (!agent) return null;
  return (
    <group ref={group} visible={false}>
      <Html center zIndexRange={[33, 12]} style={{ pointerEvents: "none" }}>
        <div className="sw-bubble" data-listening aria-label={t("society.world.talk_listening")}>
          <span className="sw-bubble-dots" aria-hidden>
            <i />
            <i />
            <i />
          </span>
        </div>
      </Html>
    </group>
  );
}

const KIND_KEY: Record<TalkKind, string | null> = {
  say: null,
  query: "society.world.talk_kind_query",
  answer: "society.world.talk_kind_answer",
  propose: "society.world.talk_kind_propose",
};

/** The bubble itself. Memoised: it is DOM, and it changes once per sentence. */
const SpeechBubble = memo(function SpeechBubble({
  who,
  text,
  kind,
  accent,
  skipped,
  truncated,
}: {
  who: string;
  text: string;
  kind: TalkKind;
  accent: string;
  skipped: number;
  truncated: boolean;
}) {
  const t = useT();
  const label = KIND_KEY[kind];
  return (
    <div
      className="sw-bubble"
      data-kind={kind}
      data-accent=""
      style={{ ["--sw-accent" as string]: accent }}
    >
      <span className="sw-bubble-kind">
        <i className="sw-bubble-dot" style={{ background: accent }} />
        {who}
        {label && <em>{t(label)}</em>}
      </span>
      {text}
      {truncated ? t("society.world.talk_more") : ""}
      {skipped > 0 && (
        <span className="sw-bubble-digest">
          {fill(t("society.world.talk_digest"), { count: skipped }).replace("{0}", String(skipped))}
        </span>
      )}
    </div>
  );
});

/**
 * A bounded room in session: a ring on the ground at the Town Hall and a sign
 * saying which round it is on. The members are already standing there — the
 * checkpoint engine put them at `civic` — so this adds no movement of its own.
 */
function RoomSign({
  room,
  paused,
}: {
  room: NonNullable<ReturnType<typeof useConversationStore.getState>["room"]>;
  paused: boolean;
}) {
  const t = useT();
  const kit = useKit();
  const ring = useRef<Mesh>(null);
  // The viewer can turn the Town Hall; the ring follows it.
  const generation = useBuildingPoses((s) => s.generation);
  const anchor = useMemo(() => {
    const { map, content } = buildIsland();
    const [tx, tz] = content.places.civic.standTile;
    const [x, z] = tileToWorld(tx, tz);
    return { x, y: groundY(map, x, z) + 0.04, z };
    // `generation` is the island's rebuild counter, not an unused value.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [generation]);

  useFrame(({ clock }) => {
    if (paused || !ring.current) return;
    const r = ROOM_RING_R_M * pipScale(clock.getElapsedTime() * 0.6);
    ring.current.scale.set(r, r, 1.6);
  });

  const round = fill(t("society.world.room_round"), { count: room.round })
    .replace("{0}", String(room.round))
    .replace("{1}", String(room.maxRounds));
  const reasonKey = `society.world.room_reason_${room.reason}`;
  const reason = room.reason ? t(reasonKey) : "";

  return (
    <group position={[anchor.x, anchor.y, anchor.z]}>
      <mesh
        ref={ring}
        geometry={kit.g.ring}
        material={kit.m.halo("#ffd166", room.settled ? 0.35 : 0.7)}
        rotation={[Math.PI / 2, 0, 0]}
        scale={[ROOM_RING_R_M, ROOM_RING_R_M, 1.6]}
      />
      <Html
        position={[0, ROOM_SIGN_Y_M, 0]}
        center
        zIndexRange={[32, 12]}
        style={{ pointerEvents: "none" }}
      >
        <div className="sw-roomsign">
          {room.settled ? (
            <>
              <span>{t("society.world.room_settled")}</span>
              {reason && reason !== reasonKey && (
                <span className="sw-roomsign-topic">{reason}</span>
              )}
            </>
          ) : (
            <>
              <span className="sw-roomsign-round">{round}</span>
              {room.topic && <span className="sw-roomsign-topic">{room.topic}</span>}
            </>
          )}
        </div>
      </Html>
    </group>
  );
}

export default ConversationScene;
