import { Component, Suspense, memo, useCallback, useEffect, useMemo, useRef, useState, type ComponentRef, type ReactNode } from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { Html, Line, OrbitControls } from "@react-three/drei";
import { Color, type Group } from "three";
import { useReducedMotion } from "framer-motion";
import { useSwarmSurface } from "./useSwarmSurface";
import { useCanvasAwake } from "@/hooks/useCanvasAwake";
import { FigureRig, type FigureDrive } from "@/components/society/figures/FigureRig";
import type { FigureRecipe } from "@/components/society/figures/figureRecipe";
import { useSwarmText } from "./strings";
import { exactCount, terminalState, type WorldSnapshot } from "./types";
import { DEFAULT_CAMERA, memoryPosition, readCamera, saveCamera, worldNodes, worldRadius, type CameraState, type WorldNode } from "./worldLayout";
import { cssHslColor } from "./worldPalette";
import { SwarmMemoryHouse } from "./SwarmMemoryHouse";

const RECIPES: Record<string, FigureRecipe> = {
  lead: { contract: 1, archetype: "biped", base: "mage", parts: {} },
  coordinator: { contract: 1, archetype: "biped", base: "knight", parts: {} },
  worker: { contract: 1, archetype: "biped", base: "rogue", parts: {} },
};
function themeColor(name: string, element: HTMLElement): Color {
  const token = getComputedStyle(element).getPropertyValue(`--${name}`).trim();
  return cssHslColor(token);
}
class SceneBoundary extends Component<{ children: ReactNode; onError: () => void }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(error: Error) { console.warn("Swarm 3D scene unavailable", error); this.props.onError(); }
  render() { return this.state.failed ? null : this.props.children; }
}

const Figure = memo(function Figure({ node, paused, selected, onSelect, accent, surface }: {
  node: WorldNode; paused: boolean; selected: boolean; onSelect: (id: string, group?: string) => void; accent: Color; surface: Color;
}) {
  const t = useSwarmText();
  const group = useRef<Group>(null);
  const initialPosition = useRef(node.position);
  const drive = useRef<FigureDrive>({ mode: "idle", speed: 0 });
  const moving = useRef(false);
  const [x, , z] = node.position;
  useEffect(() => {
    drive.current.mode = !paused && node.agent?.state === "running" ? "work" : "idle";
  }, [node.agent?.state, paused]);
  useFrame((_, dt) => {
    if (!group.current) return;
    const pos = group.current.position;
    const distance = Math.hypot(x - pos.x, z - pos.z);
    if (distance > .04 && !paused) {
      const step = Math.min(distance, dt * 4);
      pos.x += (x - pos.x) / distance * step; pos.z += (z - pos.z) / distance * step;
      group.current.rotation.y = Math.atan2(x - pos.x, z - pos.z);
      drive.current = { mode: "walk", speed: 4 }; moving.current = true;
    } else {
      pos.set(x, 0, z);
      if (moving.current) { drive.current = { mode: !paused && node.agent?.state === "running" ? "work" : "idle", speed: 0 }; moving.current = false; }
    }
  });
  return <group ref={group} position={initialPosition.current} onClick={e => { e.stopPropagation(); onSelect(node.agent?.id ?? node.id, node.group); }}>
    <mesh position={[0, .06, 0]}><cylinderGeometry args={[node.group ? 1.05 : .75, node.group ? 1.05 : .75, .12, 24]} /><meshStandardMaterial color={surface} roughness={1} /></mesh>
    <mesh position={[0, .13, 0]} rotation={[-Math.PI / 2, 0, 0]}><ringGeometry args={[node.group ? .98 : .69, node.group ? 1.05 : .76, 32]} /><meshBasicMaterial color={accent} transparent opacity={selected ? 1 : .35} /></mesh>
    <group position={[0, .13, 0]}><FigureRig recipe={RECIPES[node.agent?.role ?? "coordinator"]} heightM={1.8} drive={drive} paused={paused} /></group>
    {node.agent?.tool_activity && <mesh position={[1, .7, 0]}><boxGeometry args={[.4, .7, .25]} /><meshStandardMaterial color={accent} /></mesh>}
    <Html position={[0, 2.45, 0]} center style={{ pointerEvents: "none" }}>
      <div className="swarm-world-label" data-selected={selected}><strong>{node.title}</strong>
        <span>{node.group ? `${exactCount(node.count ?? "0")} ${t("agents")}` : `${t(node.agent?.role ?? "worker")} · ${t(node.agent?.state ?? "idle")}`}</span>
        {selected && <span>{t("level")} {node.level}{node.agent?.tool_activity ? ` · ${node.agent.tool_activity}` : ""}</span>}</div>
    </Html>
  </group>;
});

function Camera({ camera, radius, onChange }: { camera: CameraState; radius: number; onChange: (camera: CameraState) => void }) {
  const { camera: rendererCamera, invalidate } = useThree();
  const controls = useRef<ComponentRef<typeof OrbitControls>>(null);
  useEffect(() => {
    rendererCamera.position.set(camera.focus[0] + Math.cos(camera.yaw) * radius / camera.zoom, radius * (camera.elevation ?? .9) / camera.zoom, camera.focus[1] + Math.sin(camera.yaw) * radius / camera.zoom);
    rendererCamera.lookAt(camera.focus[0], .6, camera.focus[1]);
    rendererCamera.updateProjectionMatrix(); invalidate();
  }, [camera, radius, rendererCamera, invalidate]);
  const saveOrbit = () => {
    const target = controls.current?.target;
    if (!target) return;
    const dx = rendererCamera.position.x - target.x; const dz = rendererCamera.position.z - target.z;
    const distance = Math.max(.1, Math.hypot(dx, dz));
    onChange({ yaw: Math.atan2(dz, dx), zoom: Math.max(.4, Math.min(3, radius / distance)), focus: [target.x, target.z], elevation: Math.max(.1, Math.min(99, rendererCamera.position.y / distance)) });
  };
  return <OrbitControls ref={controls} makeDefault target={[camera.focus[0], .6, camera.focus[1]]} minDistance={5} maxDistance={180} maxPolarAngle={Math.PI / 2.1} screenSpacePanning={false} enableDamping={false} onEnd={saveOrbit} />;
}
export default function SwarmWorld3D({ snapshot, selected, onSelect, onUnavailable, live, onInspect }: {
  snapshot: WorldSnapshot; selected: string; onSelect: (id: string, group?: string) => void; onUnavailable: () => void; live: boolean;
  onInspect?: (kind: "artifacts" | "publications") => void;
}) {
  const t = useSwarmText();
  const host = useRef<HTMLDivElement>(null);
  const awake = useCanvasAwake(host);
  const reduced = useReducedMotion() ?? false;
  const generation = useSwarmSurface(host, onUnavailable);
  const [lowPower, setLowPower] = useState(false);
  const [camera, setCamera] = useState(() => readCamera(snapshot.team.id));
  const changeCamera = useCallback((next: CameraState) => setCamera(next), []);
  const [palette, setPalette] = useState(() => ({ accent: new Color(), surface: new Color(), background: new Color() }));
  const nodes = useMemo(() => worldNodes(snapshot), [snapshot]);
  const radius = worldRadius(nodes);
  const memory = memoryPosition(nodes);
  const artifacts = snapshot.counts.artifacts ?? "";
  const publications = snapshot.counts.publications ?? "";
  const paused = !awake || !live || reduced || lowPower || snapshot.team.state !== "running";
  useEffect(() => { saveCamera(snapshot.team.id, camera); }, [snapshot.team.id, camera]);
  useEffect(() => {
    const element = host.current;
    if (!element) return;
    const update = () => setPalette({ accent: themeColor("primary", element), surface: themeColor("muted", element), background: themeColor("background", element) });
    update();
    const observer = new MutationObserver(update);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class", "style", "data-theme"] });
    return () => observer.disconnect();
  }, []);
  const links = useMemo(() => {
    const byId = new Map(nodes.map(n => [n.id, n.position]));
    return snapshot.activity.flatMap(event => {
      const sender = event.agent_id && byId.get(event.agent_id);
      const recipientIds = event.data.recipients;
      if (!sender || !Array.isArray(recipientIds)) return [];
      return recipientIds.slice(0, 16).flatMap(id => {
        const recipient = byId.get(String(id));
        return recipient ? [{ id: `${event.id}:${id}`, points: [[sender[0], .2, sender[2]], [recipient[0], .2, recipient[2]]] as [number, number, number][] }] : [];
      });
    }).slice(-32);
  }, [nodes, snapshot.activity]);
  return <div className="swarm-world" ref={host} data-team-id={snapshot.team.id}>
    <SceneBoundary onError={onUnavailable}><Suspense fallback={<div className="swarm-empty">{t("loading")}</div>}>
      <Canvas key={generation} dpr={lowPower ? 1 : [1, 1.5]} frameloop={paused ? "demand" : "always"} camera={{ fov: 45, near: .1, far: 500 }} gl={{ antialias: !lowPower, powerPreference: "low-power" }}>
        <color attach="background" args={[palette.background]} /><ambientLight intensity={1.05} /><directionalLight position={[12, 25, 8]} intensity={2} />
        <Camera camera={camera} radius={radius} onChange={changeCamera} />
        <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -.03, 0]}><circleGeometry args={[radius * .9, 64]} /><meshStandardMaterial color={palette.surface} roughness={1} /></mesh>
        <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -.02, 0]}><ringGeometry args={[radius * .9 - .045, radius * .9, 64]} /><meshBasicMaterial color={palette.accent} transparent opacity={.18} /></mesh>
        <Suspense fallback={null}><SwarmMemoryHouse position={memory} accent={palette.accent} artifacts={artifacts} publications={publications} /></Suspense>
        {links.map(link => <Line key={link.id} points={link.points} color={palette.accent} lineWidth={1.5} dashed />)}
        {nodes.map((node, index) => <Figure key={node.id} node={node} paused={paused || index >= 32} selected={selected === node.id} onSelect={onSelect} accent={palette.accent} surface={palette.surface} />)}
      </Canvas>
    </Suspense></SceneBoundary>
    <div className="swarm-world-controls"><div role="group" aria-label={t("camera")}>
      <button aria-label={t("rotateLeft")} onClick={() => setCamera(c => ({ ...c, yaw: c.yaw - .3 }))}>↶</button>
      <button aria-label={t("rotateRight")} onClick={() => setCamera(c => ({ ...c, yaw: c.yaw + .3 }))}>↷</button>
      <button aria-label={t("zoomIn")} onClick={() => setCamera(c => ({ ...c, zoom: Math.min(3, c.zoom * 1.2) }))}>+</button>
      <button aria-label={t("zoomOut")} onClick={() => setCamera(c => ({ ...c, zoom: Math.max(.4, c.zoom / 1.2) }))}>−</button>
      <button onClick={() => setCamera({ ...DEFAULT_CAMERA, focus: [0, 0] })}>{t("resetCamera")}</button>
    </div><label className="swarm-check"><input type="checkbox" checked={lowPower} onChange={e => setLowPower(e.target.checked)} />{t("lowPower")}</label></div>
    <div className="swarm-world-memory"><strong>{t("memoryHouse")}</strong><div>
      <button onClick={() => onInspect?.("artifacts")} disabled={!onInspect}>{exactCount(artifacts)} {t("artifactCount")}</button>
      <button onClick={() => onInspect?.("publications")} disabled={!onInspect}>{exactCount(publications)} {t("publicationCount")}</button>
    </div></div>
    <div className="swarm-minimap" aria-label={t("minimap")}><svg viewBox="-50 -50 100 100" role="img" aria-label={t("minimap")}>
      <circle r="45" fill="none" stroke="currentColor" opacity=".15" />
      <rect x={memory[0] / radius * 65 - 2.5} y={memory[2] / radius * 65 - 2.5} width="5" height="5" fill="none" stroke="var(--swarm-accent)"><title>{t("memoryHouse")}</title></rect>
      {nodes.map(node => <circle key={node.id} cx={node.position[0] / radius * 65} cy={node.position[2] / radius * 65} r={node.id === selected ? 3 : 1.8} fill="var(--swarm-accent)" />)}
    </svg><button onClick={() => setCamera({ ...DEFAULT_CAMERA, focus: [0, 0] })}>{t("overview")}</button>
    {terminalState(snapshot.team.state) && <span className="swarm-muted">{t("finalWorld")}</span>}</div>
  </div>;
}
