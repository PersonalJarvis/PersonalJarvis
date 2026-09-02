/**
 * The Quest Board — the monument in the middle of the market square, where
 * the big tree used to stand. A stepped stone plinth, a tall pale obelisk
 * with a slowly turning rune band and a crystal that breathes on top, three
 * notice boards around it, and one floating scroll per quest on the board:
 * amber while it waits for a taker, blue while an agent works it. Click it
 * and the stage opens the Quest Board drawer.
 *
 * Everything is primitives from `worldMaterials.ts`; the quests come from
 * `questsData.ts` and arrive within a second of a change (SocietyQuestChanged).
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useFrame, useThree, type ThreeEvent } from "@react-three/fiber";
import { Group, Mesh } from "three";

import type { SocietyQuestRow } from "@/lib/societyApi";

import { useCameraStore } from "./cameraStore";
import { buildIsland, groundY } from "./islandLayout";
import { Block, useKit, type Kit } from "./WorldKit";
import { PAL } from "./worldMaterials";
import { boardScrolls } from "./questBoard";
import { useSocietyQuests } from "./questsData";

/** Radius of the hover/selection ring under the monument, in metres. */
const RING_R = 6.4;
/** Where the floating scrolls orbit, in metres from the obelisk. */
const SCROLL_R = 3.4;

const SCROLL_COLOR: Record<SocietyQuestRow["state"], string> = {
  open: PAL.wall,
  assigned: PAL.beacon,
  running: PAL.glass,
  done: PAL.wall,
  failed: PAL.wall,
  cancelled: PAL.wall,
};

function NoticeBoard({ kit, angle, lit }: { kit: Kit; angle: number; lit: boolean }) {
  const r = 5.6;
  const x = Math.sin(angle) * r;
  const z = Math.cos(angle) * r;
  return (
    <group position={[x, 0, z]} rotation={[0, angle, 0]}>
      {/* two posts */}
      <Block kit={kit} at={[-1.1, 1.1, 0]} size={[0.22, 2.2, 0.22]} color={PAL.woodDark} />
      <Block kit={kit} at={[1.1, 1.1, 0]} size={[0.22, 2.2, 0.22]} color={PAL.woodDark} />
      {/* the board and its parchment */}
      <Block kit={kit} at={[0, 1.55, 0]} size={[2.7, 1.5, 0.14]} color={PAL.wood} />
      <Block kit={kit} at={[0, 1.55, 0.1]} size={[2.3, 1.15, 0.05]} color={PAL.wall} />
      {/* three lines of "writing" */}
      {[0.3, 0, -0.3].map((dy) => (
        <Block key={dy} kit={kit} at={[-0.15, 1.55 + dy, 0.14]} size={[1.5 - Math.abs(dy), 0.08, 0.02]} color={PAL.trim} />
      ))}
      {/* a little roof that glows when the board carries a quest */}
      <Block kit={kit} at={[0, 2.42, 0.15]} size={[3.0, 0.14, 0.9]} color={PAL.wood} />
      <Block kit={kit} at={[0, 2.32, 0.55]} size={[2.6, 0.06, 0.12]} color={lit ? PAL.beacon : PAL.trim} glow={lit} />
    </group>
  );
}

function Scroll({
  kit,
  index,
  total,
  color,
  paused,
}: {
  kit: Kit;
  index: number;
  total: number;
  color: string;
  paused: boolean;
}) {
  const ref = useRef<Group>(null);
  const phase = (index / Math.max(1, total)) * Math.PI * 2;
  useFrame(({ clock }) => {
    if (!ref.current || paused) return;
    const t = clock.getElapsedTime();
    const a = phase + t * 0.25;
    ref.current.position.set(Math.sin(a) * SCROLL_R, 4.6 + Math.sin(t * 1.6 + phase) * 0.25, Math.cos(a) * SCROLL_R);
    ref.current.rotation.y = -a + Math.PI / 2;
  });
  return (
    <group ref={ref} position={[Math.sin(phase) * SCROLL_R, 4.6, Math.cos(phase) * SCROLL_R]}>
      {/* a rolled scroll: parchment tube with two wooden knobs and a coloured seal */}
      <mesh geometry={kit.g.cylinder} material={kit.m.lit(PAL.wall)} rotation={[0, 0, Math.PI / 2]} scale={[0.34, 1.1, 0.34]} />
      <mesh geometry={kit.g.cylinder} material={kit.m.lit(PAL.woodDark)} rotation={[0, 0, Math.PI / 2]} position={[0.62, 0, 0]} scale={[0.16, 0.16, 0.16]} />
      <mesh geometry={kit.g.cylinder} material={kit.m.lit(PAL.woodDark)} rotation={[0, 0, Math.PI / 2]} position={[-0.62, 0, 0]} scale={[0.16, 0.16, 0.16]} />
      <mesh geometry={kit.g.sphere} material={kit.m.glow(color)} position={[0, 0, 0.2]} scale={0.22} />
    </group>
  );
}

export function QuestMonument({
  paused,
  onClick,
  selected,
}: {
  paused: boolean;
  /** The monument was clicked — the stage opens the Quest Board. */
  onClick?: () => void;
  selected?: boolean;
}) {
  const kit = useKit();
  const gl = useThree((s) => s.gl);
  const [hover, setHover] = useState(false);
  const band = useRef<Mesh>(null);
  const crystal = useRef<Mesh>(null);
  const quests = useSocietyQuests();
  const scrolls = useMemo(() => boardScrolls(quests.data ?? []), [quests.data]);
  const working = scrolls.some((q) => q.state === "running");

  const { map } = buildIsland();
  const y = groundY(map, 1, 1);

  useFrame(({ clock }) => {
    if (paused) return;
    const t = clock.getElapsedTime();
    if (band.current) band.current.rotation.y = t * 0.35;
    if (crystal.current) {
      const pulse = 1 + Math.sin(t * (working ? 3.2 : 1.4)) * 0.08;
      crystal.current.scale.setScalar(0.9 * pulse);
      crystal.current.rotation.y = t * 0.8;
    }
  });

  useEffect(() => {
    if (!onClick) return;
    gl.domElement.style.cursor = hover ? "pointer" : "";
    return () => {
      gl.domElement.style.cursor = "";
    };
  }, [hover, gl, onClick]);

  const click = (e: ThreeEvent<MouseEvent>) => {
    if (!onClick) return;
    e.stopPropagation();
    if (useCameraStore.getState().dragging) return;
    onClick();
  };

  const lit = scrolls.length > 0;

  return (
    <group
      position={[0, y, 0]}
      onClick={click}
      onPointerOver={(e) => {
        e.stopPropagation();
        setHover(true);
      }}
      onPointerOut={() => setHover(false)}
    >
      {/* stepped stone plinth */}
      <mesh geometry={kit.g.cylinder} material={kit.m.lit(PAL.trim)} position={[0, 0.2, 0]} scale={[8.4, 0.4, 8.4]} />
      <mesh geometry={kit.g.cylinder} material={kit.m.lit(PAL.wallShade)} position={[0, 0.55, 0]} scale={[6.4, 0.3, 6.4]} />
      <mesh geometry={kit.g.cylinder} material={kit.m.lit(PAL.trim)} position={[0, 0.85, 0]} scale={[4.6, 0.3, 4.6]} />
      {/* the obelisk: a tapering pale shaft on a dark foot, capped by a copper pyramid */}
      <Block kit={kit} at={[0, 1.5, 0]} size={[2.6, 1.0, 2.6]} color={PAL.solar} />
      <mesh geometry={kit.g.cone} material={kit.m.lit(PAL.wall)} position={[0, 6.0, 0]} scale={[2.6, 9.0, 2.6]} />
      <mesh geometry={kit.g.cone} material={kit.m.lit(PAL.hubAccent)} position={[0, 10.9, 0]} scale={[1.05, 1.4, 1.05]} />
      {/* rune band: a turning glowing ring around the shaft */}
      <mesh ref={band} geometry={kit.g.ring} material={kit.m.glow(lit ? PAL.beacon : PAL.hubGlass)} position={[0, 3.6, 0]} rotation={[Math.PI / 2, 0, 0]} scale={[3.6, 3.6, 1.6]} />
      {/* four glowing rune slots on the shaft faces */}
      {[0, Math.PI / 2, Math.PI, -Math.PI / 2].map((a) => (
        <group key={a} rotation={[0, a, 0]}>
          <Block kit={kit} at={[0, 5.2, 1.0]} size={[0.5, 2.2, 0.06]} color={working ? PAL.glass : PAL.beacon} glow />
        </group>
      ))}
      {/* the crystal that breathes on top */}
      <mesh ref={crystal} geometry={kit.g.blob} material={kit.m.glow(working ? PAL.glassEmissive : PAL.beaconCore)} position={[0, 12.3, 0]} scale={0.9} />
      {/* three notice boards facing outward */}
      {[0, (Math.PI * 2) / 3, (Math.PI * 4) / 3].map((a, i) => (
        <NoticeBoard key={a} kit={kit} angle={a + Math.PI / 6} lit={lit && i < scrolls.length} />
      ))}
      {/* one scroll per quest on the board */}
      {scrolls.map((q, i) => (
        <Scroll key={q.quest_id} kit={kit} index={i} total={scrolls.length} color={SCROLL_COLOR[q.state]} paused={paused} />
      ))}
      {(hover || selected) && (
        <mesh position={[0, 0.06, 0]} rotation={[-Math.PI / 2, 0, 0]}>
          <ringGeometry args={[RING_R - 0.3, RING_R + 0.3, 48]} />
          <meshBasicMaterial color={selected ? "#ffd166" : "#fffaf0"} transparent opacity={0.85} />
        </mesh>
      )}
    </group>
  );
}
