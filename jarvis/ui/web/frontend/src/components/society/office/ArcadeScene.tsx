/**
 * The 3D view of Asteroid Run: a low-poly rocket seen from behind, faceted
 * asteroids tumbling out of the depth, laser bolts, debris and a warp-streak
 * star field. Everything that moves is pooled (instanced meshes and one line
 * buffer) and written straight from the game state every frame, so a busy
 * late-game field costs no React work at all.
 */
import { useEffect, useMemo, useRef } from "react";
import { useFrame, useThree } from "@react-three/fiber";
import {
  AdditiveBlending, BufferAttribute, BufferGeometry, CapsuleGeometry, Color, ConeGeometry, CylinderGeometry, DynamicDrawUsage, Fog, IcosahedronGeometry,
  InstancedMesh, LineBasicMaterial, LineSegments, MeshBasicMaterial, MeshStandardMaterial, Object3D, OctahedronGeometry,
  BoxGeometry, PointLight, RingGeometry, SphereGeometry, TetrahedronGeometry, Vector3, type Group, type Mesh,
} from "three";
import {
  BLAST_S, GUN_OFFSET, ROCK_RADIUS, ROCK_VARIANTS, SPAWN_Z, launchMissile, lockedRockIds, stepArcade,
  type ArcadeInput, type ArcadeState,
} from "./arcadeGame";

const STEP = 1 / 120;
const ROCKS_PER_VARIANT = 90;
const MAX_LASERS = 60;
const MAX_DEBRIS = 500;
const MAX_PICKUPS = 12;
const MAX_MISSILES_IN_FLIGHT = 12;
const MAX_LOCKS = 12;
const MAX_BLASTS = 24;
const BLAST_HOT = new Color("#fff1c2");
const BLAST_COOL = new Color("#ff5a1f");
/** A click this close (CSS px) to a rock's outline picks it. */
const PICK_SLOP_PX = 26;
/** Imperial-green bolts: a white-hot core in a green glow. */
const LASER_GLOW = "#2dff5a";
const STAR_COUNT = 900;
const SPACE = "#0b0a14";

/** A jagged icosahedron: the same seed always gives the same rock. */
function rockGeometry(seed: number): BufferGeometry {
  let s = seed * 9301 + 49297;
  const rand = () => { s = (s * 9301 + 49297) % 233280; return s / 233280; };
  const base = new IcosahedronGeometry(1, 1);
  const pos = base.getAttribute("position");
  // Displace shared corners by the same amount so the surface stays closed.
  const offsets = new Map<string, number>();
  for (let i = 0; i < pos.count; i += 1) {
    const key = `${pos.getX(i).toFixed(3)},${pos.getY(i).toFixed(3)},${pos.getZ(i).toFixed(3)}`;
    let k = offsets.get(key);
    if (k === undefined) { k = 0.72 + rand() * 0.5; offsets.set(key, k); }
    pos.setXYZ(i, pos.getX(i) * k, pos.getY(i) * k * (0.85 + (seed % 3) * 0.08), pos.getZ(i) * k);
  }
  const geometry = base.toNonIndexed();
  geometry.computeVertexNormals();
  base.dispose();
  return geometry;
}

const ROCK_TONES = ["#8b93b5", "#a9b1cf", "#6f7898", "#c7cde2", "#7e86a8"].map((c) => new Color(c));
const WHITE = new Color("#ffffff");
const HOT = new Color("#ffc157");
const DUST = new Color("#9aa2c2");

function Rocket({ state, reduced }: { state: React.MutableRefObject<ArcadeState>; reduced: boolean }) {
  const group = useRef<Group>(null);
  const flame = useRef<Mesh>(null);
  const shield = useRef<Mesh>(null);
  const light = useRef<PointLight>(null);
  const muzzle = useRef<Group>(null);
  const muzzleLight = useRef<PointLight>(null);
  const parts = useMemo(() => ({
    body: new CylinderGeometry(0.34, 0.4, 1.5, 12),
    band: new CylinderGeometry(0.405, 0.405, 0.22, 12),
    nose: new ConeGeometry(0.34, 0.7, 12),
    nozzle: new CylinderGeometry(0.28, 0.36, 0.25, 12),
    fin: new BoxGeometry(0.05, 0.55, 0.4),
    flame: new ConeGeometry(0.26, 1.2, 10),
    shield: new IcosahedronGeometry(1.25, 1),
    flash: new SphereGeometry(0.22, 10, 8),
    flashMat: new MeshBasicMaterial({ color: LASER_GLOW, transparent: true, opacity: 0.9, blending: AdditiveBlending, depthWrite: false, toneMapped: false }),
    white: new MeshStandardMaterial({ color: "#eef2f8", roughness: 0.35, metalness: 0.25 }),
    blue: new MeshStandardMaterial({ color: "#3b82f6", roughness: 0.4, metalness: 0.3 }),
    red: new MeshStandardMaterial({ color: "#e0443e", roughness: 0.5, flatShading: true }),
    dark: new MeshStandardMaterial({ color: "#3a4150", roughness: 0.6, metalness: 0.5 }),
    fire: new MeshBasicMaterial({ color: "#ffcc33", toneMapped: false, transparent: true, opacity: 0.95 }),
    bubble: new MeshBasicMaterial({ color: "#7dd3fc", wireframe: true, transparent: true, opacity: 0, toneMapped: false }),
  }), []);
  useEffect(() => () => { Object.values(parts).forEach((p) => p.dispose()); }, [parts]);

  useFrame(({ clock }) => {
    const s = state.current;
    const g = group.current;
    if (!g) return;
    g.visible = s.phase !== "over" && (s.invulnerable <= 0 || Math.floor(clock.elapsedTime * 10) % 2 === 0);
    g.position.set(s.shipX, s.shipY, 0);
    // Bank into turns, pitch with climbs.
    g.rotation.set(-Math.PI / 2 + s.vy * 0.03, 0, -s.vx * 0.045);
    if (flame.current) {
      const flicker = reduced ? 1 : 0.85 + Math.sin(clock.elapsedTime * 40) * 0.12 + Math.random() * 0.08;
      const len = (s.boosting ? 2 : 1) * flicker;
      flame.current.scale.set(s.boosting ? 1.25 : 1, len, s.boosting ? 1.25 : 1);
      flame.current.position.y = -0.75 - 0.6 * len;
      (flame.current.material as MeshBasicMaterial).color.set(s.boosting ? "#7dd3fc" : "#ffcc33");
    }
    if (light.current) light.current.intensity = (s.boosting ? 14 : 8) * (reduced ? 1 : 0.9 + Math.random() * 0.2);
    // Twin green muzzle flashes right after a volley.
    const flash = Math.max(0, 1 - s.sinceShot / 0.07);
    if (muzzle.current) { muzzle.current.visible = flash > 0; muzzle.current.scale.setScalar(0.6 + flash * 0.8); }
    if (muzzleLight.current) muzzleLight.current.intensity = flash * 18;
    if (shield.current) {
      const fade = Math.max(0, 1 - s.sinceHit / 0.6);
      (shield.current.material as MeshBasicMaterial).opacity = fade * 0.7;
      shield.current.rotation.y = clock.elapsedTime;
    }
  });

  return (
    // Built along +y, then laid down to point into the screen (-z).
    <group ref={group}>
      <mesh geometry={parts.body} material={parts.white} />
      <mesh geometry={parts.band} material={parts.blue} position={[0, 0.2, 0]} />
      <mesh geometry={parts.nose} material={parts.blue} position={[0, 1.1, 0]} />
      <mesh geometry={parts.nozzle} material={parts.dark} position={[0, -0.85, 0]} />
      {[0, 1, 2].map((i) => {
        const a = (i / 3) * Math.PI * 2;
        return <mesh key={i} geometry={parts.fin} material={parts.red} position={[Math.sin(a) * 0.45, -0.45, Math.cos(a) * 0.45]} rotation={[0, a, 0]} />;
      })}
      <mesh ref={flame} geometry={parts.flame} material={parts.fire} rotation={[Math.PI, 0, 0]} position={[0, -1.35, 0]} />
      <pointLight ref={light} color="#ff9a3c" distance={9} decay={2} position={[0, -1.6, 0]} />
      <mesh ref={shield} geometry={parts.shield} material={parts.bubble} />
      {/* Muzzles sit at the gun offsets, just ahead of the nose (local +y is forward). */}
      <group ref={muzzle} visible={false}>
        <mesh geometry={parts.flash} material={parts.flashMat} position={[-GUN_OFFSET, 1.3, 0]} />
        <mesh geometry={parts.flash} material={parts.flashMat} position={[GUN_OFFSET, 1.3, 0]} />
      </group>
      <pointLight ref={muzzleLight} color={LASER_GLOW} distance={7} decay={2} intensity={0} position={[0, 1.6, 0]} />
    </group>
  );
}

export function ArcadeScene({ state, input, reduced }: {
  state: React.MutableRefObject<ArcadeState>;
  input: React.MutableRefObject<ArcadeInput>;
  reduced: boolean;
}) {
  const scene = useThree((s) => s.scene);
  const camera = useThree((s) => s.camera);
  const acc = useRef(0);
  const dummy = useMemo(() => new Object3D(), []);
  const colour = useMemo(() => new Color(), []);

  const pools = useMemo(() => {
    const rockMaterial = new MeshStandardMaterial({ flatShading: true, roughness: 0.85, metalness: 0.1, color: "#ffffff" });
    const rocks = Array.from({ length: ROCK_VARIANTS }, (_, v) => {
      const mesh = new InstancedMesh(rockGeometry(v + 1), rockMaterial, ROCKS_PER_VARIANT);
      mesh.instanceMatrix.setUsage(DynamicDrawUsage);
      mesh.frustumCulled = false;
      mesh.setColorAt(0, WHITE);
      return mesh;
    });
    // Bolts lie along z: a thin bright core inside a wider additive glow.
    const boltCore = new CapsuleGeometry(0.06, 5.5, 4, 8);
    boltCore.rotateX(Math.PI / 2);
    const boltGlow = new CapsuleGeometry(0.22, 6.2, 4, 10);
    boltGlow.rotateX(Math.PI / 2);
    const lasers = new InstancedMesh(boltCore, new MeshBasicMaterial({ color: "#eaffee", toneMapped: false }), MAX_LASERS);
    const laserGlow = new InstancedMesh(boltGlow, new MeshBasicMaterial({
      color: LASER_GLOW, transparent: true, opacity: 0.55, blending: AdditiveBlending, depthWrite: false, toneMapped: false,
    }), MAX_LASERS);
    // Missiles: a white body built along z with a red-hot tint, plus an engine glow.
    const missileBody = new CylinderGeometry(0.11, 0.11, 1, 8);
    missileBody.rotateX(Math.PI / 2);
    const missiles = new InstancedMesh(missileBody, new MeshStandardMaterial({
      color: "#f1f3f8", roughness: 0.4, metalness: 0.3, emissive: "#ff4d3d", emissiveIntensity: 0.25,
    }), MAX_MISSILES_IN_FLIGHT);
    const missileGlow = new InstancedMesh(new SphereGeometry(0.28, 10, 8), new MeshBasicMaterial({
      color: "#ff9a3c", transparent: true, opacity: 0.85, blending: AdditiveBlending, depthWrite: false, toneMapped: false,
    }), MAX_MISSILES_IN_FLIGHT);
    // Lock-on diamonds around every rock a missile is chasing.
    const locks = new InstancedMesh(new RingGeometry(1.15, 1.32, 4, 1), new MeshBasicMaterial({
      color: "#ff4d3d", transparent: true, opacity: 0.9, blending: AdditiveBlending, depthWrite: false, depthTest: false, toneMapped: false,
    }), MAX_LOCKS);
    const debris = new InstancedMesh(new TetrahedronGeometry(0.16), new MeshStandardMaterial({ flatShading: true, roughness: 0.8, color: "#ffffff" }), MAX_DEBRIS);
    debris.setColorAt(0, WHITE);
    const pickups = new InstancedMesh(new OctahedronGeometry(0.55), new MeshStandardMaterial({ flatShading: true, color: "#ffffff", emissive: "#ffffff", emissiveIntensity: 0.6 }), MAX_PICKUPS);
    pickups.setColorAt(0, WHITE);
    // Fireballs: additive spheres that swell and fade to nothing (colour to black).
    const blasts = new InstancedMesh(new IcosahedronGeometry(1, 2), new MeshBasicMaterial({
      color: "#ffffff", transparent: true, blending: AdditiveBlending, depthWrite: false, toneMapped: false,
    }), MAX_BLASTS);
    blasts.setColorAt(0, WHITE);
    const effects = [lasers, laserGlow, missiles, missileGlow, locks, blasts, debris, pickups];
    for (const m of effects) { m.instanceMatrix.setUsage(DynamicDrawUsage); m.frustumCulled = false; }
    locks.renderOrder = 10;
    // Stars as short segments that stretch into warp streaks with speed.
    const starPos = new Float32Array(STAR_COUNT * 6);
    const stars = Array.from({ length: STAR_COUNT }, () => ({ x: (Math.random() - 0.5) * 140, y: (Math.random() - 0.5) * 90, z: SPAWN_Z + Math.random() * (20 - SPAWN_Z) }));
    const starGeometry = new BufferGeometry();
    starGeometry.setAttribute("position", new BufferAttribute(starPos, 3).setUsage(DynamicDrawUsage));
    const starLines = new LineSegments(starGeometry, new LineBasicMaterial({ color: "#c9d4ff", transparent: true, opacity: 0.75 }));
    starLines.frustumCulled = false;
    return { rocks, rockMaterial, lasers, laserGlow, missiles, missileGlow, locks, blasts, debris, pickups, effects, starLines, starPos, stars };
  }, []);

  useEffect(() => {
    scene.background = new Color(SPACE);
    scene.fog = new Fog(SPACE, 60, 150);
    return () => {
      scene.fog = null;
      for (const m of [...pools.rocks, ...pools.effects]) { m.geometry.dispose(); m.dispose(); }
      pools.rockMaterial.dispose();
      for (const m of pools.effects) (m.material as MeshBasicMaterial | MeshStandardMaterial).dispose();
      pools.starLines.geometry.dispose();
      (pools.starLines.material as LineBasicMaterial).dispose();
    };
  }, [scene, pools]);

  // Click a rock to send a guided missile at it. Picking happens in screen
  // space with some slop, so small, fast rocks far away are still clickable.
  const gl = useThree((s) => s.gl);
  const probe = useMemo(() => new Vector3(), []);
  useEffect(() => {
    const canvas = gl.domElement;
    const onDown = (event: PointerEvent) => {
      if (event.button !== 0) return;
      const s = state.current;
      if (s.phase !== "playing") return;
      const rect = canvas.getBoundingClientRect();
      const px = event.clientX - rect.left, py = event.clientY - rect.top;
      const locked = lockedRockIds(s);
      let best: { id: number; score: number } | null = null;
      for (const rock of s.rocks) {
        if (rock.z > 0 || locked.has(rock.id)) continue;
        probe.set(rock.x, rock.y, rock.z).project(camera);
        if (probe.z > 1) continue;
        const sx = ((probe.x + 1) / 2) * rect.width, sy = ((1 - probe.y) / 2) * rect.height;
        // Screen radius of the rock, from a point one radius to its side.
        probe.set(rock.x + ROCK_RADIUS[rock.size], rock.y, rock.z).project(camera);
        const rr = Math.abs(((probe.x + 1) / 2) * rect.width - sx);
        const d = Math.hypot(px - sx, py - sy);
        if (d > rr + PICK_SLOP_PX) continue;
        const score = d / (rr + PICK_SLOP_PX);
        if (!best || score < best.score) best = { id: rock.id, score };
      }
      if (best) launchMissile(s, best.id);
    };
    canvas.addEventListener("pointerdown", onDown);
    return () => canvas.removeEventListener("pointerdown", onDown);
  }, [gl, camera, state, probe]);

  useFrame((_, rawDt) => {
    const dt = Math.min(rawDt, 0.1);
    acc.current += dt;
    while (acc.current >= STEP) { stepArcade(state.current, input.current, STEP); acc.current -= STEP; }
    const s = state.current;

    // Camera: behind, above and off to the right of the rocket (a three-quarter
    // view that shows its side), easing after it; shake on hits.
    const shake = reduced ? 0 : s.shake * 0.9;
    const cx = s.shipX * 0.55 + 3.6 + (Math.random() - 0.5) * shake, cy = s.shipY * 0.55 + 3 + (Math.random() - 0.5) * shake;
    camera.position.x += (cx - camera.position.x) * Math.min(1, dt * 6);
    camera.position.y += (cy - camera.position.y) * Math.min(1, dt * 6);
    camera.position.z = 10.5;
    camera.lookAt(s.shipX * 0.75 - 0.8, s.shipY * 0.75 + 0.2, -25);

    // Rocks, per silhouette.
    const counts = new Array(ROCK_VARIANTS).fill(0);
    for (const rock of s.rocks) {
      const mesh = pools.rocks[rock.variant];
      const i = counts[rock.variant];
      if (i >= ROCKS_PER_VARIANT) continue;
      counts[rock.variant] = i + 1;
      dummy.position.set(rock.x, rock.y, rock.z);
      dummy.rotation.set(rock.rx, rock.ry, 0);
      dummy.scale.setScalar(ROCK_RADIUS[rock.size]);
      dummy.updateMatrix();
      mesh.setMatrixAt(i, dummy.matrix);
      mesh.setColorAt(i, rock.flash > 0 ? WHITE : ROCK_TONES[Math.floor(rock.tone * ROCK_TONES.length)]);
    }
    pools.rocks.forEach((mesh, v) => {
      mesh.count = counts[v];
      mesh.instanceMatrix.needsUpdate = true;
      if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
    });

    let n = 0;
    for (const laser of s.lasers) {
      if (n >= MAX_LASERS) break;
      // The bolt trails behind its tip, which is where hits are counted.
      dummy.position.set(laser.x, laser.y, laser.z + 2.9);
      dummy.rotation.set(0, 0, 0);
      dummy.scale.setScalar(1);
      dummy.updateMatrix();
      pools.lasers.setMatrixAt(n, dummy.matrix);
      pools.laserGlow.setMatrixAt(n, dummy.matrix);
      n += 1;
    }
    pools.lasers.count = n;
    pools.laserGlow.count = n;
    pools.lasers.instanceMatrix.needsUpdate = true;
    pools.laserGlow.instanceMatrix.needsUpdate = true;

    // Missiles point along their flight; the glow sits at the tail.
    n = 0;
    for (const m of s.missiles) {
      if (n >= MAX_MISSILES_IN_FLIGHT) break;
      const v = Math.hypot(m.vx, m.vy, m.vz) || 1;
      dummy.position.set(m.x, m.y, m.z);
      dummy.scale.setScalar(1);
      dummy.lookAt(m.x + m.vx / v, m.y + m.vy / v, m.z + m.vz / v);
      dummy.updateMatrix();
      pools.missiles.setMatrixAt(n, dummy.matrix);
      dummy.position.set(m.x - (m.vx / v) * 0.6, m.y - (m.vy / v) * 0.6, m.z - (m.vz / v) * 0.6);
      dummy.scale.setScalar(reduced ? 1 : 0.8 + Math.random() * 0.5);
      dummy.updateMatrix();
      pools.missileGlow.setMatrixAt(n, dummy.matrix);
      n += 1;
    }
    pools.missiles.count = n;
    pools.missileGlow.count = n;
    pools.missiles.instanceMatrix.needsUpdate = true;
    pools.missileGlow.instanceMatrix.needsUpdate = true;

    n = 0;
    for (const b of s.blasts) {
      if (n >= MAX_BLASTS) break;
      const k = b.age / BLAST_S;
      dummy.position.set(b.x, b.y, b.z);
      dummy.rotation.set(0, 0, 0);
      dummy.scale.setScalar(b.size * (0.35 + Math.sqrt(k) * 0.9));
      dummy.updateMatrix();
      pools.blasts.setMatrixAt(n, dummy.matrix);
      pools.blasts.setColorAt(n, colour.copy(BLAST_HOT).lerp(BLAST_COOL, Math.min(1, k * 1.6)).multiplyScalar(1 - k));
      n += 1;
    }
    pools.blasts.count = n;
    pools.blasts.instanceMatrix.needsUpdate = true;
    if (pools.blasts.instanceColor) pools.blasts.instanceColor.needsUpdate = true;

    // Lock-on diamonds, facing the camera and slowly turning.
    n = 0;
    const locked = lockedRockIds(s);
    for (const rock of s.rocks) {
      if (n >= MAX_LOCKS || !locked.has(rock.id)) continue;
      dummy.position.set(rock.x, rock.y, rock.z);
      dummy.quaternion.copy(camera.quaternion);
      dummy.rotateZ(reduced ? Math.PI / 4 : performance.now() / 400);
      dummy.scale.setScalar(ROCK_RADIUS[rock.size] * 1.1);
      dummy.updateMatrix();
      pools.locks.setMatrixAt(n, dummy.matrix);
      n += 1;
    }
    pools.locks.count = n;
    pools.locks.instanceMatrix.needsUpdate = true;

    n = 0;
    for (const d of s.debris) {
      if (n >= MAX_DEBRIS) break;
      const k = d.life / d.max;
      dummy.position.set(d.x, d.y, d.z);
      dummy.rotation.set(d.spin * k, d.spin * 0.7 * k, 0);
      dummy.scale.setScalar(d.hot ? 0.7 * k + 0.1 : 1.1 * k + 0.2);
      dummy.updateMatrix();
      pools.debris.setMatrixAt(n, dummy.matrix);
      pools.debris.setColorAt(n, d.hot ? colour.copy(HOT).lerp(WHITE, k * 0.4) : DUST);
      n += 1;
    }
    pools.debris.count = n;
    pools.debris.instanceMatrix.needsUpdate = true;
    if (pools.debris.instanceColor) pools.debris.instanceColor.needsUpdate = true;

    n = 0;
    for (const p of s.pickups) {
      if (n >= MAX_PICKUPS) break;
      dummy.position.set(p.x, p.y, p.z);
      dummy.rotation.set(p.spin * 0.6, p.spin, 0);
      dummy.scale.setScalar(1);
      dummy.updateMatrix();
      pools.pickups.setMatrixAt(n, dummy.matrix);
      pools.pickups.setColorAt(n, colour.set(p.kind === "shield" ? "#38bdf8" : "#facc15"));
      n += 1;
    }
    pools.pickups.count = n;
    pools.pickups.instanceMatrix.needsUpdate = true;
    if (pools.pickups.instanceColor) pools.pickups.instanceColor.needsUpdate = true;

    // Star streaks: the faster the field, the longer the lines.
    const moving = s.phase === "playing" ? s.speed : s.phase === "over" ? 0 : 6;
    const streak = Math.max(0.15, moving * 0.035);
    const pos = pools.starPos;
    pools.stars.forEach((star, i) => {
      star.z += moving * dt;
      if (star.z > 14) { star.z = SPAWN_Z; star.x = (Math.random() - 0.5) * 140; star.y = (Math.random() - 0.5) * 90; }
      const o = i * 6;
      pos[o] = star.x; pos[o + 1] = star.y; pos[o + 2] = star.z;
      pos[o + 3] = star.x; pos[o + 4] = star.y; pos[o + 5] = star.z - streak;
    });
    (pools.starLines.geometry.getAttribute("position") as BufferAttribute).needsUpdate = true;
  });

  return (
    <>
      <ambientLight color="#7d88b8" intensity={0.55} />
      <directionalLight color="#e6ecff" intensity={2.2} position={[6, 9, 8]} />
      <directionalLight color="#6d7cff" intensity={1} position={[-8, -4, -6]} />
      <hemisphereLight args={["#aab6ff", "#1b1530", 0.35]} />
      {pools.rocks.map((mesh, i) => <primitive key={i} object={mesh} />)}
      <primitive object={pools.laserGlow} />
      <primitive object={pools.lasers} />
      <primitive object={pools.missiles} />
      <primitive object={pools.missileGlow} />
      <primitive object={pools.locks} />
      <primitive object={pools.blasts} />
      <primitive object={pools.debris} />
      <primitive object={pools.pickups} />
      <primitive object={pools.starLines} />
      <Rocket state={state} reduced={reduced} />
    </>
  );
}
