import { Component, Suspense, useEffect, useRef, type ReactNode } from "react";
import { Canvas, useThree } from "@react-three/fiber";
import { ContactShadows, Environment, Lightformer, OrbitControls } from "@react-three/drei";
import { useReducedMotion } from "framer-motion";
import { useCanvasAwake } from "@/hooks/useCanvasAwake";
import { useWebglSurface } from "@/hooks/useWebglSurface";
import { useWebglSupported } from "@/lib/graphDimension";
import { CompanionModel, WORN_PET_LIFT_M } from "./AgentFollower";
import type { CompanionAppearance } from "./appearance";
import { useSyncCompanionPet, useWornPet } from "./companionPetStore";
import { companionFlies } from "./petCompanions";

class PreviewBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(error: Error) { console.warn("Companion preview unavailable", error); }
  render() { return this.state.failed ? null : this.props.children; }
}

/** Vertical field of view of the preview camera, degrees. */
const FOV = 30;
/** How much taller than the body the frame is, so it never touches the edges. */
const MARGIN = 1.55;

/**
 * Where the camera looks and how far back it stands, so the body fills the
 * frame whatever its size: a 0.5 m shape, a 0.28 m blob or a cat that walks
 * on the floor. Every companion knows its own height, so no bounds have to
 * be measured after the model loads.
 */
export function previewFraming(heightM: number, liftM: number): { target: [number, number, number]; position: [number, number, number] } {
  const centre = liftM + heightM / 2;
  const distance = (heightM * MARGIN) / 2 / Math.tan((FOV * Math.PI) / 360);
  // A three-quarter view, slightly from above, the way a figure sits on a desk.
  return { target: [0, centre, 0], position: [distance * 0.5, centre + distance * 0.24, distance * 0.84] };
}

/**
 * A soft studio around the body: an environment lit only by local light
 * panels (no image is downloaded), a warm key that casts the shadow, a cool
 * rim that separates the silhouette from the dark dialog, and a contact
 * shadow that sets it on the ground.
 */
function Studio() {
  return <>
    <Environment resolution={256} frames={1}>
      <Lightformer form="rect" intensity={2.2} position={[2.5, 3, 2.5]} scale={[3, 3, 1]} target={[0, 0, 0]} />
      <Lightformer form="rect" intensity={0.9} color="#cfe0ff" position={[-3, 1.5, 1]} scale={[2, 3, 1]} target={[0, 0, 0]} />
      <Lightformer form="ring" intensity={1.4} position={[0, 2, -3]} scale={2} target={[0, 0, 0]} />
    </Environment>
    <ambientLight intensity={0.25} />
    <directionalLight position={[1.6, 2.6, 1.8]} intensity={1.7} color="#fff4e6" castShadow
      shadow-mapSize={[1024, 1024]} shadow-bias={-0.0004} />
    <directionalLight position={[-1.8, 1.4, -2.2]} intensity={1.1} color="#b9d4ff" />
  </>;
}

type Framing = ReturnType<typeof previewFraming>;

/**
 * Moves the camera when the body changes. The canvas itself stays: building
 * a new one per pet would drop the old WebGL context, which the surface
 * reads as a lost GPU and stops drawing.
 */
function Frame({ framing }: { framing: Framing }) {
  const camera = useThree((state) => state.camera);
  const controls = useThree((state) => state.controls) as { target: { set: (x: number, y: number, z: number) => void }; update: () => void } | null;
  const invalidate = useThree((state) => state.invalidate);
  const [px, py, pz] = framing.position;
  const [tx, ty, tz] = framing.target;
  useEffect(() => {
    camera.position.set(px, py, pz);
    camera.lookAt(tx, ty, tz);
    controls?.target.set(tx, ty, tz);
    controls?.update();
    invalidate();
  }, [camera, controls, invalidate, px, py, pz, tx, ty, tz]);
  return null;
}

export function CompanionPreview({ appearance, lead = false }: { appearance: CompanionAppearance; lead?: boolean }) {
  const host = useRef<HTMLDivElement>(null);
  const { generation } = useWebglSurface(host);
  const awake = useCanvasAwake(host);
  const supported = useWebglSupported();
  const reduced = useReducedMotion() ?? false;
  // A worn pet's model comes from the pets catalog, which the canvas cannot query itself.
  useSyncCompanionPet();
  const pet = useWornPet(lead ? undefined : appearance.pet);
  const framing = pet
    ? previewFraming(pet.heightM, companionFlies(pet) ? WORN_PET_LIFT_M : 0)
    : previewFraming(appearance.sizeM, 0);
  // A pet breathes, blinks and turns on the table; a still preview only draws when asked.
  const live = !!pet && !reduced;
  return <div ref={host} className="h-56 w-full touch-none" data-testid="companion-preview">
    {supported && awake && <PreviewBoundary key={generation}>
      <Canvas frameloop={live ? "always" : "demand"} shadows dpr={[1, 2]}
        camera={{ position: framing.position, fov: FOV, near: 0.02, far: 20 }} gl={{ alpha: true, antialias: true }}>
        <Studio />
        <Frame framing={framing} />
        <Suspense fallback={null}><CompanionModel appearance={appearance} lead={lead} /></Suspense>
        <ContactShadows position={[0, 0.001, 0]} opacity={0.5} scale={Math.max(1, framing.target[1] * 6)} blur={2.6} far={1} resolution={512} frames={live ? Infinity : 1} />
        <OrbitControls makeDefault target={framing.target} enablePan={false} enableZoom={false} autoRotate={live} autoRotateSpeed={0.9}
          minPolarAngle={0.25} maxPolarAngle={Math.PI * 0.55} />
      </Canvas>
    </PreviewBoundary>}
  </div>;
}
