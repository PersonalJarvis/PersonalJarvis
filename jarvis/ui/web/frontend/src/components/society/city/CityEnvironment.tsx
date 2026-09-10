/** A continuous urban setting assembled from individually reviewed Blender modules. */
import { memo, useEffect, useMemo } from "react";
import { Html } from "@react-three/drei";
import {
  BoxGeometry,
  Color,
  InstancedMesh,
  Matrix4,
  MeshStandardMaterial,
  Quaternion,
  Vector3,
} from "three";
import { useT } from "@/i18n";
import { releaseCityInstances } from "./cityInstances";
import {
  CITY_ASSETS,
  CityAsset,
  CityInstances,
  type InstancePose,
} from "./CityAssets";
import {
  DISTRICTS,
  LIFTS,
  MATERIALS,
  RAIL_POINTS,
  graphWalkways,
  transformPoint,
  type Placements,
  type RouteGraph,
  type Stop,
  type Vec3,
} from "./cityModel";

interface BoxPose extends InstancePose {
  size: Vec3;
  color?: string;
}
const UP = new Vector3(0, 1, 0);
function Boxes({
  poses,
  color,
  shadows = false,
}: {
  poses: BoxPose[];
  color: string;
  shadows?: boolean;
}) {
  const mesh = useMemo(() => {
    const geometry = new BoxGeometry(1, 1, 1);
    const material = new MeshStandardMaterial({ color, roughness: 0.82 });
    const result = new InstancedMesh(geometry, material, poses.length);
    const matrix = new Matrix4(),
      position = new Vector3(),
      rotation = new Quaternion(),
      scale = new Vector3();
    poses.forEach((pose, index) => {
      matrix.compose(
        position.set(...pose.position),
        rotation.setFromAxisAngle(UP, pose.yaw ?? 0),
        scale.set(...pose.size),
      );
      result.setMatrixAt(index, matrix);
      if (pose.color) result.setColorAt(index, new Color(pose.color));
    });
    result.instanceMatrix.needsUpdate = true;
    result.computeBoundingSphere();
    result.castShadow = shadows;
    result.receiveShadow = true;
    return result;
  }, [poses, color, shadows]);
  useEffect(
    () => () => {
      mesh.geometry.dispose();
      (mesh.material as MeshStandardMaterial).dispose();
      releaseCityInstances(mesh);
    },
    [mesh],
  );
  return <primitive object={mesh} dispose={null} />;
}
function segment(
  a: Vec3,
  b: Vec3,
  width: number,
  thickness: number,
  lower = 0,
): BoxPose {
  return {
    position: [
      (a[0] + b[0]) / 2,
      (a[1] + b[1]) / 2 - thickness / 2 - lower,
      (a[2] + b[2]) / 2,
    ],
    size: [Math.hypot(b[0] - a[0], b[2] - a[2]) + 0.25, thickness, width],
    yaw: -Math.atan2(b[2] - a[2], b[0] - a[0]),
  };
}
function hash(x: number, z: number): number {
  const value = Math.sin(x * 127.1 + z * 311.7) * 43758.5453;
  return value - Math.floor(value);
}
const stationPoses: InstancePose[] = DISTRICTS.map((d) => ({
  position: [d.stationPosition[0], 12.08, d.stationPosition[2]],
  yaw: d.stationYaw,
}));
const dressing = (() => {
  const offices: InstancePose[] = [],
    towers: InstancePose[] = [],
    residences: InstancePose[] = [],
    trees: InstancePose[] = [],
    lamps: InstancePose[] = [],
    far: BoxPose[] = [],
    roads: BoxPose[] = [];
  // Streets continue beyond the operational boundary, with a cheap distant silhouette.
  for (let x = -3000; x <= 3000; x += 240)
    roads.push({ position: [x, -0.12, 0], size: [18, 0.18, 6500] });
  for (let z = -3000; z <= 3000; z += 240)
    roads.push({ position: [0, -0.12, z], size: [6500, 0.18, 18] });
  for (let x = -2700; x <= 2700; x += 90)
    for (let z = -2250; z <= 2250; z += 90) {
      if (Math.abs(x % 240) < 25 || Math.abs(z % 240) < 25) continue;
      const onRing =
        Math.abs((x / 500) ** 2 + ((z + 110) / 280) ** 2 - 1) < 0.48;
      if (
        onRing ||
        DISTRICTS.some((d) => Math.hypot(x - d.x, z - d.z) < 108) ||
        (Math.abs(x) < 45 && z > 20 && z < 320)
      )
        continue;
      const choice = hash(x, z),
        scale = 0.7 + hash(z, x) * 0.5;
      const pose: InstancePose = {
        position: [x + choice * 16, -0.15, z + choice * 10],
        yaw: choice > 0.5 ? Math.PI / 2 : 0,
        scale,
      };
      // The authored skyline kit is already only 288–636 triangles per model.
      // Reuse it into the distance instead of a visible wall of unrelated boxes.
      (choice < 0.35 ? offices : choice < 0.65 ? towers : residences).push(pose);
    }
  for (const d of DISTRICTS) {
    for (const side of [-1, 1])
      for (let z = 26; z < 132; z += 14) {
        trees.push({
          position: transformPoint(
            [d.stationPosition[0], 0, d.stationPosition[2]],
            d.stationYaw,
            [side * 38, 0, z],
          ),
          scale: 0.8 + hash(z, side) * 0.35,
        });
        if (z % 28 === 12)
          lamps.push({
            position: transformPoint(
              [d.stationPosition[0], 0, d.stationPosition[2]],
              d.stationYaw,
              [side * 25, 0, z],
            ),
            yaw: d.stationYaw,
          });
      }
  }
  return { offices, towers, residences, trees, lamps, far, roads };
})();
const infrastructure = (() => {
  const decks: BoxPose[] = [],
    rails: BoxPose[] = [],
    pillars: BoxPose[] = [];
  RAIL_POINTS.slice(1).forEach((b, index) => {
    const a = RAIL_POINTS[index];
    decks.push(segment(a, b, 6.4, 0.7, 1.5));
    const dx = b[0] - a[0],
      dz = b[2] - a[2],
      length = Math.hypot(dx, dz);
    for (const side of [-1, 1]) {
      const offsetX = (-dz / length) * side * 1.65,
        offsetZ = (dx / length) * side * 1.65;
      rails.push(
        segment(
          [a[0] + offsetX, 10.88, a[2] + offsetZ],
          [b[0] + offsetX, 10.88, b[2] + offsetZ],
          0.14,
          0.15,
        ),
      );
    }
    if (index % 8 === 0)
      pillars.push({ position: [a[0], 5, a[2]], size: [1.4, 10, 1.4] });
  });
  return { decks, rails, pillars };
})();
export const CityEnvironment = memo(function CityEnvironment({
  graph,
  placements,
  activeStops,
  onSelect,
  interiorStop,
}: {
  graph: RouteGraph;
  placements: Placements;
  activeStops: Set<Stop>;
  onSelect: (stop: Stop) => void;
  interiorStop: Stop | null;
}) {
  const t = useT();
  const paths = useMemo(
    () =>
      graphWalkways(graph).map((way) => segment(way.a, way.b, way.width, 0.18)),
    [graph],
  );
  const safetyRails = useMemo(
    () =>
      graphWalkways(graph)
        .filter((way) => way.layer === "bridge")
        .flatMap((way) => {
          const dx = way.b[0] - way.a[0],
            dz = way.b[2] - way.a[2],
            length = Math.hypot(dx, dz);
          return [-1, 1].map((side) => {
            const x = ((-dz / length) * side * way.width) / 2,
              z = ((dx / length) * side * way.width) / 2;
            return segment(
              [way.a[0] + x, way.a[1] + 1.2, way.a[2] + z],
              [way.b[0] + x, way.b[1] + 1.2, way.b[2] + z],
              0.12,
              0.12,
            );
          });
        }),
    [graph],
  );
  const plazas = useMemo(
    () =>
      DISTRICTS.map((d): BoxPose => ({
        position: transformPoint(
          [d.stationPosition[0], 0, d.stationPosition[2]],
          d.stationYaw,
          [0, -0.25, 73],
        ),
        size: [96, 0.3, 132],
        yaw: d.stationYaw,
      })),
    [],
  );
  return (
    <>
      <mesh
        rotation={[-Math.PI / 2, 0, 0]}
        position={[0, -0.35, 0]}
        receiveShadow
      >
        <planeGeometry args={[18000, 18000]} />
        <meshStandardMaterial color="#819085" roughness={1} />
      </mesh>
      <Boxes poses={dressing.roads} color="#3d4c53" />
      <Boxes poses={dressing.far} color="#65737b" />
      <CityInstances url={CITY_ASSETS.office} poses={dressing.offices} />
      <CityInstances url={CITY_ASSETS.tower} poses={dressing.towers} />
      <CityInstances url={CITY_ASSETS.residence} poses={dressing.residences} />
      <CityInstances url={CITY_ASSETS.tree} poses={dressing.trees} />
      <CityInstances url={CITY_ASSETS.lamp} poses={dressing.lamps} />
      <Boxes poses={plazas} color={MATERIALS.concrete} />
      <Boxes poses={paths} color="#c3c8bf" />
      <Boxes poses={safetyRails} color={MATERIALS.metal} />
      <Boxes poses={infrastructure.decks} color={MATERIALS.graphite} />
      <Boxes poses={infrastructure.rails} color={MATERIALS.metal} />
      <Boxes
        poses={infrastructure.pillars}
        color={MATERIALS.concrete}
        shadows
      />
      <CityInstances url={CITY_ASSETS.station} poses={stationPoses} shadows />
      {LIFTS.map((lift) => (
        <group key={lift.id} position={lift.a}>
          <mesh position={[0, (lift.b[1] - lift.a[1]) / 2, 0]}>
            <boxGeometry args={[5, lift.b[1] - lift.a[1], 5]} />
            <meshStandardMaterial
              color="#8ab3c5"
              transparent
              opacity={0.12}
              depthWrite={false}
              roughness={0.2}
            />
          </mesh>
          <mesh position={[0, lift.b[1] - lift.a[1] - 0.15, 0]}>
            <boxGeometry args={[5.5, 0.3, 5.5]} />
            <meshStandardMaterial color={MATERIALS.metal} />
          </mesh>
        </group>
      ))}
      {DISTRICTS.map((d) => (
        <group key={d.stop}>
          <CityAsset
            url={CITY_ASSETS[d.id]}
            position={placements[d.stop]}
            yaw={d.buildingRotation}
            working={activeStops.has(d.stop)}
            cutaway={interiorStop === d.stop}
            onClick={() => onSelect(d.stop)}
          />
          <Html
            position={[d.stationPosition[0], 24, d.stationPosition[2]]}
            zIndexRange={[1, 0]}
            center
            style={{ pointerEvents: "none" }}
          >
            <span className="whitespace-nowrap rounded border border-border bg-popover/95 px-2 py-1 text-xs text-popover-foreground">
              {t(d.labelKey)}
            </span>
          </Html>
        </group>
      ))}
    </>
  );
});
