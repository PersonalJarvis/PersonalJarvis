/**
 * A chunky toy office character built from a handful of shared primitives:
 * big round head with dot eyes, short rounded torso, stubby arms, short legs
 * and chunky sneakers. The animation is procedural (`poseFor` in
 * `toyFigure.ts`) and applied straight to a small joint hierarchy:
 *
 *   root (scale) → pelvis → torso → head / shoulders → elbows
 *                         → hips → knees → ankles
 *
 * Origin on the floor, the figure faces local +z. Geometries are module-level
 * singletons and materials are cached by colour, so twenty figures share them;
 * the frame loop allocates nothing.
 */
import { type ReactNode, useMemo, useRef, type MutableRefObject } from "react";
import { useFrame } from "@react-three/fiber";
import {
  BoxGeometry, CapsuleGeometry, Color, ConeGeometry, CylinderGeometry, MeshBasicMaterial, MeshStandardMaterial,
  SphereGeometry, TorusGeometry, type Group,
} from "three";
import { RoundedBoxGeometry } from "three-stdlib";

import type { FigureMode } from "../figures/FigureRig";
import {
  blendPose, copyPose, createPose, poseFor, TOY, TOY_HEIGHT, walkCadence,
  type HairStyle, type OutfitId, type PoseOptions, type ToyLook, type ToyPose,
} from "./toyFigureModel";

// ---------------------------------------------------------------------------
// Shared geometry

const HD = TOY.head;
/** The forearm tapers a little below the upper arm towards the wrist. */
const FORE_RADIUS = TOY.armRadius - 0.006;

const GEO = {
  sphere: new SphereGeometry(1, 40, 28),
  lowSphere: new SphereGeometry(1, 16, 12),
  /** Upper half of a sphere, reaching slightly past the equator: hair caps and hats. */
  dome: new SphereGeometry(1, 40, 18, 0, Math.PI * 2, 0, Math.PI * 0.56),
  pelvis: new RoundedBoxGeometry(TOY.pelvis.w, TOY.pelvis.h, TOY.pelvis.d, 3, 0.05),
  chest: new RoundedBoxGeometry(TOY.torso.w, TOY.torso.h - 0.02, TOY.torso.d, 3, 0.08),
  thigh: new CapsuleGeometry(TOY.legRadius, 0.12, 4, 10),
  shin: new CapsuleGeometry(0.056, 0.1, 4, 10),
  // Each limb capsule overshoots its joint, so the rounded ends overlap into a closed elbow at any bend.
  upperArm: new CapsuleGeometry(TOY.armRadius, TOY.upperArm - 0.04, 6, 16),
  foreArm: new CapsuleGeometry(FORE_RADIUS, TOY.foreArm - 0.05, 6, 16),
  /** A short T-shirt sleeve, slightly flared, hanging from the shoulder cap. */
  sleeve: new CylinderGeometry(TOY.armRadius + 0.011, TOY.armRadius + 0.016, 0.075, 24),
  cuff: new CylinderGeometry(FORE_RADIUS + 0.007, FORE_RADIUS + 0.007, 0.024, 24),
  shoe: new RoundedBoxGeometry(0.13, 0.075, TOY.heel + TOY.toe + 0.01, 3, 0.034),
  sole: new RoundedBoxGeometry(0.136, 0.022, TOY.heel + TOY.toe + 0.018, 2, 0.01),
  smile: new TorusGeometry(0.042, 0.009, 6, 14, Math.PI),
  spike: new ConeGeometry(0.065, 0.16, 6),
  brim: new RoundedBoxGeometry(0.3, 0.022, 0.17, 2, 0.01),
  /** A band of hair around the back and sides, from just above the equator down: the hair a hat leaves visible. */
  hairBand: new SphereGeometry(1, 40, 14, Math.PI * 0.92, Math.PI * 1.16, Math.PI * 0.46, Math.PI * 0.3),
  /** A unit triangular pyramid, one flat face towards +z, tip up: a jacket's V-opening (scaled, turned tip down). */
  wedge: new ConeGeometry(1, 1, 3, 1, false, Math.PI),
  box: new BoxGeometry(1, 1, 1),
  collar: new CylinderGeometry(0.085, 0.097, 1, 24),
  pocket: new RoundedBoxGeometry(0.2, 0.07, 0.02, 2, 0.01),
  lens: new TorusGeometry(0.047, 0.008, 8, 24),
};

const materials = new Map<string, MeshStandardMaterial>();
/** A material per colour, shared by every figure; matte unless a garment asks for sheen. */
function matte(color: string, roughness = 0.82, metalness = 0): MeshStandardMaterial {
  const key = `${color}|${roughness}|${metalness}`;
  let m = materials.get(key);
  if (!m) {
    m = new MeshStandardMaterial({ color, roughness, metalness });
    materials.set(key, m);
  }
  return m;
}

const EYE = new MeshStandardMaterial({ color: "#16161b", roughness: 0.35, metalness: 0 });
const CATCHLIGHT = new MeshBasicMaterial({ color: "#ffffff" });
const MOUTH = matte("#5b2b28", 0.7);
const SOLE_LIGHT = "#f4f4f2";
const METAL = matte("#c7c9cc", 0.3, 0.8);
const FRAME = matte("#1a1a1e", 0.4);
const SHADES = matte("#101014", 0.15, 0.2);

function mix(a: string, b: string, k: number): string {
  return `#${new Color(a).lerp(new Color(b), k).getHexString()}`;
}

function isLight(hex: string): boolean {
  const c = new Color(hex);
  return 0.299 * c.r + 0.587 * c.g + 0.114 * c.b > 0.6;
}

/** Depth of the head ellipsoid's front surface at (x, y) in head-group space. */
function faceZ(x: number, y: number, inset = 0): number {
  const u = x / HD.rx;
  const v = (y - HD.y) / HD.ry;
  return HD.rz * Math.sqrt(Math.max(0, 1 - u * u - v * v)) - inset;
}

// ---------------------------------------------------------------------------
// Parts

function Face({ look }: { look: ToyLook }) {
  const skin = matte(look.skin);
  const blush = matte(mix(look.skin, "#ff6f7d", 0.38));
  const brow = matte(look.hairStyle === "bald" ? mix(look.skin, "#2a1a12", 0.55) : mix(look.hair, "#1a1210", 0.35));
  const eyeY = HD.y - 0.03;
  return (
    <group>
      {[-1, 1].map((sx) => {
        const x = sx * 0.1;
        const z = faceZ(x, eyeY, 0.012);
        return (
          <group key={sx}>
            <mesh geometry={GEO.sphere} material={EYE} position={[x, eyeY, z]} rotation={[0, sx * 0.4, 0]} scale={[0.034, 0.043, 0.02]} />
            <mesh geometry={GEO.lowSphere} material={CATCHLIGHT} position={[x + 0.011, eyeY + 0.014, z + 0.017]} scale={0.009} />
          </group>
        );
      })}
      {/* Soft brows give the face an expression; hair-coloured, or a darker skin tone when bald. */}
      {[-1, 1].map((sx) => {
        const x = sx * 0.1;
        const y = eyeY + 0.056;
        return (
          <mesh key={`w${sx}`} geometry={GEO.sphere} material={brow} position={[x, y, faceZ(x, y, 0.006)]}
            rotation={[0, sx * 0.4, sx * -0.12]} scale={[0.036, 0.0095, 0.012]} />
        );
      })}
      <mesh geometry={GEO.sphere} material={skin} position={[0, HD.y - 0.085, faceZ(0, HD.y - 0.085, 0.006)]} scale={[0.026, 0.021, 0.02]} />
      <mesh geometry={GEO.smile} material={MOUTH} position={[0, HD.y - 0.115, faceZ(0, HD.y - 0.115, 0.004)]} rotation={[-0.25, 0, Math.PI]} />
      {look.blush && [-1, 1].map((sx) => {
        // Inside the face outline: further out, the cheek pokes past the head's silhouette.
        const x = sx * 0.145;
        const y = HD.y - 0.085;
        return (
          <mesh key={`b${sx}`} geometry={GEO.sphere} material={blush} position={[x, y, faceZ(x, y, 0.008)]}
            rotation={[0, sx * 0.5, 0]} scale={[0.04, 0.024, 0.012]} />
        );
      })}
      {[-1, 1].map((sx) => (
        <mesh key={`e${sx}`} geometry={GEO.sphere} material={skin} position={[sx * (HD.rx - 0.012), HD.y - 0.02, -0.01]} scale={[0.03, 0.058, 0.045]} castShadow />
      ))}
    </group>
  );
}

/** Hair cap shared by most styles: a dome tilted back so the forehead shows. */
/** Tufts along the hairline: a soft fringe instead of the hard rim of a bowl cut. */
const FRINGE: Array<[number, number, number]> = [[-0.2, 0.075, -0.55], [-0.105, 0.098, -0.25], [0, 0.105, 0], [0.105, 0.098, 0.25], [0.2, 0.075, 0.55]];

function HairCap({ color, grow = 0, fringe = true }: { color: string; grow?: number; fringe?: boolean }) {
  const material = matte(color);
  return (
    <group>
      {/* Tilted back just enough to free the forehead; further and the crown shows skin. */}
      <mesh geometry={GEO.dome} material={material} position={[0, HD.y + 0.01, -0.005]} rotation={[-0.5, 0, 0]}
        scale={[HD.rx + 0.018 + grow, HD.ry + 0.03 + grow, HD.rz + 0.03 + grow]} castShadow />
      {/* Hair reaches down the back of the head to the nape, not just over the crown. */}
      <mesh geometry={GEO.hairBand} material={material} position={[0, HD.y - 0.005, -0.004]}
        scale={[HD.rx + 0.009 + grow, HD.ry + 0.008 + grow, HD.rz + 0.012 + grow]} castShadow />
      {fringe && FRINGE.map(([x, dy, rz], i) => {
        const y = HD.y + dy;
        return (
          // Sunk into the head and flat, so from the side the fringe hugs the forehead instead of jutting out.
          <mesh key={i} geometry={GEO.sphere} material={material} position={[x, y, faceZ(x, y, 0.012)]}
            rotation={[0.35, 0, rz]} scale={[0.085, 0.05, 0.036]} castShadow />
        );
      })}
    </group>
  );
}

const SPIKES: Array<[number, number, number, number, number]> = [
  [0, 0.6, 0.1, 0.75, 0], [-0.12, 0.6, 0.04, 0.35, 0.45], [0.12, 0.6, 0.04, 0.35, -0.45],
  [0, 0.62, -0.05, -0.2, 0], [-0.21, 0.52, -0.04, -0.1, 0.95], [0.21, 0.52, -0.04, -0.1, -0.95],
  [-0.09, 0.57, -0.13, -0.6, 0.35], [0.09, 0.57, -0.13, -0.6, -0.35],
];

/** Puff centres for the curly style: directions over the upper head, clear of the face. */
const CURLS: Array<[number, number, number]> = (() => {
  const out: Array<[number, number, number]> = [[0, 1, 0]];
  const rings: Array<[number, number, number]> = [[0.5, 6, 0], [1.0, 8, 0.4]];
  for (const [theta, count, offset] of rings) {
    for (let i = 0; i < count; i += 1) {
      const phi = offset + (i / count) * Math.PI * 2;
      const x = Math.sin(theta) * Math.sin(phi);
      const z = Math.sin(theta) * Math.cos(phi);
      if (z > 0.55 && theta > 0.8) continue; // keep the forehead free
      out.push([x, Math.cos(theta), z]);
    }
  }
  return out;
})();

/**
 * Hair under a hat: only the band below the hat's rim, and slightly smaller
 * than the hat shell, so no strand can ever poke through the crown.
 */
function HairUnderHat({ color }: { color: string }) {
  return (
    <mesh geometry={GEO.hairBand} material={matte(color)} position={[0, HD.y - 0.005, -0.004]}
      scale={[HD.rx + 0.016, HD.ry + 0.014, HD.rz + 0.016]} castShadow />
  );
}

function Hair({ style, look }: { style: HairStyle; look: ToyLook }) {
  const hair = matte(look.hair);
  switch (style) {
    case "bald":
      return null;
    case "short":
      return <HairCap color={look.hair} />;
    case "spiky":
      return (
        <group>
          <HairCap color={look.hair} />
          {SPIKES.map(([x, y, z, rx, rz], i) => (
            <mesh key={i} geometry={GEO.spike} material={hair} position={[x, y, z]} rotation={[rx, 0, rz]} castShadow />
          ))}
        </group>
      );
    case "bun":
      return (
        <group>
          <HairCap color={look.hair} />
          <mesh geometry={GEO.sphere} material={hair} position={[0, HD.y + HD.ry + 0.02, -0.1]} scale={0.1} castShadow />
        </group>
      );
    case "curly":
      return (
        <group>
          <HairCap color={look.hair} grow={0.01} />
          {CURLS.map(([x, y, z], i) => (
            <mesh key={i} geometry={GEO.lowSphere} material={hair} castShadow
              position={[x * HD.rx * 0.92, HD.y + y * HD.ry * 0.92 + 0.02, z * HD.rz * 0.72]} scale={0.085} />
          ))}
        </group>
      );
    case "long":
      return (
        <group>
          <HairCap color={look.hair} />
          <mesh geometry={GEO.sphere} material={hair} position={[0, HD.y - 0.1, -0.15]} scale={[0.29, 0.3, 0.125]} castShadow />
          {[-1, 1].map((sx) => (
            <mesh key={sx} geometry={GEO.sphere} material={hair} position={[sx * 0.285, HD.y - 0.1, -0.03]} scale={[0.07, 0.19, 0.1]} castShadow />
          ))}
        </group>
      );
    case "slick":
      // Combed straight back with a little quiff: no fringe, a polished sheen.
      return (
        <group>
          <HairCap color={look.hair} fringe={false} />
          {/* Sunk into the cap at the front hairline, so it reads as swept-up hair rather than a hat. */}
          <mesh geometry={GEO.sphere} material={matte(look.hair, 0.45)} position={[0, HD.y + HD.ry - 0.075, 0.12]} rotation={[-0.75, 0, 0]}
            scale={[0.19, 0.06, 0.11]} castShadow />
        </group>
      );
    case "sidepart":
      return (
        <group>
          <HairCap color={look.hair} fringe={false} />
          {/* One swept wave from the parting across the forehead. */}
          <mesh geometry={GEO.sphere} material={hair} position={[-0.04, HD.y + 0.2, faceZ(-0.04, HD.y + 0.2, 0.04)]} rotation={[0.55, 0, 0.22]}
            scale={[0.22, 0.07, 0.08]} castShadow />
        </group>
      );
    case "buzz":
      // Close-cropped: a thin cap tinted towards the skin, no fringe.
      return <HairCap color={mix(look.hair, look.skin, 0.3)} grow={-0.014} fringe={false} />;
    case "ponytail":
      return (
        <group>
          <HairCap color={look.hair} />
          <mesh geometry={GEO.lowSphere} material={matte(look.shirtAccent)} position={[0, HD.y + 0.06, -(HD.rz + 0.025)]} scale={0.035} />
          <mesh geometry={GEO.sphere} material={hair} position={[0, HD.y - 0.08, -(HD.rz + 0.035)]} rotation={[0.18, 0, 0]}
            scale={[0.06, 0.15, 0.055]} castShadow />
        </group>
      );
    case "beanie": {
      const knit = matte(look.shirtAccent, 0.95);
      return (
        <group>
          <HairUnderHat color={look.hair} />
          <mesh geometry={GEO.dome} material={knit} position={[0, HD.y + 0.01, 0]} rotation={[-0.15, 0, 0]}
            scale={[HD.rx + 0.025, HD.ry + 0.045, HD.rz + 0.028]} castShadow />
          <mesh geometry={GEO.lowSphere} material={knit} position={[0, HD.y + HD.ry + 0.07, -0.03]} scale={0.06} castShadow />
          {[-1, 1].map((sx) => (
            <mesh key={sx} geometry={GEO.lowSphere} material={hair} position={[sx * 0.27, HD.y - 0.02, 0.06]} scale={[0.05, 0.07, 0.06]} />
          ))}
        </group>
      );
    }
    case "cap": {
      const cap = matte(look.shirtAccent, 0.7);
      return (
        <group>
          <HairUnderHat color={look.hair} />
          {/* Clearly larger than the hair band underneath: equal shells z-fight and the hair flickers through. */}
          <mesh geometry={GEO.dome} material={cap} position={[0, HD.y + 0.03, 0]} rotation={[-0.12, 0, 0]}
            scale={[HD.rx + 0.045, HD.ry + 0.0, HD.rz + 0.05]} castShadow />
          <mesh geometry={GEO.brim} material={cap} position={[0, HD.y + 0.07, HD.rz + 0.06]} rotation={[0.14, 0, 0]} castShadow />
          <mesh geometry={GEO.lowSphere} material={cap} position={[0, HD.y + HD.ry + 0.01, -0.02]} scale={0.022} />
        </group>
      );
    }
  }
}

// ---------------------------------------------------------------------------
// Clothes

/** Outfits worn with polished dress shoes (when the shoe colour is dark). */
const DRESS_SHOES: ReadonlySet<OutfitId> = new Set(["suit", "blazer", "leather"]);

/** Outfits whose jacket shows a shirt cuff at the wrist. */
const CUFFED: ReadonlySet<OutfitId> = new Set(["suit", "blazer"]);

/** The material of a garment: leather shines, knits and fleece stay soft. */
function garment(look: ToyLook, color: string): MeshStandardMaterial {
  if (look.outfit === "leather" && color === look.shirt) return matte(color, 0.32, 0.12);
  if (look.outfit === "quarterzip" || look.outfit === "turtleneck" || look.outfit === "hoodie") return matte(color, 0.95);
  return matte(color);
}

/** Front of the chest (torso-local z) at mid height; the rounded box curves back above and below. */
const FRONT = TOY.torso.d / 2;

/**
 * The opening of a jacket or vest: a shirt-coloured V (tip down) with lapels
 * along its edges, tilted back to follow the chest's rounded top.
 */
function VOpening({ look, width, depth, lapel, tie }: { look: ToyLook; width: number; depth: number; lapel: number; tie: boolean }) {
  const top = 0.285;
  const angle = Math.atan2(width / 2, depth);
  const edge = Math.hypot(width / 2, depth);
  const shade = garment(look, mix(look.shirt, "#000000", look.outfit === "leather" ? 0.25 : 0.14));
  return (
    <group position={[0, top - depth / 2, FRONT - 0.012]} rotation={[-0.22, 0, 0]}>
      {/* A cone of radius r has a face r·√3 wide; scaled flat in z it is a triangle on the chest. */}
      <mesh geometry={GEO.wedge} material={matte(look.inner)} rotation={[0, 0, Math.PI]} scale={[width / Math.sqrt(3), depth, 0.03]} />
      {[-1, 1].map((sx) => (
        <mesh key={sx} geometry={GEO.box} material={shade} castShadow
          position={[sx * (width / 4 + lapel * 0.45), 0, 0.02]} rotation={[0, 0, -sx * angle]} scale={[lapel, edge + 0.02, 0.012]} />
      ))}
      {tie && (
        <group position={[0, 0, 0.022]}>
          <mesh geometry={GEO.box} material={matte(look.shirtAccent, 0.5)} position={[0, depth / 2 - 0.018, 0]} scale={[0.03, 0.024, 0.014]} />
          <mesh geometry={GEO.box} material={matte(look.shirtAccent, 0.5)} position={[0, -0.012, 0]} scale={[0.028, depth - 0.05, 0.01]} />
        </group>
      )}
    </group>
  );
}

function Buttons({ count, color }: { count: number; color: string }) {
  return (
    <>
      {Array.from({ length: count }, (_, i) => (
        <mesh key={i} geometry={GEO.lowSphere} material={matte(color, 0.4)} position={[0, 0.105 - i * 0.04, FRONT + 0.004 - i * 0.006]} scale={[0.011, 0.011, 0.006]} />
      ))}
    </>
  );
}

/** Everything an outfit adds to the chest: opening, lapels, tie, collar, hood, zip, pockets. */
function OutfitDetails({ look }: { look: ToyLook }) {
  const accent = matte(look.shirtAccent);
  const dark = mix(look.shirt, "#000000", 0.35);
  switch (look.outfit) {
    case "suit":
      return (
        <group>
          <VOpening look={look} width={0.13} depth={0.17} lapel={0.034} tie />
          <Buttons count={2} color={dark} />
          <mesh geometry={GEO.box} material={accent} position={[0.09, 0.2, FRONT + 0.002]} scale={[0.04, 0.014, 0.01]} />
        </group>
      );
    case "blazer":
      return (
        <group>
          <VOpening look={look} width={0.14} depth={0.16} lapel={0.034} tie={false} />
          <Buttons count={1} color={mix(look.shirtAccent, "#000000", 0.2)} />
          <mesh geometry={GEO.box} material={accent} position={[0.09, 0.2, FRONT + 0.002]} scale={[0.04, 0.014, 0.01]} />
        </group>
      );
    case "leather":
      // A moto-style leather jacket: wide shiny collar and an off-centre zip over a plain tee.
      return (
        <group>
          <VOpening look={look} width={0.16} depth={0.17} lapel={0.05} tie={false} />
          <mesh geometry={GEO.box} material={METAL} position={[-0.055, 0.075, FRONT + 0.003]} scale={[0.008, 0.1, 0.008]} />
          <mesh geometry={GEO.box} material={METAL} position={[0.085, 0.11, FRONT + 0.002]} scale={[0.05, 0.006, 0.008]} />
        </group>
      );
    case "vest":
      // A fleece vest over a button-down: a small shirt V, a centre zip and a chest logo.
      return (
        <group>
          <VOpening look={look} width={0.1} depth={0.09} lapel={0.022} tie={false} />
          <mesh geometry={GEO.box} material={METAL} position={[0, 0.12, FRONT + 0.002]} scale={[0.007, 0.13, 0.008]} />
          <mesh geometry={GEO.box} material={accent} position={[0.075, 0.2, FRONT + 0.002]} scale={[0.04, 0.02, 0.008]} />
        </group>
      );
    case "turtleneck":
      return <mesh geometry={GEO.collar} material={garment(look, look.shirt)} position={[0, 0.29, 0]} scale={[1, 0.075, 1]} castShadow />;
    case "quarterzip":
      return (
        <group>
          <mesh geometry={GEO.collar} material={garment(look, look.shirt)} position={[0, 0.285, 0]} scale={[1.04, 0.055, 1.04]} castShadow />
          <mesh geometry={GEO.box} material={METAL} position={[0, 0.25, FRONT - 0.01]} rotation={[-0.45, 0, 0]} scale={[0.008, 0.09, 0.01]} />
          <mesh geometry={GEO.box} material={METAL} position={[0.012, 0.21, FRONT + 0.004]} scale={[0.012, 0.022, 0.006]} />
        </group>
      );
    case "hoodie":
      return (
        <group>
          {/* The hood lies folded behind the neck. */}
          <mesh geometry={GEO.sphere} material={garment(look, look.shirt)} position={[0, 0.275, -0.07]} scale={[0.15, 0.065, 0.085]} castShadow />
          {[-1, 1].map((sx) => (
            <mesh key={sx} geometry={GEO.box} material={accent} position={[sx * 0.035, 0.22, FRONT - 0.002]} rotation={[-0.3, 0, 0]} scale={[0.009, 0.075, 0.009]} />
          ))}
          <mesh geometry={GEO.pocket} material={garment(look, dark)} position={[0, 0.075, FRONT - 0.006]} />
        </group>
      );
    case "tee":
    default:
      // Chest emblem in the accent colour, like the logo on a T-shirt.
      return <mesh geometry={GEO.sphere} material={accent} position={[0, 0.16, FRONT - 0.004]} scale={[0.05, 0.05, 0.012]} />;
  }
}

function Eyewear({ look }: { look: ToyLook }) {
  if (look.eyewear === "none") return null;
  const eyeY = HD.y - 0.03;
  const shades = look.eyewear === "shades";
  return (
    <group>
      {[-1, 1].map((sx) => {
        const x = sx * 0.1;
        return (
          <group key={sx} position={[x, eyeY, faceZ(x, eyeY) + 0.012]} rotation={[0, sx * 0.4, 0]}>
            <mesh geometry={GEO.lens} material={FRAME} />
            {shades && <mesh geometry={GEO.sphere} material={SHADES} scale={[0.046, 0.046, 0.006]} />}
          </group>
        );
      })}
      <mesh geometry={GEO.box} material={FRAME} position={[0, eyeY + 0.012, faceZ(0, eyeY) + 0.01]} scale={[0.06, 0.01, 0.01]} />
    </group>
  );
}

type JointRef = MutableRefObject<Group | null>;

/**
 * One arm: a rounded shoulder cap that grows out of the chest's edge, a full
 * upper arm and a slightly tapered forearm whose capsule ends overlap into a
 * closed elbow, and a mitten hand with a thumb. A tee shows a short flared
 * sleeve over a bare arm; every other outfit has long sleeves ending in a cuff
 * at the wrist (a vest shows the shirt's sleeves, a suit the shirt cuff).
 */
function Arm({ side, look, shoulder, elbow, holding }: { side: 1 | -1; look: ToyLook; shoulder: JointRef; elbow: JointRef; holding?: ReactNode }) {
  const skin = matte(look.skin);
  const sleeveColour = look.outfit === "vest" ? look.inner : look.shirt;
  const cloth = garment(look, sleeveColour);
  const long = look.outfit !== "tee";
  const cuff = CUFFED.has(look.outfit) ? matte(look.inner) : garment(look, mix(sleeveColour, "#000000", 0.08));
  const hand = -TOY.foreArm - 0.006;
  return (
    <group ref={shoulder} position={[side * TOY.shoulderX, TOY.shoulderY, 0]}>
      {/* Shoulder cap, pulled in towards the chest so it rounds off the torso's corner instead of perching on it. */}
      <mesh geometry={GEO.sphere} material={cloth} position={[-side * 0.008, -0.004, 0]} scale={[TOY.armRadius + 0.012, TOY.armRadius + 0.008, TOY.armRadius + 0.012]} castShadow />
      {!long && <mesh geometry={GEO.sleeve} material={cloth} position={[0, -0.045, 0]} castShadow />}
      <mesh geometry={GEO.upperArm} material={long ? cloth : skin} position={[0, -TOY.upperArm / 2, 0]} castShadow />
      <group ref={elbow} position={[0, -TOY.upperArm, 0]}>
        <mesh geometry={GEO.foreArm} material={long ? cloth : skin} position={[0, -TOY.foreArm / 2 + 0.01, 0]} castShadow />
        {long && <mesh geometry={GEO.cuff} material={cuff} position={[0, hand + 0.052, 0]} />}
        {/* Mitten hand: a soft paddle, palm turned towards the body, with a thumb pointing forward. */}
        <mesh geometry={GEO.sphere} material={skin} position={[0, hand, 0.004]} scale={[TOY.handRadius * 0.82, TOY.handRadius * 1.08, TOY.handRadius * 0.95]} castShadow />
        <mesh geometry={GEO.lowSphere} material={skin} position={[-side * 0.03, hand + 0.018, 0.034]} rotation={[0.5, 0, side * 0.45]} scale={[0.02, 0.03, 0.02]} castShadow />
        {holding && <group position={[0, hand - 0.01, 0.03]}>{holding}</group>}
      </group>
    </group>
  );
}

function Leg({ side, look, hip, knee, ankle }: { side: 1 | -1; look: ToyLook; hip: JointRef; knee: JointRef; ankle: JointRef }) {
  const pants = matte(look.pants);
  // Dress shoes are polished with a dark sole; sneakers keep the light rubber sole.
  const dress = DRESS_SHOES.has(look.outfit) && !isLight(look.shoes);
  const sole = matte(dress ? mix(look.shoes, "#000000", 0.45) : isLight(look.shoes) ? mix(look.shoes, "#9a9aa0", 0.25) : SOLE_LIGHT);
  return (
    <group ref={hip} position={[side * TOY.hipX, TOY.hipUp, 0]}>
      <mesh geometry={GEO.thigh} material={pants} position={[0, -TOY.thigh / 2, 0]} castShadow />
      <group ref={knee} position={[0, -TOY.thigh, 0]}>
        <mesh geometry={GEO.shin} material={pants} position={[0, -TOY.shin / 2 + 0.005, 0]} castShadow />
        <group ref={ankle} position={[0, -TOY.shin, 0]}>
          <mesh geometry={GEO.shoe} material={matte(look.shoes, dress ? 0.3 : 0.6)} position={[0, -TOY.sole + 0.0425, (TOY.toe - TOY.heel) / 2]} castShadow />
          <mesh geometry={GEO.sole} material={sole} position={[0, -TOY.sole + 0.011, (TOY.toe - TOY.heel) / 2]} castShadow />
        </group>
      </group>
    </group>
  );
}

// ---------------------------------------------------------------------------
// Figure

/** Cross-fade time between two modes, seconds. */
const BLEND_S = 0.2;
/** Longest frame step the animation accepts (a tab switch must not jump). */
const MAX_DT = 0.1;

export interface ToyFigureProps {
  look: ToyLook;
  drive: { current: { mode: FigureMode; speed: number } };
  paused: boolean;
  /** Rendered height in metres; the rig is designed at 1.3 m and scales uniformly. */
  heightM?: number;
  /** Seat-top height in metres for the seated modes (sit, work, sleep). */
  seatHeight?: number;
  /** Something held in the right hand (e.g. a dog treat); the arm then stays raised forward. */
  holding?: ReactNode;
}

export function ToyFigure({ look, drive, paused, heightM = TOY_HEIGHT, seatHeight = 0.52, holding }: ToyFigureProps) {
  const holds = useRef(false);
  holds.current = !!holding;
  const scale = heightM / TOY_HEIGHT;
  const pelvis = useRef<Group>(null);
  const torso = useRef<Group>(null);
  const head = useRef<Group>(null);
  const lShoulder = useRef<Group>(null);
  const rShoulder = useRef<Group>(null);
  const lElbow = useRef<Group>(null);
  const rElbow = useRef<Group>(null);
  const lHip = useRef<Group>(null);
  const rHip = useRef<Group>(null);
  const lKnee = useRef<Group>(null);
  const rKnee = useRef<Group>(null);
  const lAnkle = useRef<Group>(null);
  const rAnkle = useRef<Group>(null);

  const state = useMemo(() => ({
    time: Math.random() * 10, // desynchronise idle loops between figures
    phase: 0,
    mode: null as FigureMode | null,
    blend: 1,
    from: createPose(),
    target: createPose(),
    current: createPose(),
    opts: { seatHeight: 0.52, phase: 0 } as PoseOptions & { phase: number },
    settled: false,
  }), []);

  useFrame((_, rawDt) => {
    const s = state;
    const dt = Math.min(rawDt, MAX_DT);
    const { mode, speed } = drive.current;
    const rigSpeed = Math.max(0, speed) / scale;
    const modeChanged = mode !== s.mode;
    if (paused && !modeChanged && s.settled) return;
    if (!paused) {
      s.time += dt;
      if (mode === "walk") s.phase += dt * walkCadence(rigSpeed);
    }
    if (modeChanged) {
      if (s.mode === null) s.blend = 1;
      else { copyPose(s.current, s.from); s.blend = 0; }
      s.mode = mode;
    }
    s.opts.seatHeight = seatHeight / scale;
    s.opts.phase = s.phase;
    poseFor(mode, s.time, rigSpeed, s.opts, s.target);
    if (s.blend < 1) {
      s.blend = Math.min(1, s.blend + dt / BLEND_S);
      const k = s.blend * s.blend * (3 - 2 * s.blend);
      blendPose(s.from, s.target, k, s.current);
    } else {
      copyPose(s.target, s.current);
    }
    s.settled = s.blend >= 1;
    // Holding something: the right arm stays raised forward with a bent elbow, whatever the gait does.
    if (holds.current) {
      s.current.rightShoulder[0] = -1.05; s.current.rightShoulder[1] = 0; s.current.rightShoulder[2] = 0.1;
      s.current.rightElbow = -0.7;
    }
    apply(s.current);
  });

  function apply(p: ToyPose): void {
    pelvis.current?.position.set(0, p.hipY, p.hipZ);
    if (torso.current) {
      torso.current.position.y = TOY.hipUp + p.bob;
      torso.current.rotation.set(p.torsoPitch, p.torsoYaw, p.torsoRoll);
    }
    head.current?.rotation.set(p.headPitch, p.headYaw, p.headRoll);
    lShoulder.current?.rotation.set(p.leftShoulder[0], p.leftShoulder[1], p.leftShoulder[2]);
    rShoulder.current?.rotation.set(p.rightShoulder[0], p.rightShoulder[1], p.rightShoulder[2]);
    if (lElbow.current) lElbow.current.rotation.x = p.leftElbow;
    if (rElbow.current) rElbow.current.rotation.x = p.rightElbow;
    if (lHip.current) lHip.current.rotation.x = p.leftHip;
    if (rHip.current) rHip.current.rotation.x = p.rightHip;
    if (lKnee.current) lKnee.current.rotation.x = p.leftKnee;
    if (rKnee.current) rKnee.current.rotation.x = p.rightKnee;
    if (lAnkle.current) lAnkle.current.rotation.x = p.leftAnkle;
    if (rAnkle.current) rAnkle.current.rotation.x = p.rightAnkle;
  }

  return (
    <group scale={scale}>
      <group ref={pelvis} position={[0, TOY.thigh + TOY.shin + TOY.sole - TOY.hipUp, 0]}>
        <mesh geometry={GEO.pelvis} material={matte(look.pants)} position={[0, TOY.pelvis.h / 2, 0]} castShadow />
        <Leg side={1} look={look} hip={lHip} knee={lKnee} ankle={lAnkle} />
        <Leg side={-1} look={look} hip={rHip} knee={rKnee} ankle={rAnkle} />
        <group ref={torso} position={[0, TOY.hipUp, 0]}>
          <mesh geometry={GEO.chest} material={garment(look, look.shirt)} position={[0, 0.02 + (TOY.torso.h - 0.02) / 2, 0]} castShadow />
          <OutfitDetails look={look} />
          <mesh geometry={GEO.lowSphere} material={matte(look.skin)} position={[0, TOY.neckY - 0.01, 0]} scale={[0.07, 0.04, 0.07]} />
          <Arm side={1} look={look} shoulder={lShoulder} elbow={lElbow} />
          <Arm side={-1} look={look} shoulder={rShoulder} elbow={rElbow} holding={holding} />
          <group ref={head} position={[0, TOY.neckY, 0]}>
            <mesh geometry={GEO.sphere} material={matte(look.skin)} position={[0, HD.y, 0]} scale={[HD.rx, HD.ry, HD.rz]} castShadow />
            <Face look={look} />
            <Eyewear look={look} />
            <Hair style={look.hairStyle} look={look} />
          </group>
        </group>
      </group>
    </group>
  );
}
