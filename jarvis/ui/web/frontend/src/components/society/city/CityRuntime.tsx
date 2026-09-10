import {
  memo,
  useEffect,
  useMemo,
  useRef,
  useState,
  type MutableRefObject,
} from "react";
import { useFrame, useThree } from "@react-three/fiber";
import { Html, OrbitControls, useGLTF } from "@react-three/drei";
import {
  AnimationMixer,
  Color,
  Fog,
  Group,
  Mesh,
  PMREMGenerator,
  Vector3,
  type DirectionalLight,
  type HemisphereLight,
} from "three";
import type { OrbitControls as OrbitControlsType } from "three-stdlib";
import { RoomEnvironment } from "three/examples/jsm/environments/RoomEnvironment.js";
import { FigureRig, type FigureDrive } from "../figures/FigureRig";
import type { FigureRecipe } from "../figures/figureRecipe";
import { CAMERA_BOUNDS, type Vec3 } from "./cityModel";
import { daylight, type Citizen, type CitySimulation } from "./citySimulation";
import { tickCityClock, type CityClock } from "./cityClock";
import { Metro } from "./CityAssets";
import citizenUrl from "../../../../../../../../art/studies/city-realism-study/exports/citizen.glb";

export interface CityActor {
  id: string;
  label: string;
  recipe?: FigureRecipe | null;
  checkpoint: string;
  state: string;
}
export interface CityMetrics {
  fps: number;
  calls: number;
  triangles: number;
  geometries: number;
  textures: number;
}
export interface CameraFocus {
  position: Vec3;
  distance: number;
  yaw: number;
  key: number;
}
export const OVERVIEW: CameraFocus = {
  position: [0, 0, -60],
  distance: 950,
  yaw: 0.65,
  key: 0,
};
function ReferenceCitizen({
  citizen,
  paused,
}: {
  citizen: Citizen;
  paused: boolean;
}) {
  const { scene, animations } = useGLTF(citizenUrl);
  const clone = useMemo(() => scene.clone(true), [scene]);
  const mixer = useMemo(() => new AnimationMixer(clone), [clone]);
  const moving = useRef(false);
  const phase = useRef(0);
  useEffect(() => {
    for (const clip of animations) mixer.clipAction(clip).play();
    return () => {
      mixer.stopAllAction();
      mixer.uncacheRoot(clone);
    };
  }, [animations, clone, mixer]);
  useFrame((_, dt) => {
    const walking = !paused && citizen.speed > 0;
    if (walking) mixer.update((Math.min(dt, 0.1) * citizen.speed) / 1.28);
    else if (moving.current) mixer.setTime(0);
    moving.current = walking;
    const working = citizen.atWork && citizen.taskActive && !paused;
    if (!paused) phase.current += Math.min(dt, 0.1);
    for (const [index, side] of ["L", "R"].entries()) {
      const arm = clone.getObjectByName(`Arm_${side}`),
        forearm = clone.getObjectByName(`Forearm_${side}`);
      if (arm) arm.rotation.x = working ? -0.45 : 0;
      if (forearm)
        forearm.rotation.x = working
          ? -1.1 + Math.sin(phase.current * 8 + index * Math.PI) * 0.07
          : 0;
    }
  });
  return <primitive object={clone} dispose={null} />;
}
export const MovingActor = memo(function MovingActor({
  actor,
  sim,
  paused,
  selected,
  onSelect,
}: {
  actor: CityActor;
  sim: CitySimulation;
  paused: boolean;
  selected: boolean;
  onSelect: (id: string) => void;
}) {
  const root = useRef<Group>(null),
    body = useRef<Group>(null),
    distant = useRef<Mesh>(null);
  const nearRef = useRef(true);
  const [near, setNear] = useState(true);
  const drive = useRef<FigureDrive>({ mode: "idle", speed: 0 });
  const citizen = sim.citizens.get(actor.id);
  useFrame(({ camera }) => {
    if (!root.current || !citizen) return;
    root.current.position.set(...citizen.position);
    root.current.rotation.y = citizen.heading;
    const detail =
      selected || camera.position.distanceTo(root.current.position) < 100;
    if (detail !== nearRef.current) {
      nearRef.current = detail;
      setNear(detail);
    }
    if (body.current) body.current.visible = detail;
    if (distant.current) distant.current.visible = !detail;
    drive.current.mode =
      citizen.speed > 0
        ? "walk"
        : citizen.atWork && citizen.taskActive
          ? "work"
          : "idle";
    drive.current.speed = citizen.speed;
  });
  if (!citizen) return null;
  return (
    <group
      ref={root}
      onClick={(event) => {
        event.stopPropagation();
        onSelect(actor.id);
      }}
    >
      <group ref={body}>
        {actor.recipe ? (
          <FigureRig
            recipe={actor.recipe}
            drive={drive}
            paused={paused || !near}
          />
        ) : (
          <ReferenceCitizen citizen={citizen} paused={paused || !near} />
        )}
      </group>
      <mesh ref={distant} position={[0, 0.85, 0]} visible={false}>
        <capsuleGeometry args={[0.24, 1.2, 3, 6]} />
        <meshStandardMaterial color="#5595a4" roughness={0.8} />
      </mesh>
      {selected && (
        <>
          <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.025, 0]}>
            <ringGeometry args={[0.65, 0.82, 24]} />
            <meshBasicMaterial color="#ebc772" />
          </mesh>
          <Html
            position={[0, 2.4, 0]}
            center
            zIndexRange={[1, 0]}
            style={{ pointerEvents: "none" }}
          >
            <span className="whitespace-nowrap rounded border border-border bg-popover px-2 py-1 text-xs text-popover-foreground">
              {actor.label}
            </span>
          </Html>
        </>
      )}
    </group>
  );
});

export const CityRuntime = memo(function CityRuntime({
  sim,
  clock,
  paused,
  focus,
  follow,
  metrics,
  requestFrame,
}: {
  sim: CitySimulation;
  clock: CityClock;
  paused: boolean;
  focus: CameraFocus;
  follow: string | null;
  metrics: MutableRefObject<CityMetrics>;
  requestFrame: MutableRefObject<() => void>;
}) {
  const sun = useRef<DirectionalLight>(null),
    sky = useRef<HemisphereLight>(null),
    controls = useRef<OrbitControlsType>(null);
  const { scene, camera, gl, invalidate } = useThree();
  const samples = useRef({ seconds: 0, frames: 0 });
  const light = useMemo(
    () => ({
      night: new Color("#182b3c"),
      day: new Color("#a6beca"),
      background: new Color(),
      target: new Vector3(),
      delta: new Vector3(),
    }),
    [],
  );
  useEffect(() => {
    requestFrame.current = invalidate;
    gl.localClippingEnabled = true;
    const room = new RoomEnvironment(),
      generator = new PMREMGenerator(gl),
      target = generator.fromScene(room, 0.04);
    scene.environment = target.texture;
    scene.fog = new Fog("#a6beca", 1100, 4400);
    room.dispose();
    generator.dispose();
    invalidate();
    return () => {
      scene.environment = null;
      scene.fog = null;
      target.dispose();
      requestFrame.current = () => undefined;
    };
  }, [gl, scene, invalidate, requestFrame]);
  useEffect(() => {
    if (!controls.current) return;
    const [x, y, z] = focus.position;
    controls.current.target.set(x, y, z);
    camera.position.set(
      x + Math.sin(focus.yaw) * focus.distance,
      y + focus.distance * 0.6,
      z + Math.cos(focus.yaw) * focus.distance,
    );
    controls.current.update();
    invalidate();
  }, [focus, camera, invalidate]);
  useEffect(() => {
    const citizen = follow ? sim.citizens.get(follow) : null;
    if (!citizen || !controls.current) return;
    const [x, y, z] = citizen.position;
    controls.current.target.set(x, y + 1, z);
    camera.position.set(x + 12, y + 9, z - 16);
    controls.current.update();
    invalidate();
  }, [follow, camera, sim, invalidate]);
  useFrame((_, dt) => {
    tickCityClock(clock, sim, performance.now(), paused);
    const strength = daylight(clock.hour);
    light.background.copy(light.night).lerp(light.day, strength);
    scene.background = light.background;
    if (scene.fog instanceof Fog) scene.fog.color.copy(light.background);
    scene.environmentIntensity = 0.25 + strength * 0.6;
    if (sky.current) sky.current.intensity = 0.7 + strength * 1.1;
    if (sun.current) {
      sun.current.intensity = 0.2 + strength * 2.1;
      sun.current.position.set(
        Math.cos((clock.hour / 24) * Math.PI * 2) * 350,
        160 + strength * 250,
        240,
      );
    }
    const orbit = controls.current;
    if (orbit) {
      const citizen = follow ? sim.citizens.get(follow) : null;
      if (citizen) {
        light.target.set(...citizen.position);
        light.target.y += 1;
        light.delta.copy(light.target).sub(orbit.target);
        camera.position.add(light.delta);
        orbit.target.copy(light.target);
      }
      light.target.copy(orbit.target);
      orbit.target.x = Math.max(
        CAMERA_BOUNDS.minX,
        Math.min(CAMERA_BOUNDS.maxX, orbit.target.x),
      );
      orbit.target.z = Math.max(
        CAMERA_BOUNDS.minZ,
        Math.min(CAMERA_BOUNDS.maxZ, orbit.target.z),
      );
      camera.position.add(light.delta.copy(orbit.target).sub(light.target));
      camera.position.y = Math.max(2.5, camera.position.y);
      orbit.update();
      // City-scale distances need a larger near plane to prevent road/plaza z-fighting.
      const near = Math.max(0.2, Math.min(15, camera.position.distanceTo(orbit.target) / 100));
      if (Math.abs(camera.near - near) > 0.05) { camera.near = near; camera.updateProjectionMatrix(); }
    }
    if (dt > 0) {
      samples.current.seconds += dt;
      samples.current.frames++;
    }
    if (samples.current.seconds >= 1) {
      metrics.current = {
        fps: Math.round(samples.current.frames / samples.current.seconds),
        calls: gl.info.render.calls,
        triangles: gl.info.render.triangles,
        geometries: gl.info.memory.geometries,
        textures: gl.info.memory.textures,
      };
      samples.current = { seconds: 0, frames: 0 };
    }
  }, -1);
  return (
    <>
      <hemisphereLight ref={sky} args={["#d2e0e9", "#697266", 1.5]} />
      <directionalLight ref={sun} position={[250, 350, 240]} intensity={2.2} />
      <OrbitControls
        ref={controls}
        makeDefault
        target={[0, 0, -60]}
        minDistance={CAMERA_BOUNDS.minDistance}
        maxDistance={CAMERA_BOUNDS.maxDistance}
        maxPolarAngle={Math.PI / 2 - 0.025}
        enableDamping={!paused}
      />
      <Metro sim={sim} paused={paused} follow={follow} />
    </>
  );
});
