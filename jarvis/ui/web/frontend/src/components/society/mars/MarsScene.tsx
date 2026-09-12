import { useCallback, useEffect, useMemo, useRef, useState, type RefObject } from "react";
import { useFrame, useThree, type ThreeEvent } from "@react-three/fiber";
import { Html, OrbitControls } from "@react-three/drei";
import { Group, Mesh, Object3D, PerspectiveCamera, Vector3 } from "three";
import { useT } from "@/i18n";
import { roadOrientation } from "./roadGeometry";
import { OutpostReference } from "./OutpostReference";
import type { OrbitControls as OrbitControlsInstance } from "three-stdlib";
import { advancePlayer, createPlayer } from "./controller";
import { bindPlayerInput, NO_INPUT } from "./input";
import { avoidCameraCollision, fitWorldBounds, MAX_POLAR, MIN_POLAR } from "./camera";
import {
  BUILDING_COLLIDERS, createTerrainGeometry, OUTPOST, outpostBounds, PLAYER_SPAWN,
  ROADS, terrainHeight, WORLD, WORLD_BOUNDS, type Collider, type Road, type Vec3,
} from "./world";

export type CameraMode = "overview" | "outpost" | "orbit" | "player";
export interface MarsSceneProps {
  hostRef: RefObject<HTMLDivElement>;
  mode: CameraMode;
  neutral: boolean;
  awake: boolean;
  selected: string | null;
  onSelect: (id: string) => void;
  onOrbit: () => void;
  onOpenStation?: () => void;
  reset: number;
}

function ColliderMesh({ collider, onClick }: { collider: Collider; onClick?: (event: ThreeEvent<MouseEvent>) => void }) {
  const size = collider.max.map((v, index) => v - collider.min[index]) as Vec3;
  const position = collider.min.map((v, index) => v + size[index] / 2) as Vec3;
  return (
    <mesh position={position} onClick={onClick} castShadow receiveShadow>
      <boxGeometry args={size} />
      <meshStandardMaterial color={collider.id.startsWith("operations") ? "#c1beb0" : "#b3a998"} roughness={0.95} />
    </mesh>
  );
}

function RoadMesh({ road }: { road: Road }) {
  const midpoint = road.start.map((v, i) => (v + road.end[i]) / 2) as Vec3;
  const delta = new Vector3(...road.end).sub(new Vector3(...road.start));
  const length = delta.length();
  const orientation = roadOrientation(road.start, road.end);
  const bridge = road.kind === "bridge";
  const supports = bridge ? [0.2, 0.5, 0.8] : [];
  return (
    <group>
      <group position={midpoint} quaternion={orientation}>
        <mesh position={[0, -0.27, 0]} receiveShadow castShadow={bridge}>
          <boxGeometry args={[road.width, 0.7, length + 0.12]} />
          <meshStandardMaterial color={bridge ? "#787975" : "#9e8c70"} roughness={0.92} />
        </mesh>
        {bridge && [-1, 1].map((side) => (
          <mesh key={side} position={[side * (road.width / 2 - 0.12), 0.75, 0]} castShadow>
            <boxGeometry args={[0.24, 1.35, length]} />
            <meshStandardMaterial color="#666a65" roughness={0.85} />
          </mesh>
        ))}
      </group>
      {supports.map((t) => {
        const x = road.start[0] + (road.end[0] - road.start[0]) * t;
        const z = road.start[2] + (road.end[2] - road.start[2]) * t;
        const deck = road.start[1] + (road.end[1] - road.start[1]) * t - 0.62;
        const floor = terrainHeight(x, z);
        if (deck <= floor) return null;
        return (
          <mesh key={t} position={[x, (deck + floor) / 2, z]} castShadow receiveShadow>
            <boxGeometry args={[2.4, deck - floor, 2.4]} />
            <meshStandardMaterial color="#77776e" roughness={1} />
          </mesh>
        );
      })}
    </group>
  );
}

function Terrain() {
  const geometry = useMemo(createTerrainGeometry, []);
  useEffect(() => () => geometry.dispose(), [geometry]);
  return <mesh geometry={geometry} receiveShadow><meshStandardMaterial vertexColors roughness={1} /></mesh>;
}

/** Static blockout is deliberately separate from input and per-frame simulation. */
function ColonyBlockout({ onSelect, outpostReady }: { onSelect: (id: string) => void; outpostReady: boolean }) {
  const t = useT();
  return (
    <group>
      <Terrain />
      {ROADS.filter((road) => !outpostReady || road.id !== "route-01").map((road) => <RoadMesh key={road.id} road={road} />)}
      {BUILDING_COLLIDERS.filter((collider) => !outpostReady || !collider.id.startsWith("outpost:")).map((collider) => (
        <ColliderMesh key={collider.id} collider={collider} onClick={(event) => {
          if (event.delta > 4) return;
          event.stopPropagation(); onSelect(collider.id.startsWith("outpost:operations-") ? "operations" : collider.id.replace("outpost:", ""));
        }} />
      ))}
      {/* Neutral massing markers reserve every district, including the northern skyline. */}
      {WORLD.districts.filter((district) => !WORLD.buildings.some((building) => building.district_id === district.id)).map((district) => (
        <mesh key={district.id} position={[district.center[0], district.center[1] + district.landmark_height / 2, district.center[2]]}>
          <boxGeometry args={[district.size[0] * 0.28, district.landmark_height, district.size[1] * 0.28]} />
          <meshStandardMaterial color="#ac9c83" roughness={1} wireframe />
        </mesh>
      ))}
      {WORLD.districts.map((district) => (
        <Html key={district.id} center position={[district.center[0], district.center[1] + district.landmark_height + 10, district.center[2]]} zIndexRange={[12, 0]} style={{ pointerEvents: "none" }}>
          <div className="mars-district-label"><strong>{district.name}</strong><span>{t(district.id === "communications-outpost" && outpostReady ? "society.mars.reference_pending" : "society.mars.district_blockout")}</span></div>
        </Html>
      ))}
      <Html center position={[294, 61.8, 74]} zIndexRange={[12, 0]} style={{ pointerEvents: "none" }}>
        <div className="mars-door-label">{t("society.mars.entrance")}</div>
      </Html>
      <mesh position={[294, 58.6, 63]}><boxGeometry args={[2, 1.1, 0.8]} /><meshStandardMaterial color="#ceab56" roughness={0.7} /></mesh>
    </group>
  );
}

export function MarsScene({ hostRef, mode, neutral, awake, selected, onSelect, onOrbit, onOpenStation, reset }: MarsSceneProps) {
  const sunTarget = useMemo(() => {
    const target = new Object3D(); target.position.set(320, 58, 50); return target;
  }, []);
  const [outpostReady, setOutpostReady] = useState(false);
  const referenceReady = useCallback((ready: boolean) => {
    setOutpostReady(ready);
    if (hostRef.current) hostRef.current.dataset.marsReference = ready ? "loaded-unapproved" : "unavailable";
  }, [hostRef]);
  const controls = useRef<OrbitControlsInstance>(null);
  const figure = useRef<Group>(null);
  const marker = useRef<Mesh>(null);
  const player = useRef(createPlayer());
  const input = useRef<ReturnType<typeof bindPlayerInput> | null>(null);
  const frameCount = useRef(0), lastTelemetry = useRef(0);
  const { camera, size, invalidate, gl } = useThree();
  const orbitYaw = useRef(0.7);
  const followHeight = useRef(2.15);

  useEffect(() => {
    const host = hostRef.current;
    if (!host || mode !== "player" || !awake) { input.current?.clear(); return; }
    const binding = bindPlayerInput(host, invalidate, () => { host.blur(); }, (dx, dy) => {
      orbitYaw.current -= dx * 0.005;
      followHeight.current = Math.max(0.5, Math.min(7, followHeight.current + dy * 0.025));
    }, () => {
      const [x, y, z] = player.current.position;
      if (Math.hypot(x - 294, y - 58, z - 64) <= 4) onOpenStation?.();
    });
    input.current = binding;
    return () => { binding.dispose(); input.current = null; };
  }, [hostRef, mode, awake, invalidate, onOpenStation]);

  useEffect(() => {
    const value = controls.current;
    if (!value || !(camera instanceof PerspectiveCamera)) return;
    if (mode === "player") {
      orbitYaw.current = Math.atan2(camera.position.x - value.target.x, camera.position.z - value.target.z);
      invalidate(); return;
    }
    if (mode === "orbit") return;
    const bounds = mode === "overview" ? WORLD_BOUNDS : outpostBounds();
    const frame = fitWorldBounds(bounds, size.width / Math.max(1, size.height), WORLD.view.overview_padding);
    camera.position.fromArray(frame.position);
    camera.far = Math.max(8000, frame.distance * 4);
    camera.updateProjectionMatrix();
    value.target.fromArray(frame.target);
    value.update(); invalidate();
  }, [camera, size.width, size.height, mode, reset, invalidate]);

  useEffect(() => {
    const value = controls.current;
    const building = WORLD.buildings.find((item) => item.id === selected);
    if (!value || !building) return;
    const [x, y, z] = building.position, [w, h, d] = building.size;
    // Aim outside the facade rather than putting the orbit pivot inside solid geometry.
    value.target.set(x, y + Math.min(2.4, h / 2), z + d / 2 + 1.2);
    camera.position.set(x + w * 0.6, y + h + 12, z + d + 24);
    value.update(); invalidate();
  }, [selected, camera, invalidate]);

  useFrame(({ clock }, delta) => {
    const value = controls.current;
    if (!value || !awake) return;
    const command = mode === "player" ? input.current?.read() ?? NO_INPUT : NO_INPUT;
    const steps = advancePlayer(player.current, command, orbitYaw.current, delta);
    if (steps > 0) input.current?.consume();
    if (figure.current) { figure.current.position.fromArray(player.current.position); figure.current.rotation.y = player.current.yaw; }
    if (marker.current) marker.current.visible = mode !== "player";
    if (mode === "player") {
      const [x, y, z] = player.current.position;
      const target: Vec3 = [x, y + 1.45, z];
      const desired: Vec3 = [x + Math.sin(orbitYaw.current) * 6, y + 1.45 + followHeight.current, z + Math.cos(orbitYaw.current) * 6];
      const position = avoidCameraCollision(target, desired);
      camera.position.fromArray(position);
      value.target.fromArray(target); camera.lookAt(...target);
    } else {
      value.target.x = Math.max(WORLD_BOUNDS.min[0], Math.min(WORLD_BOUNDS.max[0], value.target.x));
      value.target.z = Math.max(WORLD_BOUNDS.min[2], Math.min(WORLD_BOUNDS.max[2], value.target.z));
      value.target.y = Math.max(terrainHeight(value.target.x, value.target.z) + 1, value.target.y);
      const safe = avoidCameraCollision(value.target.toArray() as Vec3, camera.position.toArray() as Vec3);
      camera.position.fromArray(safe); camera.lookAt(value.target);
    }
    frameCount.current++;
    if (clock.elapsedTime - lastTelemetry.current > 0.25) {
      lastTelemetry.current = clock.elapsedTime;
      const host = hostRef.current;
      if (host) {
        host.dataset.marsPlayer = player.current.position.map((v) => v.toFixed(3)).join(",");
        host.dataset.marsGrounded = String(player.current.grounded);
        host.dataset.marsFrames = String(frameCount.current);
        host.dataset.marsDrawCalls = String(gl.info.render.calls);
        host.dataset.marsTriangles = String(gl.info.render.triangles);
        host.dataset.marsCamera = camera.position.toArray().map((v) => v.toFixed(2)).join(",");
      }
    }
    // Reduced-motion scenes redraw only for deliberate input, gravity or camera interaction.
    if (mode === "player" && (command.forward || command.right || command.jump || !player.current.grounded)) invalidate();
  });

  return (
    <>
      <color attach="background" args={[neutral ? "#d0d1cd" : "#b8a394"]} />
      <hemisphereLight args={[neutral ? "#ffffff" : "#e4d6c0", "#584333", neutral ? 1.25 : 0.85]} />
      <primitive object={sunTarget} />
      <directionalLight target={sunTarget} position={[OUTPOST.center[0] - 140, 230, 180]} intensity={neutral ? 2 : 3.4} color={neutral ? "#ffffff" : "#ffdfb0"} castShadow shadow-mapSize={[2048, 2048]} shadow-camera-left={-170} shadow-camera-right={170} shadow-camera-top={170} shadow-camera-bottom={-170} shadow-camera-near={1} shadow-camera-far={650} shadow-normalBias={0.06} />
      <ColonyBlockout onSelect={onSelect} outpostReady={outpostReady} />
      <OutpostReference onSelect={onSelect} onReady={referenceReady} />
      <group ref={figure} position={PLAYER_SPAWN}>
        <mesh position={[0, 0.9, 0]} castShadow><capsuleGeometry args={[0.32, 1.15, 5, 12]} /><meshStandardMaterial color="#f0c75c" roughness={0.8} /></mesh>
        <mesh position={[0, 1.25, 0.31]}><boxGeometry args={[0.32, 0.15, 0.1]} /><meshStandardMaterial color="#393a38" /></mesh>
        <mesh ref={marker} position={[0, 4, 0]}><coneGeometry args={[0.75, 2, 8]} /><meshBasicMaterial color="#f0c75c" /></mesh>
      </group>
      <OrbitControls
        ref={controls} makeDefault enabled={mode !== "player" && awake} enableDamping={false}
        minPolarAngle={MIN_POLAR} maxPolarAngle={MAX_POLAR} minDistance={WORLD.view.near_inspection_m}
        maxDistance={20000} screenSpacePanning={false} onStart={onOrbit}
      />
    </>
  );
}
