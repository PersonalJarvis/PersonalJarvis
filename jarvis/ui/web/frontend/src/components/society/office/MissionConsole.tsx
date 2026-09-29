/**
 * Mission Control's holo deck on the coding floor, in the style of a
 * superhero's workshop: a round glass command table on a lit pedestal, a HUD
 * glowing in its top, a light column feeding a turning wireframe globe with
 * orbit rings, floating data panels all the way round and drifting sparks.
 *
 * Built in local space centred on the origin and kept inside the
 * `missionConsole` box (FURNITURE_SIZE). The deck is round, so it is not a
 * solid box: navigation walks around `missionDeckObstacles` instead, strips
 * inside the table's circle — nobody bumps into air beside it. A click
 * anywhere on the deck opens Mission Control.
 */
import { useEffect, useMemo, useRef } from "react";
import { useFrame, type ThreeEvent } from "@react-three/fiber";
import { useReducedMotion } from "framer-motion";
import {
  AdditiveBlending, BufferGeometry, CircleGeometry, CylinderGeometry, DoubleSide, Float32BufferAttribute,
  IcosahedronGeometry, MeshBasicMaterial, MeshStandardMaterial, PlaneGeometry, PointsMaterial, RingGeometry,
  SphereGeometry, TorusGeometry, type Group, type Texture,
} from "three";
import { cachedCanvasTexture } from "./canvasMaterials";
import { MISSION_DECK_RADIUS, MISSION_TABLE_RADIUS, type Furniture, type FurnitureKind } from "./officeLayout";
import { useOfficeStore } from "./officeStore";

const CYAN = "#38e1ff";
const ICE = "#b8f3ff";
const AMBER = "#ffb347";

// ---------------------------------------------------------------------------
// Canvas faces: the HUD in the table top and the floating panels.
// ---------------------------------------------------------------------------

function glow(ctx: CanvasRenderingContext2D, colour: string, blur = 10): void {
  ctx.strokeStyle = colour;
  ctx.fillStyle = colour;
  ctx.shadowColor = colour;
  ctx.shadowBlur = blur;
}

/** The round HUD in the table top: range rings, ticks, sweep arcs and blips. */
function drawTableHud(ctx: CanvasRenderingContext2D, w: number, h: number): void {
  const c = w / 2;
  ctx.clearRect(0, 0, w, h);
  glow(ctx, CYAN, 8);
  ctx.lineWidth = 2;
  for (const r of [0.96, 0.78, 0.6, 0.42, 0.24]) { ctx.beginPath(); ctx.arc(c, c, c * r, 0, Math.PI * 2); ctx.stroke(); }
  // Tick ring on the rim.
  for (let i = 0; i < 120; i += 1) {
    const a = (i / 120) * Math.PI * 2, long = i % 10 === 0;
    const r0 = c * (long ? 0.86 : 0.9), r1 = c * 0.95;
    ctx.lineWidth = long ? 3 : 1.4;
    ctx.beginPath(); ctx.moveTo(c + Math.cos(a) * r0, c + Math.sin(a) * r0); ctx.lineTo(c + Math.cos(a) * r1, c + Math.sin(a) * r1); ctx.stroke();
  }
  // Thick arc segments, like gauges.
  ctx.lineWidth = 9;
  for (const [from, to, r] of [[0.1, 0.9, 0.69], [2.2, 3.4, 0.69], [4.1, 5.6, 0.51], [1.2, 1.9, 0.33]] as const) {
    ctx.beginPath(); ctx.arc(c, c, c * r, from, to); ctx.stroke();
  }
  // Crosshair and diagonals.
  ctx.lineWidth = 1.2;
  ctx.globalAlpha = 0.55;
  for (let i = 0; i < 4; i += 1) {
    const a = (i / 4) * Math.PI;
    ctx.beginPath(); ctx.moveTo(c + Math.cos(a) * c * 0.2, c + Math.sin(a) * c * 0.2); ctx.lineTo(c + Math.cos(a) * c * 0.96, c + Math.sin(a) * c * 0.96); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(c - Math.cos(a) * c * 0.2, c - Math.sin(a) * c * 0.2); ctx.lineTo(c - Math.cos(a) * c * 0.96, c - Math.sin(a) * c * 0.96); ctx.stroke();
  }
  ctx.globalAlpha = 1;
  // Blips, a few in amber.
  for (let i = 0; i < 14; i += 1) {
    const a = i * 2.39, r = c * (0.3 + ((i * 37) % 60) / 100);
    glow(ctx, i % 4 === 0 ? AMBER : ICE, 14);
    ctx.beginPath(); ctx.arc(c + Math.cos(a) * r, c + Math.sin(a) * r, i % 4 === 0 ? 7 : 4, 0, Math.PI * 2); ctx.fill();
  }
}

type PanelKind = "bars" | "wave" | "gauge" | "list" | "grid";

/** One floating panel: a framed readout in cyan on glass. */
function drawPanel(kind: PanelKind): (ctx: CanvasRenderingContext2D, w: number, h: number) => void {
  return (ctx, w, h) => {
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = "rgba(40, 190, 255, 0.22)";
    ctx.fillRect(0, 0, w, h);
    glow(ctx, CYAN, 8);
    ctx.lineWidth = 3;
    // Corner brackets instead of a full frame.
    const k = 34;
    for (const [x, y, dx, dy] of [[6, 6, 1, 1], [w - 6, 6, -1, 1], [6, h - 6, 1, -1], [w - 6, h - 6, -1, -1]] as const) {
      ctx.beginPath(); ctx.moveTo(x, y + dy * k); ctx.lineTo(x, y); ctx.lineTo(x + dx * k, y); ctx.stroke();
    }
    ctx.lineWidth = 2;
    ctx.fillRect(22, 22, w * 0.4, 7);
    ctx.globalAlpha = 0.6;
    ctx.fillRect(22, 36, w * 0.22, 4);
    ctx.globalAlpha = 1;
    const top = 58, bottom = h - 24, left = 24, right = w - 24;
    if (kind === "bars") {
      for (let i = 0; i < 12; i += 1) {
        const bh = (bottom - top) * (0.25 + (((i * 53) % 70) / 100));
        glow(ctx, i === 8 ? AMBER : CYAN, 8);
        ctx.fillRect(left + i * ((right - left) / 12) + 3, bottom - bh, (right - left) / 12 - 8, bh);
      }
    } else if (kind === "wave") {
      for (const [amp, phase, colour] of [[0.3, 0, CYAN], [0.18, 1.7, ICE], [0.1, 3.1, AMBER]] as const) {
        glow(ctx, colour, 8);
        ctx.beginPath();
        for (let x = left; x <= right; x += 4) {
          const y = (top + bottom) / 2 + Math.sin(x * 0.045 + phase) * (bottom - top) * amp + Math.sin(x * 0.13 + phase) * 6;
          if (x === left) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        }
        ctx.stroke();
      }
    } else if (kind === "gauge") {
      const cx = w / 2, cy = (top + bottom) / 2 + 8, r = (bottom - top) / 2;
      ctx.lineWidth = 12;
      ctx.globalAlpha = 0.25;
      ctx.beginPath(); ctx.arc(cx, cy, r, Math.PI * 0.8, Math.PI * 2.2); ctx.stroke();
      ctx.globalAlpha = 1;
      ctx.beginPath(); ctx.arc(cx, cy, r, Math.PI * 0.8, Math.PI * 1.85); ctx.stroke();
      ctx.lineWidth = 3;
      ctx.beginPath(); ctx.arc(cx, cy, r * 0.62, 0, Math.PI * 2); ctx.stroke();
      glow(ctx, AMBER, 12);
      ctx.beginPath(); ctx.arc(cx, cy, 8, 0, Math.PI * 2); ctx.fill();
    } else if (kind === "list") {
      for (let i = 0; i < 7; i += 1) {
        const y = top + i * ((bottom - top) / 7);
        glow(ctx, i === 2 ? AMBER : CYAN, 6);
        ctx.fillRect(left, y, 10, 10);
        ctx.globalAlpha = 0.85;
        ctx.fillRect(left + 22, y + 2, (right - left - 40) * (0.35 + ((i * 29) % 55) / 100), 6);
        ctx.globalAlpha = 1;
      }
    } else {
      ctx.lineWidth = 1;
      ctx.globalAlpha = 0.5;
      for (let x = left; x <= right; x += 24) { ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, bottom); ctx.stroke(); }
      for (let y = top; y <= bottom; y += 24) { ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(right, y); ctx.stroke(); }
      ctx.globalAlpha = 1;
      ctx.lineWidth = 3;
      ctx.beginPath();
      [[0.1, 0.8], [0.3, 0.55], [0.45, 0.62], [0.62, 0.3], [0.8, 0.38], [0.92, 0.15]].forEach(([fx, fy], i) => {
        const x = left + (right - left) * fx, y = top + (bottom - top) * fy;
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      });
      ctx.stroke();
    }
  };
}

function holoMaterial(map: Texture | null, opacity = 0.9): MeshBasicMaterial {
  return new MeshBasicMaterial({
    map, color: map ? "#ffffff" : CYAN, transparent: true, opacity, side: DoubleSide,
    depthWrite: false, blending: AdditiveBlending, toneMapped: false,
  });
}

// ---------------------------------------------------------------------------
// Shared geometry and materials.
// ---------------------------------------------------------------------------

const DECK_R = MISSION_DECK_RADIUS;
const TABLE_R = MISSION_TABLE_RADIUS;
const TABLE_Y = 0.92;
const GLOBE_Y = 2.05;

const MM = {
  deck: new MeshStandardMaterial({ color: "#243044", roughness: 0.4, metalness: 0.55 }),
  pedestal: new MeshStandardMaterial({ color: "#2a3549", roughness: 0.35, metalness: 0.6 }),
  glass: new MeshStandardMaterial({ color: "#07101d", roughness: 0.12, metalness: 0.5, transparent: true, opacity: 0.88 }),
  line: new MeshBasicMaterial({ color: CYAN, toneMapped: false }),
  lineSoft: new MeshBasicMaterial({ color: CYAN, transparent: true, opacity: 0.45, toneMapped: false, depthWrite: false }),
  column: new MeshBasicMaterial({ color: CYAN, transparent: true, opacity: 0.05, side: DoubleSide, depthWrite: false, blending: AdditiveBlending, toneMapped: false }),
  globe: new MeshBasicMaterial({ color: CYAN, wireframe: true, transparent: true, opacity: 0.32, depthWrite: false, blending: AdditiveBlending, toneMapped: false }),
  core: new MeshBasicMaterial({ color: "#0e6f8f", transparent: true, opacity: 0.12, depthWrite: false, blending: AdditiveBlending, toneMapped: false }),
  orbit: new MeshBasicMaterial({ color: CYAN, transparent: true, opacity: 0.65, blending: AdditiveBlending, toneMapped: false }),
  moon: new MeshBasicMaterial({ color: AMBER, toneMapped: false }),
  sparks: new PointsMaterial({ color: CYAN, size: 0.03, transparent: true, opacity: 0.55, depthWrite: false, blending: AdditiveBlending, toneMapped: false }),
};

const GEO = {
  deck: new CylinderGeometry(DECK_R, DECK_R + 0.04, 0.1, 64),
  deckEdge: new TorusGeometry(DECK_R + 0.01, 0.018, 8, 96),
  deckRing: new RingGeometry(TABLE_R + 0.12, TABLE_R + 0.15, 96),
  pedestal: new CylinderGeometry(0.72, 0.95, TABLE_Y - 0.1, 48),
  band: new TorusGeometry(0.8, 0.012, 6, 64),
  table: new CylinderGeometry(TABLE_R, TABLE_R - 0.05, 0.06, 72),
  tableRim: new TorusGeometry(TABLE_R, 0.022, 8, 96),
  hud: new CircleGeometry(TABLE_R - 0.06, 72),
  column: new CylinderGeometry(0.62, 0.36, GLOBE_Y - TABLE_Y - 0.35, 40, 1, true),
  globe: new IcosahedronGeometry(0.55, 3),
  core: new SphereGeometry(0.46, 24, 16),
  orbitA: new TorusGeometry(0.78, 0.009, 6, 96),
  orbitB: new TorusGeometry(0.9, 0.007, 6, 96),
  orbitC: new TorusGeometry(1.0, 0.006, 6, 96),
  moon: new SphereGeometry(0.045, 12, 8),
  panel: new PlaneGeometry(0.9, 0.54),
};

/** Sparks drifting up through the deck, seeded so every load looks the same. */
function sparkGeometry(): BufferGeometry {
  const positions: number[] = [];
  let seed = 7;
  const rand = () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647; };
  for (let i = 0; i < 120; i += 1) {
    // Kept out of the globe's middle so the sparks never pile up into a white blob.
    const a = rand() * Math.PI * 2, r = 0.7 + rand() * (TABLE_R - 0.8);
    positions.push(Math.cos(a) * r, TABLE_Y + 0.05 + rand() * 2.0, Math.sin(a) * r);
  }
  const geometry = new BufferGeometry();
  geometry.setAttribute("position", new Float32BufferAttribute(positions, 3));
  return geometry;
}

const PANELS: { kind: PanelKind; angle: number; y: number }[] = [
  { kind: "bars", angle: 0, y: 1.5 },
  { kind: "wave", angle: (Math.PI * 2) / 6, y: 1.62 },
  { kind: "gauge", angle: (Math.PI * 4) / 6, y: 1.45 },
  { kind: "list", angle: Math.PI, y: 1.58 },
  { kind: "grid", angle: (Math.PI * 8) / 6, y: 1.48 },
  { kind: "wave", angle: (Math.PI * 10) / 6, y: 1.6 },
];
const PANEL_RADIUS = 1.3;

// ---------------------------------------------------------------------------
// The deck.
// ---------------------------------------------------------------------------

function useHoloMaterials() {
  return useMemo(() => ({
    hud: holoMaterial(cachedCanvasTexture("mission:hud", 512, 512, drawTableHud), 0.95),
    panels: PANELS.map((p, i) => holoMaterial(cachedCanvasTexture(`mission:panel:${p.kind}:${i % 2}`, 384, 232, drawPanel(p.kind)), 0.85)),
  }), []);
}

export function MissionConsole() {
  const reduced = useReducedMotion() ?? false;
  const holo = useHoloMaterials();
  const sparks = useMemo(sparkGeometry, []);
  useEffect(() => () => sparks.dispose(), [sparks]);
  const globe = useRef<Group>(null);
  const orbits = useRef<Group>(null);
  const panels = useRef<Group>(null);
  const drift = useRef<Group>(null);
  const hovered = useRef(false);

  useFrame(({ clock }, delta) => {
    if (reduced) return;
    const t = clock.elapsedTime;
    if (globe.current) globe.current.rotation.y += delta * 0.3;
    if (orbits.current) {
      const [a, b, c] = orbits.current.children;
      if (a) a.rotation.z += delta * 0.6;
      if (b) b.rotation.z -= delta * 0.4;
      if (c) c.rotation.z += delta * 0.25;
    }
    if (panels.current) {
      panels.current.rotation.y += delta * 0.05;
      panels.current.children.forEach((panel, i) => { panel.position.y = PANELS[i].y + Math.sin(t * 1.1 + i) * 0.04; });
    }
    if (drift.current) drift.current.rotation.y -= delta * 0.08;
  });

  useEffect(() => () => { if (hovered.current) document.body.style.cursor = ""; }, []);
  const open = (event: ThreeEvent<MouseEvent>) => {
    event.stopPropagation();
    useOfficeStore.getState().select({ kind: "checkpoint", id: "mission" });
  };
  const over = (event: ThreeEvent<PointerEvent>) => { event.stopPropagation(); hovered.current = true; document.body.style.cursor = "pointer"; };
  const out = () => { hovered.current = false; document.body.style.cursor = ""; };

  return (
    <group name="mission-deck" onClick={open} onPointerOver={over} onPointerOut={out}>
      {/* Deck: a dark round platform with a glowing edge and a ring round the table. */}
      <mesh geometry={GEO.deck} material={MM.deck} position={[0, 0.05, 0]} receiveShadow />
      <mesh geometry={GEO.deckEdge} material={MM.line} position={[0, 0.1, 0]} rotation={[Math.PI / 2, 0, 0]} />
      <mesh geometry={GEO.deckRing} material={MM.lineSoft} position={[0, 0.103, 0]} rotation={[-Math.PI / 2, 0, 0]} />

      {/* Pedestal with light bands, glass table top, its rim and the HUD inside it. */}
      <mesh geometry={GEO.pedestal} material={MM.pedestal} position={[0, 0.1 + (TABLE_Y - 0.1) / 2, 0]} castShadow />
      {[0.3, 0.5, 0.7].map((y) => <mesh key={y} geometry={GEO.band} material={MM.line} position={[0, y, 0]} rotation={[Math.PI / 2, 0, 0]} scale={1 - y * 0.12} />)}
      <mesh geometry={GEO.table} material={MM.glass} position={[0, TABLE_Y, 0]} castShadow receiveShadow />
      <mesh geometry={GEO.tableRim} material={MM.line} position={[0, TABLE_Y + 0.03, 0]} rotation={[Math.PI / 2, 0, 0]} />
      <mesh geometry={GEO.hud} material={holo.hud} position={[0, TABLE_Y + 0.034, 0]} rotation={[-Math.PI / 2, 0, 0]} />

      {/* Light column rising from the table into the globe. */}
      <mesh geometry={GEO.column} material={MM.column} position={[0, TABLE_Y + 0.03 + (GLOBE_Y - TABLE_Y - 0.35) / 2, 0]} />

      {/* Wireframe globe with a soft core, and three tilted orbit rings with moons. */}
      <group position={[0, GLOBE_Y, 0]}>
        <group ref={globe}>
          <mesh geometry={GEO.globe} material={MM.globe} />
        </group>
        <mesh geometry={GEO.core} material={MM.core} />
        <group ref={orbits}>
          <group rotation={[Math.PI / 2.4, 0.3, 0]}>
            <mesh geometry={GEO.orbitA} material={MM.orbit} />
            <mesh geometry={GEO.moon} material={MM.moon} position={[0.78, 0, 0]} />
          </group>
          <group rotation={[Math.PI / 1.8, -0.5, 0]}>
            <mesh geometry={GEO.orbitB} material={MM.orbit} />
            <mesh geometry={GEO.moon} material={MM.moon} position={[0, 0.9, 0]} />
          </group>
          <group rotation={[Math.PI / 2, 0, 0.9]}>
            <mesh geometry={GEO.orbitC} material={MM.orbit} />
          </group>
        </group>
      </group>

      {/* Floating readouts all the way round, facing outwards, drifting slowly. */}
      <group ref={panels}>
        {PANELS.map((p, i) => (
          <group key={i} position={[Math.sin(p.angle) * PANEL_RADIUS, p.y, Math.cos(p.angle) * PANEL_RADIUS]} rotation={[0, p.angle, 0]}>
            <mesh geometry={GEO.panel} material={holo.panels[i]} rotation={[-0.45, 0, 0]} />
          </group>
        ))}
      </group>

      <group ref={drift}>
        <points geometry={sparks} material={MM.sparks} />
      </group>
    </group>
  );
}

/** Mission Control's renderers; merged into OfficeProps' exhaustive table. */
export const MISSION_RENDERERS = {
  missionConsole: () => <MissionConsole />,
} satisfies Partial<Record<FurnitureKind, (props: { item: Furniture }) => JSX.Element>>;
