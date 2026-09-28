/**
 * The office floor: a wood-and-carpet plate floating in a starry night, glass
 * railings around it, one carpeted department per provider family, the lead's
 * glass office at the back and a lounge at the front.
 */
import { useEffect, useMemo, useRef } from "react";
import { OrbitControls, Stars } from "@react-three/drei";
import { useThree } from "@react-three/fiber";
import { CanvasTexture, Color, RepeatWrapping, SRGBColorSpace, type Texture } from "three";
import { useT } from "@/i18n";
import type { OrbitControls as OrbitControlsImpl } from "three-stdlib";
import type { SocietyAgent } from "../data";
import { Bookshelf, Couch, Desk, MAT, Plant, Railing, Rug, SignWall } from "./OfficeFurniture";
import { OfficeAgents } from "./OfficeAgents";
import { allDesks, type Department, type OfficeLayout } from "./officeLayout";
import { DEPARTMENT_TINTS, OFFICE } from "./officePalette";
import { cameraHome, CAMERA_LIMITS, focusBounds } from "./officeCamera";
import type { ScreenFace } from "./screenTextures";

/** Warm planks drawn once; repeated across the floor at about 1 m per plank run. */
let plankTexture: Texture | null | undefined;
function planks(): Texture | null {
  if (plankTexture !== undefined) return plankTexture;
  const canvas = typeof document !== "undefined" ? document.createElement("canvas") : null;
  const ctx = canvas?.getContext("2d") ?? null;
  plankTexture = null;
  if (canvas && ctx) {
    canvas.width = 256;
    canvas.height = 256;
    const tones = ["#c9a27a", "#c29a71", "#cfa983", "#bf966c"];
    ctx.fillStyle = tones[0];
    ctx.fillRect(0, 0, 256, 256);
    for (let row = 0; row < 8; row += 1) {
      // Staggered joints; segments run past both edges so the tile wraps seamlessly.
      const offset = ((row % 2) * 64 + ((row * 53) % 64)) - 128;
      for (let seg = 0; seg < 4; seg += 1) {
        ctx.fillStyle = tones[(row + seg + 4) % tones.length];
        ctx.fillRect(offset + seg * 128, row * 32, 128, 32);
        ctx.fillStyle = "rgba(90,60,35,0.35)";
        ctx.fillRect(offset + seg * 128, row * 32, 2, 32);
      }
      ctx.fillStyle = "rgba(90,60,35,0.3)";
      ctx.fillRect(0, row * 32, 256, 2);
    }
    const texture = new CanvasTexture(canvas);
    texture.colorSpace = SRGBColorSpace;
    texture.wrapS = texture.wrapT = RepeatWrapping;
    texture.anisotropy = 8;
    plankTexture = texture;
  }
  return plankTexture;
}

function Slab({ layout }: { layout: OfficeLayout }) {
  const { minX, maxX, minZ, maxZ } = layout.bounds;
  const w = maxX - minX, d = maxZ - minZ, cx = (minX + maxX) / 2, cz = (minZ + maxZ) / 2;
  const floorMap = useMemo(() => {
    const base = planks();
    if (!base) return null;
    const map = base.clone();
    map.repeat.set(w / 3, d / 3);
    map.needsUpdate = true;
    return map;
  }, [w, d]);
  return (
    <group>
      <mesh position={[cx, -0.3, cz]} receiveShadow>
        <boxGeometry args={[w, 0.6, d]} />
        <meshStandardMaterial color={OFFICE.slabEdge} roughness={0.9} />
      </mesh>
      {/* Warm wood floor everywhere; departments lay carpet on top. */}
      <mesh position={[cx, 0.001, cz]} rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
        <planeGeometry args={[w, d]} />
        <meshStandardMaterial color={floorMap ? "#ffffff" : OFFICE.walkway} map={floorMap} roughness={0.8} />
      </mesh>
      <Railing from={[minX + 0.2, minZ + 0.2]} to={[maxX - 0.2, minZ + 0.2]} />
      <Railing from={[maxX - 0.2, minZ + 0.2]} to={[maxX - 0.2, maxZ - 0.2]} />
      <Railing from={[maxX - 0.2, maxZ - 0.2]} to={[minX + 0.2, maxZ - 0.2]} />
      <Railing from={[minX + 0.2, maxZ - 0.2]} to={[minX + 0.2, minZ + 0.2]} />
    </group>
  );
}

function deskFace(agentId: string | null, agents: ReadonlyMap<string, SocietyAgent>): ScreenFace {
  const agent = agentId ? agents.get(agentId) : undefined;
  return agent ? agent.state : "empty";
}

function DepartmentArea({ dept, agents }: { dept: Department; agents: ReadonlyMap<string, SocietyAgent> }) {
  const t = useT();
  const w = dept.maxX - dept.minX, d = dept.maxZ - dept.minZ;
  const cx = (dept.minX + dept.maxX) / 2, cz = (dept.minZ + dept.maxZ) / 2;
  const tint = DEPARTMENT_TINTS[dept.tint % DEPARTMENT_TINTS.length];
  return (
    <group>
      <mesh position={[cx, 0.006, cz]} rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
        <planeGeometry args={[w, d]} />
        <meshStandardMaterial color={tint} roughness={0.95} />
      </mesh>
      <SignWall label={dept.label || t("society.office.open_space")} width={w - 0.4} position={[cx, 0, dept.minZ + 0.1]} />
      {dept.desks.map((desk) => (
        <group key={desk.id} position={[desk.x, 0, desk.z]} rotation={[0, desk.facing === "north" ? 0 : Math.PI, 0]}>
          <Desk face={deskFace(desk.agentId, agents)} />
        </group>
      ))}
      <Plant position={[dept.minX + 0.45, 0, dept.maxZ - 0.45]} size={0.9} />
      <Plant position={[dept.maxX - 0.45, 0, dept.maxZ - 0.45]} size={0.9} />
    </group>
  );
}

function LeadOfficeRoom({ layout, agents }: { layout: OfficeLayout; agents: ReadonlyMap<string, SocietyAgent> }) {
  const { minX, maxX, minZ, maxZ } = layout.lead;
  const w = maxX - minX, d = maxZ - minZ, cx = (minX + maxX) / 2, cz = (minZ + maxZ) / 2;
  return (
    <group>
      <mesh position={[cx, 0.006, cz]} rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
        <planeGeometry args={[w, d]} />
        <meshStandardMaterial color={OFFICE.wood} roughness={0.8} />
      </mesh>
      <SignWall label="Lead" width={w - 0.4} position={[cx, 0, minZ + 0.1]} />
      {/* Glass walls on the open sides, with a door gap facing the floor. */}
      <Railing from={[maxX, minZ + 0.3]} to={[maxX, maxZ]} height={2.1} />
      <Railing from={[minX + 0.2, maxZ]} to={[cx - 0.7, maxZ]} height={2.1} />
      <Railing from={[cx + 0.7, maxZ]} to={[maxX, maxZ]} height={2.1} />
      {layout.lead.desks.map((desk) => (
        <group key={desk.id} position={[desk.x, 0, desk.z]} rotation={[0, Math.PI, 0]}>
          <Desk face={deskFace(desk.agentId, agents)} />
        </group>
      ))}
      <Bookshelf position={[maxX - 1.3, 0, minZ + 0.45]} />
      <Plant position={[minX + 0.5, 0, maxZ - 0.5]} size={1.2} />
    </group>
  );
}

function Lounge({ layout }: { layout: OfficeLayout }) {
  const { minX, maxX, minZ, maxZ } = layout.lounge;
  const cx = (minX + maxX) / 2, cz = (minZ + maxZ) / 2;
  return (
    <group>
      <Rug position={[cx - 3, 0.01, cz]} size={[6.5, 4]} />
      <Couch position={[cx - 3, 0, cz - 1]} />
      <Couch position={[cx - 5.4, 0, cz + 0.3]} rotationY={Math.PI / 2} />
      <mesh position={[cx - 3, 0.22, cz + 0.4]} castShadow receiveShadow material={MAT.wood}>
        <cylinderGeometry args={[0.55, 0.55, 0.06, 24]} />
      </mesh>
      <Bookshelf position={[cx + 2.5, 0, maxZ - 0.5]} rotationY={Math.PI} />
      <Bookshelf position={[cx + 4.6, 0, maxZ - 0.5]} rotationY={Math.PI} />
      <Plant position={[minX + 0.6, 0, maxZ - 0.6]} size={1.4} />
      <Plant position={[maxX - 0.6, 0, minZ + 0.6]} size={1.2} />
      <Plant position={[cx + 0.4, 0, cz - 1.2]} size={1.1} />
    </group>
  );
}

function CameraRig({ layout, reset }: { layout: OfficeLayout; reset: number }) {
  const focus = useMemo(() => focusBounds(allDesks(layout).filter((d) => d.agentId)), [layout]);
  const controls = useRef<OrbitControlsImpl>(null);
  const camera = useThree((s) => s.camera);
  const size = useThree((s) => s.size);
  useEffect(() => {
    const home = cameraHome(layout.bounds, size.width / Math.max(1, size.height), focus);
    camera.position.set(...home.position);
    controls.current?.target.set(...home.target);
    controls.current?.update();
    // Dev-only handle for runtime screenshots at chosen angles.
    if (import.meta.env.DEV) (window as unknown as Record<string, unknown>).__office = { camera, controls: controls.current };
    // `size` only matters for the first framing; a resize keeps the user's view.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [camera, layout.bounds, focus, reset]);
  return (
    <OrbitControls ref={controls} makeDefault enableDamping dampingFactor={0.12} screenSpacePanning={false}
      minPolarAngle={CAMERA_LIMITS.minPolar} maxPolarAngle={CAMERA_LIMITS.maxPolar}
      minDistance={CAMERA_LIMITS.minDistance} maxDistance={CAMERA_LIMITS.maxDistance} />
  );
}

export function OfficeScene({ layout, agents, awake, reduced, reset, onSelect }: {
  layout: OfficeLayout; agents: ReadonlyMap<string, SocietyAgent>; awake: boolean; reduced: boolean; reset: number; onSelect: (id: string) => void;
}) {
  const desks = useMemo(() => allDesks(layout), [layout]);
  const background = useMemo(() => new Color(OFFICE.space), []);
  const { minX, maxX, minZ, maxZ } = layout.bounds;
  const span = Math.max(maxX - minX, maxZ - minZ);
  return (
    <>
      <primitive attach="background" object={background} />
      <fog attach="fog" args={[OFFICE.space, span * 2.2, span * 4.5]} />
      <Stars radius={span * 3} depth={span} count={2500} factor={4} saturation={0} fade speed={reduced ? 0 : 0.3} />
      <hemisphereLight args={["#dfe9ff", "#6b5a48", 0.9]} />
      <ambientLight intensity={0.25} />
      <directionalLight position={[maxX + 10, 26, maxZ + 6]} intensity={1.6} castShadow
        shadow-mapSize={[2048, 2048]} shadow-bias={-0.0004} shadow-normalBias={0.03}
        shadow-camera-left={-span * 0.7} shadow-camera-right={span * 0.7}
        shadow-camera-top={span * 0.7} shadow-camera-bottom={-span * 0.7} shadow-camera-far={120} />
      <Slab layout={layout} />
      <LeadOfficeRoom layout={layout} agents={agents} />
      {layout.departments.map((dept) => <DepartmentArea key={dept.id} dept={dept} agents={agents} />)}
      <Lounge layout={layout} />
      <OfficeAgents desks={desks} agents={agents} awake={awake} reduced={reduced} onSelect={onSelect} />
      <CameraRig layout={layout} reset={reset} />
    </>
  );
}
