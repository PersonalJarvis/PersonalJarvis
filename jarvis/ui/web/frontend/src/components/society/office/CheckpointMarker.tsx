/**
 * A checkpoint the person walks to (or clicks from afar): a gold ring on the
 * floor, a floating hexagon token with a canvas-drawn icon, and a label pill.
 * Icons are simple SVG path strings, drawn on the token's canvas and inlined
 * in the pill — no emoji, no icon fonts, nothing fetched.
 */
import { useEffect, useMemo, useRef } from "react";
import { Html } from "@react-three/drei";
import { useFrame, type ThreeEvent } from "@react-three/fiber";
import {
  CircleGeometry, CylinderGeometry, DoubleSide, MeshStandardMaterial, Vector3,
  type Group, type Mesh, type MeshBasicMaterial,
} from "three";
import { useT } from "@/i18n";
import { plateScale } from "./OfficeAgents";
import { cachedCanvasTexture } from "./canvasMaterials";
import type { Checkpoint, CheckpointKind } from "./officeLayout";
import { CHECKPOINT_GOLD } from "./officePalette";

export type CheckpointIcon = "spawn" | "plus" | "list" | "team" | "shirt" | "star" | "coffee" | "updown" | "target";

/** Stroke-only icons in a 24 × 24 box (round caps and joins). */
export const CHECKPOINT_ICON_PATHS: Record<CheckpointIcon, string> = {
  // The spawn point: a plus between two arcs, a portal opening.
  spawn: "M12 8.5v7M8.5 12h7M5.2 7.5A8 8 0 0 1 18.8 7.5M18.8 16.5A8 8 0 0 1 5.2 16.5",
  plus: "M12 5v14M5 12h14",
  list: "M9 6h11M9 12h11M9 18h11M4.5 6h.01M4.5 12h.01M4.5 18h.01",
  team: "M9 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM3 20c0-3.3 2.7-5.5 6-5.5s6 2.2 6 5.5M16.5 11a2.5 2.5 0 1 0 0-5M18 14.5c2 .6 3.5 2.4 3.5 5",
  shirt: "M8 3L3 6l2 4 2.5-1v12h9V9l2.5 1 2-4-5-3c-.5 1.5-2 2.5-4 2.5S8.5 4.5 8 3z",
  star: "M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1-4.4-4.3 6.1-.9z",
  coffee: "M4 9h12v5a5 5 0 0 1-5 5H9a5 5 0 0 1-5-5V9zM16 10h1.5a2.5 2.5 0 0 1 0 5H16M8 3.5c0 1 1 1 1 2M12 3.5c0 1 1 1 1 2",
  // The elevator: an up arrow beside a down arrow.
  updown: "M8 20V4M4 8l4-4 4 4M16 4v16M12 16l4 4 4-4",
  // Mission Control: a target with crosshairs.
  target: "M12 3a9 9 0 1 1 0 18a9 9 0 1 1 0-18zM12 8a4 4 0 1 1 0 8a4 4 0 1 1 0-8zM12 1v4M12 19v4M1 12h4M19 12h4",
};

/** The icon each checkpoint wears, on its floor token and in the reception's list of places. */
export const CHECKPOINT_ICON: Record<CheckpointKind, CheckpointIcon> = {
  spawn: "spawn", launch: "spawn", create: "plus", manage: "list", team: "team", wardrobe: "shirt", lead: "star", break: "coffee", elevator: "updown", mission: "target",
};

const TOKEN_RADIUS = 0.36;
const TOKEN_DEPTH = 0.1;
/** Height of the token centre above the floor. */
export const TOKEN_Y = 1.7;
const LABEL_Y = TOKEN_Y - 0.62;
const RING_Y = 0.025;

// A flat-topped hexagon prism whose faces point along ±z; the face discs match its corners.
const prismGeometry = new CylinderGeometry(TOKEN_RADIUS, TOKEN_RADIUS, TOKEN_DEPTH, 6, 1, false, Math.PI / 2);
prismGeometry.rotateX(Math.PI / 2);
const faceGeometry = new CircleGeometry(TOKEN_RADIUS * 0.9, 6);

const rimMaterial = new MeshStandardMaterial({
  color: CHECKPOINT_GOLD.rim, roughness: 0.4, metalness: 0.2, emissive: CHECKPOINT_GOLD.ring, emissiveIntensity: 0.3,
});

const faceMaterials = new Map<CheckpointIcon, MeshStandardMaterial>();
function faceMaterial(icon: CheckpointIcon): MeshStandardMaterial {
  let material = faceMaterials.get(icon);
  if (!material) {
    const map = cachedCanvasTexture(`checkpoint:${icon}`, 256, 256, (ctx, w, h) => {
      const grad = ctx.createLinearGradient(0, 0, 0, h);
      grad.addColorStop(0, CHECKPOINT_GOLD.face);
      grad.addColorStop(1, CHECKPOINT_GOLD.faceDeep);
      ctx.fillStyle = grad;
      ctx.fillRect(0, 0, w, h);
      // Inner hexagon outline (flat top, matching the geometry).
      ctx.strokeStyle = "rgba(255,255,255,0.55)";
      ctx.lineWidth = 6;
      ctx.beginPath();
      for (let i = 0; i < 6; i += 1) {
        const a = (i * Math.PI) / 3;
        const x = w / 2 + Math.cos(a) * w * 0.4, y = h / 2 + Math.sin(a) * h * 0.4;
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      }
      ctx.closePath();
      ctx.stroke();
      if (typeof Path2D === "undefined") return;
      const path = new Path2D(CHECKPOINT_ICON_PATHS[icon]);
      const scale = 5.2;
      ctx.save();
      ctx.translate(w / 2 - 12 * scale, h / 2 - 12 * scale);
      ctx.scale(scale, scale);
      ctx.lineCap = "round";
      ctx.lineJoin = "round";
      // A soft dark under-stroke keeps the white icon readable on gold.
      ctx.translate(0.25, 0.3);
      ctx.strokeStyle = "rgba(90,55,0,0.45)";
      ctx.lineWidth = 3.2;
      ctx.stroke(path);
      ctx.translate(-0.25, -0.3);
      ctx.strokeStyle = CHECKPOINT_GOLD.icon;
      ctx.lineWidth = 2.3;
      ctx.stroke(path);
      ctx.restore();
    });
    material = new MeshStandardMaterial({
      color: map ? "#ffffff" : CHECKPOINT_GOLD.face, map, roughness: 0.45,
      emissive: "#ffffff", emissiveMap: map, emissiveIntensity: map ? 0.5 : 0,
    });
    faceMaterials.set(icon, material);
  }
  return material;
}

export function IconSvg({ icon }: { icon: CheckpointIcon }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.4} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d={CHECKPOINT_ICON_PATHS[icon]} />
    </svg>
  );
}

export function CheckpointMarker({ checkpoint, label, icon, active, animate, onActivate }: {
  checkpoint: Checkpoint;
  label: string;
  icon: CheckpointIcon;
  active: boolean;
  animate: boolean;
  onActivate: () => void;
}) {
  const token = useRef<Group>(null);
  const ring = useRef<Mesh>(null);
  const pulse = useRef<Mesh>(null);
  const anchor = useRef<Group>(null);
  const pill = useRef<HTMLButtonElement>(null);
  const lastScale = useRef(0);
  const hovered = useRef(false);
  const world = useMemo(() => new Vector3(), []);
  // A per-checkpoint phase so the tokens do not bob in lockstep.
  const phase = useMemo(() => [...checkpoint.id].reduce((sum, ch) => sum + ch.charCodeAt(0), 0) % 7, [checkpoint.id]);
  const t = useT();
  const hint = t(`society.office.cp_${checkpoint.id}_hint`);
  // A checkpoint over a tall prop (the holo deck) floats its token above it.
  const tokenY = checkpoint.tokenY ?? TOKEN_Y;
  const face = faceMaterial(icon);
  const ringOpacity = active ? 0.95 : 0.6;
  const fillOpacity = active ? 0.16 : 0.07;

  useFrame(({ clock, camera }, delta) => {
    const time = clock.elapsedTime + phase;
    if (token.current) {
      // Turn the icon face towards the camera (a full spin showed it edge-on half
      // the time); animation only adds a gentle bob and sway around that.
      const toCamera = Math.atan2(camera.position.x - checkpoint.x, camera.position.z - checkpoint.z);
      if (animate) {
        token.current.position.y = tokenY + Math.sin(time * 1.8) * 0.07;
        const sway = Math.sin(time * (active ? 2.2 : 1.1)) * (active ? 0.45 : 0.3);
        const goal = toCamera + sway;
        // Ease towards the goal along the shorter way round, so orbiting never snaps it.
        const diff = Math.atan2(Math.sin(goal - token.current.rotation.y), Math.cos(goal - token.current.rotation.y));
        token.current.rotation.y += diff * Math.min(1, delta * 6);
      } else {
        token.current.position.y = tokenY;
        token.current.rotation.y = toCamera;
      }
    }
    if (ring.current) {
      const material = ring.current.material as MeshBasicMaterial;
      material.opacity = animate && active ? 0.75 + Math.sin(time * 4) * 0.2 : ringOpacity;
    }
    if (pulse.current) {
      const material = pulse.current.material as MeshBasicMaterial;
      if (animate && active) {
        const k = (time % 1.6) / 1.6;
        pulse.current.visible = true;
        pulse.current.scale.setScalar(1 + k * 0.35);
        material.opacity = 0.6 * (1 - k);
      } else {
        pulse.current.visible = false;
      }
    }
    if (anchor.current && pill.current) {
      const scale = plateScale(camera.position.distanceTo(anchor.current.getWorldPosition(world)));
      if (Math.abs(scale - lastScale.current) >= 0.02) {
        lastScale.current = scale;
        pill.current.style.transform = `scale(${scale.toFixed(2)})`;
      }
    }
  });

  // Never leave a pointer cursor behind when the marker unmounts under the mouse.
  useEffect(() => () => { if (hovered.current) document.body.style.cursor = ""; }, []);

  const activate = (event: ThreeEvent<MouseEvent>) => { event.stopPropagation(); onActivate(); };
  const over = (event: ThreeEvent<PointerEvent>) => {
    event.stopPropagation();
    hovered.current = true;
    document.body.style.cursor = "pointer";
  };
  const out = () => {
    hovered.current = false;
    document.body.style.cursor = "";
  };

  return (
    <group position={[checkpoint.x, 0, checkpoint.z]} name={`checkpoint:${checkpoint.id}`}>
      {/* Floor ring, a faint fill and (active + animated) an expanding pulse. */}
      <mesh ref={ring} rotation={[-Math.PI / 2, 0, 0]} position={[0, RING_Y, 0]}>
        <ringGeometry args={[Math.max(0.05, checkpoint.radius - 0.09), checkpoint.radius, 64]} />
        <meshBasicMaterial color={CHECKPOINT_GOLD.ring} transparent opacity={ringOpacity} side={DoubleSide} depthWrite={false} toneMapped={false} />
      </mesh>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, RING_Y - 0.002, 0]}>
        <circleGeometry args={[Math.max(0.05, checkpoint.radius - 0.09), 64]} />
        <meshBasicMaterial color={CHECKPOINT_GOLD.ring} transparent opacity={fillOpacity} depthWrite={false} toneMapped={false} />
      </mesh>
      <mesh ref={pulse} rotation={[-Math.PI / 2, 0, 0]} position={[0, RING_Y + 0.002, 0]} visible={false}>
        <ringGeometry args={[Math.max(0.05, checkpoint.radius - 0.04), checkpoint.radius, 64]} />
        <meshBasicMaterial color={CHECKPOINT_GOLD.ring} transparent opacity={0} side={DoubleSide} depthWrite={false} toneMapped={false} />
      </mesh>

      {/* Floating hexagon token with the icon on both faces. */}
      <group ref={token} position={[0, tokenY, 0]} rotation={[0, Math.PI / 4, 0]} scale={active ? 1.15 : 1}
        onClick={activate} onPointerOver={over} onPointerOut={out}>
        <mesh geometry={prismGeometry} material={rimMaterial} castShadow />
        <mesh geometry={faceGeometry} material={face} position={[0, 0, TOKEN_DEPTH / 2 + 0.002]} />
        <mesh geometry={faceGeometry} material={face} position={[0, 0, -TOKEN_DEPTH / 2 - 0.002]} rotation={[0, Math.PI, 0]} />
      </group>

      <group ref={anchor} position={[0, tokenY - TOKEN_Y + LABEL_Y, 0]}>
        <Html center zIndexRange={[20, 0]}>
          <button ref={pill} type="button" data-office-ui className="office-checkpoint" data-active={active ? "true" : "false"}
            aria-label={hint ? `${label}: ${hint}` : label} title={hint || undefined}
            onClick={(event) => { event.stopPropagation(); onActivate(); }}>
            <span className="office-checkpoint-badge"><IconSvg icon={icon} /></span>
            <span className="office-checkpoint-text">
              <span className="office-checkpoint-label">{label}</span>
              {/* Up close, say what the place does, not only what it is called. */}
              {active && hint ? <span className="office-checkpoint-hint">{hint}</span> : null}
            </span>
          </button>
        </Html>
      </group>
    </group>
  );
}
