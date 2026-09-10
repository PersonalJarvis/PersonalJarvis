/** Isolated city reference. Existing catalogs and saved island positions remain intact. */
import {
  Suspense,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { Html, OrbitControls, useGLTF } from "@react-three/drei";
import { useCityReducedMotion } from "./cityMotion";
import {
  AnimationMixer,
  Color,
  Group,
  Mesh,
  PMREMGenerator,
  Vector3,
  type DirectionalLight,
  type HemisphereLight,
} from "three";
import { mergeGeometries } from "three/examples/jsm/utils/BufferGeometryUtils.js";
import { RoomEnvironment } from "three/examples/jsm/environments/RoomEnvironment.js";
import type { OrbitControls as OrbitControlsType } from "three-stdlib";
import { useCanvasAwake } from "@/hooks/useCanvasAwake";
import { useWebglSurface } from "@/hooks/useWebglSurface";
import { useWebglSupported } from "@/lib/graphDimension";
import { useLocaleChunk, useT } from "@/i18n";
import { useSocietyRoster } from "../data";
import { FigureRig, type FigureDrive } from "../figures/FigureRig";
import type { FigureRecipe } from "../figures/figureRecipe";
import type { PlaceId } from "../world/islandLayout";
import {
  CITY,
  DISTRICTS,
  MATERIALS,
  PLOTS,
  checkpointStop,
  stopX,
  validPlacement,
  type Stop,
  type Vec3,
} from "./cityModel";
import {
  addCitizen,
  advanceHour,
  createSimulation,
  daylight,
  previewCrossing,
  safeDestination,
  setTarget,
  stepSimulation,
  trainAt,
  type CitySimulation,
  type Citizen,
} from "./citySimulation";
import stationUrl from "../../../../../../../../art/studies/city-realism-study/exports/station.glb";
import buildingUrl from "../../../../../../../../art/studies/city-realism-study/exports/building.glb";
import trainUrl from "../../../../../../../../art/studies/city-realism-study/exports/train.glb";
import treeUrl from "../../../../../../../../art/studies/city-realism-study/exports/tree.glb";
import citizenUrl from "../../../../../../../../art/studies/city-realism-study/exports/citizen.glb";

interface Props {
  topRight?: ReactNode;
  onOpenLedger: () => void;
  onSelectAgent?: (id: string | null) => void;
  onSelectPlace?: (place: PlaceId) => void;
}
interface Actor {
  id: string;
  label: string;
  recipe?: FigureRecipe | null;
  checkpoint: string;
  state: string;
}
interface Metrics {
  fps: number;
  calls: number;
  triangles: number;
  geometries: number;
  textures: number;
}
const INITIAL_METRICS: Metrics = {
  fps: 0,
  calls: 0,
  triangles: 0,
  geometries: 0,
  textures: 0,
};

function Asset({
  url,
  ...props
}: {
  url: string;
  position?: Vec3;
  rotation?: Vec3;
  scale?: number;
  onClick?: () => void;
}) {
  const { scene } = useGLTF(url);
  // Reference structures have no animated subnodes. Merge by material to keep
  // the artist's authored detail without one draw call for every facade strip.
  const clone = useMemo(() => {
    scene.updateMatrixWorld(true);
    const groups = new Map<Mesh["material"], Mesh[]>();
    scene.traverse((o) => {
      if (o instanceof Mesh) {
        const list = groups.get(o.material) ?? [];
        list.push(o);
        groups.set(o.material, list);
      }
    });
    const result = new Group();
    for (const [material, meshes] of groups) {
      const pieces = meshes.map((mesh) =>
        mesh.geometry.clone().applyMatrix4(mesh.matrixWorld),
      );
      const geometry = mergeGeometries(pieces);
      pieces.forEach((piece) => piece.dispose());
      if (!geometry)
        throw new Error("Reference asset has incompatible geometry attributes");
      const mesh = new Mesh(geometry, material);
      mesh.castShadow = true;
      mesh.receiveShadow = true;
      result.add(mesh);
    }
    return result;
  }, [scene]);
  useEffect(
    () => () => {
      clone.traverse((o) => {
        if (o instanceof Mesh) o.geometry.dispose();
      });
    },
    [clone],
  );
  return <primitive object={clone} {...props} dispose={null} />;
}
function Solid({
  at,
  size,
  color = MATERIALS.concrete,
}: {
  at: Vec3;
  size: Vec3;
  color?: string;
}) {
  return (
    <mesh position={at} receiveShadow castShadow>
      <boxGeometry args={size} />
      <meshStandardMaterial color={color} roughness={0.8} />
    </mesh>
  );
}
function Connection({
  a,
  b,
  width,
  color,
}: {
  a: Vec3;
  b: Vec3;
  width: number;
  color: string;
}) {
  const direction = new Vector3(...b).sub(new Vector3(...a));
  const center = new Vector3(...a).add(new Vector3(...b)).multiplyScalar(0.5);
  const group = useRef<Group>(null);
  useEffect(() => {
    group.current?.quaternion.setFromUnitVectors(
      new Vector3(0, 1, 0),
      direction.clone().normalize(),
    );
  }, [a[0], a[1], a[2], b[0], b[1], b[2]]);
  return (
    <group ref={group} position={center}>
      <Solid
        at={[0, 0, 0]}
        size={[width, direction.length(), width]}
        color={color}
      />
    </group>
  );
}
function District({
  placements,
  onPlace,
}: {
  placements: Record<Stop, Vec3>;
  onPlace: (stop: Stop) => void;
}) {
  const t = useT();
  return (
    <>
      <Solid
        at={[0, -1.3, 0]}
        size={[CITY.width, 2, CITY.depth]}
        color="#6d7468"
      />
      <Solid at={[0, -0.06, 40]} size={[590, 0.1, 12]} color="#535c60" />
      <Solid
        at={[0, -0.03, 43]}
        size={[590, 0.08, 0.15]}
        color={MATERIALS.light}
      />
      <Solid
        at={[0, 10.9, 0]}
        size={[276, 1, 5.5]}
        color={MATERIALS.graphite}
      />
      {[-1.7, 1.7].map((z) => (
        <Solid
          key={z}
          at={[0, 11.65, z]}
          size={[276, 0.15, 0.12]}
          color={MATERIALS.metal}
        />
      ))}
      {[-120, -80, -40, 0, 40, 80, 120].map((x) => (
        <Connection
          key={x}
          a={[x - 3, 0, 0]}
          b={[x + 3, 10.4, 0]}
          width={1}
          color={MATERIALS.graphite}
        />
      ))}
      <Solid at={[0, 19.6, 20]} size={[6, 0.8, 120]} />
      {[-3, 3].map((x) => (
        <Solid
          key={x}
          at={[x, 20.7, 20]}
          size={[0.16, 1.4, 120]}
          color={MATERIALS.metal}
        />
      ))}
      <Solid at={[0, 10, 80]} size={[4, 20, 4]} color={MATERIALS.glass} />
      {(["west", "east"] as Stop[]).map((s) => (
        <group key={s}>
          <Solid at={[stopX(s), -0.15, 69]} size={[76, 0.3, 116]} />
          <Asset url={stationUrl} position={[stopX(s), 12, 0]} />
          <Solid at={[stopX(s), 11.75, 15]} size={[6, 0.5, 6]} />
          <mesh position={[stopX(s), 6, 16]}>
            <boxGeometry args={[4, 12, 4]} />
            <meshStandardMaterial
              color={MATERIALS.glass}
              transparent
              opacity={0.18}
              roughness={0.2}
              depthWrite={false}
            />
          </mesh>
          {[-2, 2].map((offset) => (
            <Solid
              key={offset}
              at={[stopX(s) + offset, 6, 16]}
              size={[0.15, 12, 4]}
              color={MATERIALS.metal}
            />
          ))}
          <Asset
            url={buildingUrl}
            position={placements[s]}
            rotation={[0, Math.PI, 0]}
            onClick={() => onPlace(s)}
          />
          <Html
            zIndexRange={[1, 0]}
            position={[stopX(s), 23, 7]}
            center
            distanceFactor={180}
            style={{ pointerEvents: "none" }}
          >
            <span className="whitespace-nowrap rounded border border-border bg-popover px-3 py-1 text-xs text-popover-foreground">
              {t(`society.city.${s}`)}
            </span>
          </Html>
          {[-1, 1].flatMap((side) =>
            [24, 48, 72, 112].map((z) => (
              <Asset
                key={`${side}:${z}`}
                url={treeUrl}
                position={[stopX(s) + side * 32, 0, z]}
              />
            )),
          )}
        </group>
      ))}
      {DISTRICTS.slice(2).map((d) => (
        <group key={d.id} position={[d.x, 0, d.z]}>
          {[0, 1, 2].map((i) => (
            <Solid
              key={i}
              at={[i * 55 - 55, 25 + i * 12, 0]}
              size={[32, 50 + i * 24, 34]}
              color={MATERIALS.graphite}
            />
          ))}
          <Html
            zIndexRange={[1, 0]}
            position={[0, 110, 0]}
            center
            style={{ pointerEvents: "none" }}
          >
            <span className="whitespace-nowrap rounded bg-popover px-2 py-1 text-xs text-popover-foreground">
              {t(`society.city.${d.id}`)} · {t("society.city.blockout")}
            </span>
          </Html>
        </group>
      ))}
    </>
  );
}

function ReferenceCitizen({
  citizen,
  paused,
  detailed,
}: {
  citizen: Citizen;
  paused: boolean;
  detailed: { current: boolean };
}) {
  const { scene, animations } = useGLTF(citizenUrl);
  const clone = useMemo(() => scene.clone(true), [scene]);
  const mixer = useMemo(() => new AnimationMixer(clone), [clone]);
  const wasMoving = useRef(false);
  useEffect(() => {
    for (const clip of animations) mixer.clipAction(clip).play();
    return () => {
      mixer.stopAllAction();
      mixer.uncacheRoot(clone);
    };
  }, [animations, clone, mixer]);
  useFrame((_, dt) => {
    const moving = !paused && detailed.current && citizen.speed > 0;
    if (moving) mixer.update((dt * citizen.speed) / 1.28);
    else if (wasMoving.current) mixer.setTime(0);
    wasMoving.current = moving;
  });
  return <primitive object={clone} dispose={null} />;
}
function MovingActor({
  actor,
  sim,
  paused,
  selected,
  onClick,
}: {
  actor: Actor;
  sim: CitySimulation;
  paused: boolean;
  selected: boolean;
  onClick: () => void;
}) {
  const root = useRef<Group>(null);
  const body = useRef<Group>(null);
  const distant = useRef<Mesh>(null);
  const detailed = useRef(true);
  const drive = useRef<FigureDrive>({ mode: "idle", speed: 0 });
  const citizen = sim.citizens.get(actor.id)!;
  useFrame(({ camera }) => {
    if (!root.current) return;
    root.current.position.set(...citizen.position);
    root.current.rotation.y = citizen.heading;
    detailed.current = selected || camera.position.distanceTo(root.current.position) < 80;
    if (body.current) body.current.visible = detailed.current;
    if (distant.current) distant.current.visible = !detailed.current;
    drive.current.mode = citizen.speed > 0 ? "walk" : "idle";
    drive.current.speed = citizen.speed;
  });
  return (
    <group
      ref={root}
      position={citizen.position}
      onClick={(event) => {
        event.stopPropagation();
        onClick();
      }}
    >
      <group ref={body}>
        {actor.recipe ? (
          <FigureRig recipe={actor.recipe} drive={drive} paused={paused} />
        ) : (
          <ReferenceCitizen citizen={citizen} paused={paused} detailed={detailed} />
        )}
      </group>
      <mesh ref={distant} position={[0, 0.85, 0]} visible={false}>
        <capsuleGeometry args={[0.24, 1.2, 3, 6]} />
        <meshStandardMaterial color="#438c9e" roughness={0.8} />
      </mesh>
      {selected && (
        <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.03, 0]}>
          <ringGeometry args={[0.7, 0.85, 24]} />
          <meshBasicMaterial color="#e7c56b" />
        </mesh>
      )}
      {selected && (
        <Html zIndexRange={[1, 0]} position={[0, 2.5, 0]} center>
          <span className="whitespace-nowrap rounded bg-popover px-2 py-1 text-xs text-popover-foreground">
            {actor.label}
          </span>
        </Html>
      )}
    </group>
  );
}

function Runtime({
  sim,
  hour,
  paused,
  awake,
  follow,
  overview,
  onMetrics,
}: {
  sim: CitySimulation;
  hour: number;
  paused: boolean;
  awake: boolean;
  follow: string | null;
  overview: number;
  onMetrics: (m: Metrics) => void;
}) {
  const train = useRef<Group>(null);
  const sun = useRef<DirectionalLight>(null);
  const sky = useRef<HemisphereLight>(null);
  const controls = useRef<OrbitControlsType>(null);
  const { scene, camera, gl, invalidate } = useThree();
  const lastTime = useRef(performance.now());
  const sample = useRef({ seconds: 0, frames: 0 });
  const colors = useMemo(
    () => ({ night: new Color("#263449"), day: new Color("#afc9da") }),
    [],
  );
  useEffect(() => {
    // A local reflection environment keeps metal/glass legible without a network HDRI.
    const room = new RoomEnvironment();
    const generator = new PMREMGenerator(gl);
    const target = generator.fromScene(room, 0.04);
    const previous = scene.environment;
    scene.environment = target.texture;
    room.dispose(); generator.dispose(); invalidate();
    return () => { scene.environment = previous; target.dispose(); };
  }, [gl, scene, invalidate]);
  useEffect(() => {
    lastTime.current = performance.now();
    invalidate();
  }, [paused, awake, invalidate]);
  useEffect(() => {
    camera.position.set(200, 140, 240);
    controls.current?.target.set(0, 0, 45);
    controls.current?.update();
    invalidate();
  }, [overview, camera, invalidate]);
  useEffect(() => {
    const citizen = follow ? sim.citizens.get(follow) : null;
    if (citizen && controls.current) {
      const [x, y, z] = citizen.position;
      controls.current.target.set(x, y + 1, z);
      camera.position.set(x + 14, y + 10, z - 18);
      controls.current.update();
      invalidate();
    }
  }, [follow, camera, sim, invalidate]);
  useFrame((_, dt) => {
    const now = performance.now();
    if (!paused)
      stepSimulation(sim, Math.max(0, (now - lastTime.current) / 1000));
    lastTime.current = now;
    const currentTrain = trainAt(sim.time);
    train.current?.position.set(...currentTrain.position);
    const strength = daylight(hour);
    scene.background = colors.night.clone().lerp(colors.day, strength);
    scene.environmentIntensity = 0.2 + strength * 0.6;
    if (sun.current) {
      sun.current.intensity = 0.25 + strength * 2.5;
      sun.current.position.set(
        Math.cos((hour / 24) * Math.PI * 2) * 180,
        60 + strength * 220,
        120,
      );
    }
    if (sky.current) sky.current.intensity = 0.65 + strength * 1.2;
    if (follow && controls.current) {
      const citizen = sim.citizens.get(follow);
      if (citizen) {
        const target = new Vector3(...citizen.position);
        camera.position.add(target.clone().sub(controls.current.target));
        controls.current.target.copy(target);
        controls.current.update();
      }
    }
    sample.current.seconds += dt;
    sample.current.frames++;
    if (sample.current.seconds >= 1) {
      onMetrics({
        fps: Math.round(sample.current.frames / sample.current.seconds),
        calls: gl.info.render.calls,
        triangles: gl.info.render.triangles,
        geometries: gl.info.memory.geometries,
        textures: gl.info.memory.textures,
      });
      sample.current = { seconds: 0, frames: 0 };
    }
  }, -1);
  return (
    <>
      <hemisphereLight ref={sky} args={["#c6d8e9", "#69695a", 1.4]} />
      <directionalLight
        ref={sun}
        castShadow
        position={[100, 180, 120]}
        intensity={2}
        shadow-mapSize={[1024, 1024]}
        shadow-camera-left={-220}
        shadow-camera-right={220}
        shadow-camera-top={200}
        shadow-camera-bottom={-200}
        shadow-camera-far={700}
        shadow-bias={-0.0005}
      />
      <OrbitControls
        ref={controls}
        makeDefault
        target={[0, 0, 45]}
        minDistance={5}
        maxDistance={1800}
        maxPolarAngle={Math.PI / 2 - 0.02}
        enableDamping={!paused}
      />
      <group ref={train}>
        <Asset url={trainUrl} />
      </group>
    </>
  );
}

export function CityStage({
  topRight,
  onOpenLedger,
  onSelectAgent,
  onSelectPlace,
}: Props) {
  const t = useT();
  const ready = useLocaleChunk("society");
  const host = useRef<HTMLDivElement>(null);
  const { generation } = useWebglSurface(host);
  const awake = useCanvasAwake(host);
  const webgl = useWebglSupported();
  const reduced = useCityReducedMotion();
  const roster = useSocietyRoster();
  const sim = useMemo(createSimulation, []);
  const [demo, setDemo] = useState(false);
  const [count, setCount] = useState(1);
  const [selected, setSelected] = useState<string | null>(null);
  const [follow, setFollow] = useState<string | null>(null);
  const [overview, setOverview] = useState(0);
  const [hour, setHour] = useState(10);
  const [dayPaused, setDayPaused] = useState(false);
  const [metrics, setMetrics] = useState(INITIAL_METRICS);
  const [tick, setTick] = useState(0);
  const [activePlot, setActivePlot] = useState<Stop | null>(null);
  const [placementError, setPlacementError] = useState(false);
  const [placements, setPlacements] = useState<Record<Stop, Vec3>>({
    west: [-120, 0, 104],
    east: [120, 0, 104],
  });
  const actors = useMemo<Actor[]>(
    () =>
      demo
        ? Array.from({ length: count }, (_, i) => ({
            id: `reference-${i}`,
            label: `${t("society.city.reference_agent")} ${i + 1}`,
            checkpoint: "idle",
            state: "demo",
          }))
        : (roster.data?.agents ?? []).map((a) => ({
            id: a.agentId,
            label: a.name,
            recipe: a.figure,
            checkpoint: a.checkpoint,
            state: a.state,
          })),
    [demo, count, roster.data, t],
  );
  // Reconcile outside the render loop; a changed checkpoint replaces intent, not position.
  const signatures = useRef(new Map<string, string>());
  for (const actor of actors)
    if (!sim.citizens.has(actor.id))
      addCitizen(sim, actor.id, checkpointStop(actor.checkpoint) ?? "west");
  useEffect(() => {
    const ids = new Set(actors.map((a) => a.id));
    for (const id of sim.citizens.keys())
      if (!ids.has(id)) {
        sim.citizens.delete(id);
        signatures.current.delete(id);
      }
    for (const actor of actors) {
      const signature = `${actor.checkpoint}:${actor.state}`;
      if (signatures.current.get(actor.id) === signature) continue;
      signatures.current.set(actor.id, signature);
      const citizen = sim.citizens.get(actor.id)!;
      const target = checkpointStop(actor.checkpoint);
      if (target) setTarget(sim, actor.id, target, citizen.revision + 1);
      else if (!demo && citizen.stage !== "arrived") {
        const safeStop = safeDestination(sim, citizen);
        setTarget(sim, actor.id, safeStop, citizen.revision + 1);
      }
    }
  }, [actors, sim, demo]);
  useEffect(() => {
    let last = performance.now();
    const timer = window.setInterval(() => {
      const now = performance.now(),
        dt = (now - last) / 1000;
      last = now;
      if (!awake && !reduced) stepSimulation(sim, dt);
      setHour((h) => advanceHour(h, dt, dayPaused || reduced));
      setTick((n) => n + 1);
    }, 250);
    return () => window.clearInterval(timer);
  }, [sim, awake, reduced, dayPaused]);
  function travel(stop: Stop) {
    for (const c of sim.citizens.values())
      setTarget(sim, c.id, stop, c.revision + 1);
    setTick((n) => n + 1);
  }
  function movePlot(delta: number) {
    if (!activePlot) return;
    const current = placements[activePlot];
    const x = current[0] + delta;
    const valid = validPlacement(
      PLOTS.find((p) => p.id === activePlot)!,
      x,
      current[2],
      48,
      42,
      [],
    );
    setPlacementError(!valid);
    if (valid)
      setPlacements({ ...placements, [activePlot]: [x, 0, current[2]] });
  }
  const selectedActor = actors.find((a) => a.id === selected);
  const citizen = selected ? sim.citizens.get(selected) : null;
  const button =
    "rounded border border-border bg-background px-2 py-1 text-xs text-foreground hover:bg-muted disabled:opacity-40";
  if (!ready) return null;
  return (
    <div
      className="relative flex h-full min-h-0 flex-col bg-background text-foreground"
      data-testid="city-reference"
    >
      <div className="flex flex-wrap items-center gap-2 border-b border-border bg-popover p-2">
        <strong className="mr-auto text-sm">{t("society.city.title")}</strong>
        {topRight}
        <label className="flex items-center gap-1 text-xs">
          <input
            type="checkbox"
            checked={demo}
            onChange={(e) => {
              setDemo(e.target.checked);
              setSelected(null);
              setFollow(null);
            }}
          />
          {t("society.city.demo")}
        </label>
        {demo && (
          <>
            <select
              className={button}
              aria-label={t("society.city.agent_count")}
              value={count}
              onChange={(e) => setCount(Number(e.target.value))}
            >
              {[1, 8, 30].map((n) => (
                <option key={n}>{n}</option>
              ))}
            </select>
            <button className={button} onClick={() => travel("west")}>
              {t("society.city.west")}
            </button>
            <button className={button} onClick={() => travel("east")}>
              {t("society.city.east")}
            </button>
            <button
              className={button}
              disabled={
                !actors.some((a) =>
                  ["arrived", "waiting", "unreachable"].includes(
                    sim.citizens.get(a.id)?.stage ?? "",
                  ),
                )
              }
              onClick={() => {
                for (const actor of actors) previewCrossing(sim, actor.id);
              }}
            >
              {t("society.city.bridge_walk")}
            </button>
          </>
        )}
        <button
          className={button}
          onClick={() => {
            setFollow(null);
            setOverview((n) => n + 1);
          }}
        >
          {t("society.city.overview")}
        </button>
        <button className={button} onClick={() => setDayPaused(!dayPaused)}>
          {t(dayPaused ? "society.city.resume_day" : "society.city.pause_day")}
        </button>
        <label className="flex items-center gap-1 text-xs">
          {t("society.city.hour")}
          <input
            className="w-24"
            aria-label={t("society.city.hour")}
            type="range"
            min="0"
            max="23.9"
            step="0.1"
            value={hour}
            onChange={(e) => {
              setHour(Number(e.target.value));
              setDayPaused(true);
            }}
          />
          {String(Math.floor(hour)).padStart(2, "0")}:00
        </label>
      </div>
      <div ref={host} className="relative isolate min-h-0 flex-1">
        {webgl ? (
          <Canvas
            key={generation}
            camera={{
              position: [200, 140, 240],
              fov: 45,
              near: 0.2,
              far: 4000,
            }}
            dpr={1}
            shadows
            gl={{ antialias: true, alpha: false }}
            frameloop={!awake ? "never" : reduced ? "demand" : "always"}
          >
            <Suspense fallback={null}>
              <Runtime
                sim={sim}
                hour={hour}
                paused={reduced}
                awake={awake}
                follow={follow}
                overview={overview}
                onMetrics={setMetrics}
              />
              <District
                placements={placements}
                onPlace={(s) => {
                  setActivePlot(s);
                  setPlacementError(false);
                }}
              />
              {actors.map((actor) => (
                <MovingActor
                  key={actor.id}
                  actor={actor}
                  sim={sim}
                  paused={reduced}
                  selected={selected === actor.id}
                  onClick={() => setSelected(actor.id)}
                />
              ))}
            </Suspense>
          </Canvas>
        ) : (
          <div className="p-6">
            <p>{t("society.world.webgl_missing_body")}</p>
            <button className={button} onClick={onOpenLedger}>
              {t("society.world.open_ledger")}
            </button>
          </div>
        )}
        <div className="absolute z-20 bottom-3 left-3 max-w-sm rounded border border-border bg-popover/95 p-3 text-xs shadow-lg">
          <p className="font-medium">{t("society.city.reference_notice")}</p>
          <p className="mt-1 text-muted-foreground">
            {t("society.city.controls")}
          </p>
          <p className="mt-2 font-mono" data-testid="city-metrics">
            {metrics.fps} FPS · {metrics.calls} draws ·{" "}
            {metrics.triangles.toLocaleString()} triangles
          </p>
          <p className="text-muted-foreground">
            600 × 400 m · {actors.length} {t("society.city.agents")} ·{" "}
            {Math.floor(sim.time)} s
          </p>
          {reduced && <p>{t("society.city.reduced")}</p>}
          {roster.isError && !demo && (
            <p role="alert">{t("society.city.feed_error")}</p>
          )}
        </div>
        <div className="absolute z-20 right-3 top-3 max-h-[55%] w-52 overflow-auto rounded border border-border bg-popover/95 p-2 text-xs">
          <p className="mb-2 font-medium">
            {t(demo ? "society.city.demo" : "society.city.live")}
          </p>
          {actors.map((a) => (
            <button
              key={a.id}
              className="mb-1 block w-full rounded px-2 py-1 text-left hover:bg-muted"
              onClick={() => setSelected(a.id)}
            >
              {a.label}
              <span className="block text-muted-foreground">
                {t(
                  `society.city.stage_${sim.citizens.get(a.id)?.stage ?? "arrived"}`,
                )}
              </span>
            </button>
          ))}
        </div>
        {selectedActor && citizen && (
          <div
            className="absolute z-20 bottom-3 right-3 w-64 rounded border border-border bg-popover p-3 text-xs shadow-lg"
            data-testid="city-journey"
            data-stage={citizen.stage}
            data-position={citizen.position.join(",")}
            data-tick={tick}
          >
            <strong>{selectedActor.label}</strong>
            <p>
              {t("society.city.task")}: {selectedActor.checkpoint} ·{" "}
              {selectedActor.state}
            </p>
            <p>
              {t("society.city.travel")}:{" "}
              {t(`society.city.stage_${citizen.stage}`)}
            </p>
            <p>
              {t("society.city.target")}: {t(`society.city.${citizen.target}`)}
            </p>
            <div className="mt-2 flex gap-2">
              <button
                className={button}
                onClick={() =>
                  setFollow(follow === citizen.id ? null : citizen.id)
                }
              >
                {t(
                  follow === citizen.id
                    ? "society.city.unfollow"
                    : "society.city.follow",
                )}
              </button>
              {!demo && (
                <button
                  className={button}
                  onClick={() => onSelectAgent?.(citizen.id)}
                >
                  {t("society.city.open_agent")}
                </button>
              )}
            </div>
          </div>
        )}
        {activePlot && (
          <div className="absolute z-20 left-3 top-3 rounded border border-border bg-popover p-3 text-xs shadow-lg">
            <strong>{t(`society.city.${activePlot}`)}</strong>
            <p className="my-2">{t("society.city.plot")}</p>
            <div className="flex gap-2">
              <button className={button} onClick={() => movePlot(-4)}>
                −4 m
              </button>
              <button className={button} onClick={() => movePlot(4)}>
                +4 m
              </button>
              <button
                className={button}
                onClick={() =>
                  onSelectPlace?.(activePlot === "west" ? "civic" : "cli")
                }
              >
                {t("society.city.open_building")}
              </button>
              <button className={button} onClick={() => setActivePlot(null)}>
                {t("society.city.close")}
              </button>
            </div>
            {placementError && (
              <p role="alert" className="mt-2">
                {t("society.city.invalid_plot")}
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
