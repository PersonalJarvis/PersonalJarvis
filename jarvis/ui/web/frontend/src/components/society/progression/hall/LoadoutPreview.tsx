/**
 * The Upgrade Studio's live preview: the person's own figure or their pet on
 * the hall's stage, wearing a loadout — the one they wear, or one with a
 * locked piece put on to try. Trails only show in motion, so with a trail on
 * the figure walks a slow circle round the stage; otherwise it stands while
 * the stage turns. Drag turns it by hand.
 *
 * One canvas through `useWebglSurface` (AP-32: the context is handed back on
 * unmount and rebuilt when the browser takes it), rendering only while on
 * screen (`useCanvasAwake`). The stage is the game's own room, so it keeps
 * the hall's midnight look in light and dark mode, like the world itself.
 */
import { Component, Suspense, useLayoutEffect, useMemo, useRef, type ErrorInfo, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { useReducedMotion } from "framer-motion";
import type { Group } from "three";
import { useCanvasAwake } from "@/hooks/useCanvasAwake";
import { useWebglSurface } from "@/hooks/useWebglSurface";
import { useT } from "@/i18n";
import { CompanionModel } from "../../companion/AgentFollower";
import { defaultCompanion } from "../../companion/appearance";
import { companionFlies, type CompanionPet } from "../../companion/petCompanions";
import type { PetDrive } from "../../companion/PetModel";
import type { FigureDrive } from "../../figures/FigureRig";
import { CompanionBody, GIGI_OFFICE_SIZE_M, ModelBoundary } from "../../office/GigiFlyer";
import { StudioStage } from "../../office/LevelHall";
import { OFFICE_FIGURE_HEIGHT_M } from "../../office/OfficeAgents";
import { ToyFigure } from "../../office/ToyFigure";
import type { ToyLook } from "../../office/toyFigureModel";
import type { Loadout } from "../cosmetics";
import { figureGadgetSlots } from "../wornGadget";
import { CosmeticTrail, type FlairSource, type TrailKind } from "../effects/CosmeticTrail";
import { CosmeticAura, CosmeticGadget, type AuraKind, type GadgetKind } from "../effects/CosmeticWear";

/** The walk round the stage: radius in metres and speed in m/s. */
const WALK_R = 0.95;
const WALK_SPEED = 0.85;
/** How fast the stage turns while the figure stands, rad/s. */
const TURN_SPEED = 0.35;
/** Flying pets hover this high over the stage. */
const HOVER_M = 0.32;
/** A pet is drawn this tall on the stage, so a 0.3 m teapot reads as well as the person does. */
const PET_SHOWN_M = 0.78;

export type PreviewSubject = { kind: "person"; look: ToyLook } | { kind: "pet"; pet: CompanionPet };

/** Where the figure stands this frame, shared by the figure and every flair that follows it. */
interface Pose { x: number; z: number; heading: number; moving: boolean }

function PreviewScene({ subject, loadout, paused, reduced, spin }: {
  subject: PreviewSubject; loadout: Loadout; paused: boolean; reduced: boolean; spin: { current: number };
}) {
  const walking = !!loadout.trail && !reduced;
  const pose = useRef<Pose>({ x: 0, z: 0, heading: 0, moving: false });
  const clock = useRef(0);
  const body = useRef<Group>(null);
  const turntable = useRef<Group>(null);
  const figureDrive = useRef<FigureDrive>({ mode: "idle", speed: 0 });
  const petDrive = useRef<PetDrive>({ speed: 0, mood: "idle" });
  const pet = subject.kind === "pet" ? subject.pet : null;
  const flies = pet ? companionFlies(pet) : false;
  // A pet stands on the same stage, drawn larger (its own group scales; the stage does not), so its walk shrinks to match.
  const grow = pet ? PET_SHOWN_M / pet.heightM : 1;
  const lift = flies ? HOVER_M / grow : 0;
  const top = pet ? lift + pet.heightM + 0.04 : OFFICE_FIGURE_HEIGHT_M + 0.02;
  const walkR = WALK_R / grow;
  const scale = pet ? 0.5 : 1;
  const gigi = useMemo(() => ({ ...defaultCompanion("jarvis"), sizeM: GIGI_OFFICE_SIZE_M }), []);
  const worn = figureGadgetSlots(pet ? null : (loadout.gadget as GadgetKind | undefined), { drive: figureDrive, top, paused, reduced });

  useFrame((_, rawDt) => {
    const dt = paused ? 0 : Math.min(rawDt, 0.1);
    clock.current += dt;
    const p = pose.current;
    if (walking) {
      const angle = (clock.current * WALK_SPEED) / WALK_R;
      p.x = Math.cos(angle) * walkR;
      p.z = Math.sin(angle) * walkR;
      // Heading along the circle (counter-clockwise seen from above).
      p.heading = Math.atan2(-Math.sin(angle), Math.cos(angle));
      p.moving = true;
    } else {
      p.x = 0;
      p.z = 0;
      p.heading = 0;
      p.moving = false;
    }
    figureDrive.current.mode = p.moving ? "walk" : "idle";
    figureDrive.current.speed = p.moving ? WALK_SPEED : 0;
    petDrive.current.speed = p.moving ? WALK_SPEED : 0;
    if (body.current) {
      body.current.position.set(p.x, lift + (flies && !reduced ? Math.sin(clock.current * 2.2) * 0.04 : 0), p.z);
      body.current.rotation.y = p.heading;
    }
    // The stage turns while the figure stands; a drag adds its own turn.
    if (turntable.current) {
      if (!walking && !reduced) spin.current += dt * TURN_SPEED;
      turntable.current.rotation.y = spin.current;
    }
  });

  // Flair reads the pose in the turntable's frame, so it turns with the figure.
  const ground: FlairSource = () => ({ x: pose.current.x, z: pose.current.z, heading: pose.current.heading });
  const head: FlairSource = () => ({ x: pose.current.x, z: pose.current.z, y: top });
  return (
    <>
      <hemisphereLight args={["#dfe8ff", "#1a1f3a", 0.9]} />
      <directionalLight position={[2.5, 4, 3]} intensity={1.4} color="#fff4e2" />
      <pointLight position={[0, 2.6, 0.6]} intensity={5} distance={6} decay={2} color="#ffdca0" />
      <pointLight position={[-2, 1.4, -2]} intensity={3} distance={6} decay={2} color="#8fb8ff" />
      <group ref={turntable}>
        <StudioStage />
        <group scale={grow}>
        <group ref={body}>
          {pet ? (
            <ModelBoundary key={pet.id}>
              <Suspense fallback={null}>
                <CompanionBody pet={pet} drive={petDrive} reduced={reduced} paused={paused} gigi={<CompanionModel appearance={gigi} lead />} />
              </Suspense>
            </ModelBoundary>
          ) : (
            <>
              <ToyFigure look={(subject as Extract<PreviewSubject, { kind: "person" }>).look} drive={figureDrive} paused={paused} heightM={OFFICE_FIGURE_HEIGHT_M}
                back={worn.back} headwear={worn.headwear} />
              {worn.beside}
            </>
          )}
        </group>
        {loadout.aura && <CosmeticAura key={loadout.aura} kind={loadout.aura as AuraKind} source={ground} scale={scale} paused={paused} reduced={reduced} />}
        {walking && <CosmeticTrail key={loadout.trail} kind={loadout.trail as TrailKind} source={ground} scale={scale} paused={paused} />}
        {loadout.gadget && pet && (
          <CosmeticGadget key={loadout.gadget} kind={loadout.gadget as GadgetKind} source={head} top={top} scale={scale} paused={paused} reduced={reduced} />
        )}
        </group>
      </group>
      <CameraFrame />
    </>
  );
}

/** Frames the whole stage with the walk circle on it; a tall, narrow box stands further back. */
function CameraFrame() {
  const camera = useThree((s) => s.camera);
  const size = useThree((s) => s.size);
  useLayoutEffect(() => {
    const narrow = size.width / Math.max(1, size.height) < 0.9;
    const distance = 4.9 * (narrow ? 1.22 : 1);
    camera.position.set(0, 2.0, distance);
    camera.lookAt(0, 0.5, 0);
    camera.updateProjectionMatrix();
  }, [camera, size]);
  return null;
}

export function LoadoutPreview({ subject, loadout, label }: { subject: PreviewSubject; loadout: Loadout; label: string }) {
  const t = useT();
  const host = useRef<HTMLDivElement>(null);
  const { generation } = useWebglSurface(host);
  const awake = useCanvasAwake(host);
  const reduced = useReducedMotion() ?? false;
  const spin = useRef(0.4);
  const drag = useRef<{ x: number; id: number } | null>(null);
  const onPointerDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    drag.current = { x: e.clientX, id: e.pointerId };
    e.currentTarget.setPointerCapture(e.pointerId);
  };
  const onPointerMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (!d || d.id !== e.pointerId) return;
    spin.current += (e.clientX - d.x) * 0.012;
    d.x = e.clientX;
  };
  const onPointerUp = (e: ReactPointerEvent<HTMLDivElement>) => { if (drag.current?.id === e.pointerId) drag.current = null; };
  return (
    <div ref={host} className="hall-preview" role="img" aria-label={label}
      onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerCancel={onPointerUp}>
      <PreviewBoundary fallback={<p className="hall-preview-fallback">{t("society.figure.unavailable")}</p>}>
        <Canvas key={generation} dpr={[1, 1.5]} frameloop={awake ? "always" : "never"}
          gl={{ antialias: true, alpha: true, powerPreference: "low-power" }} camera={{ fov: 30, near: 0.1, far: 40, position: [0, 1.75, 4.1] }}
          onCreated={({ gl }) => gl.setClearColor(0x000000, 0)}>
          <Suspense fallback={null}>
            <PreviewScene subject={subject} loadout={loadout} paused={!awake} reduced={reduced} spin={spin} />
          </Suspense>
        </Canvas>
      </PreviewBoundary>
      {/* A trail only shows in motion; with reduced motion the figure stands, so say why the try-on looks the same. */}
      {reduced && loadout.trail && <p className="hall-preview-note">{t("society.hall.trail_reduced")}</p>}
    </div>
  );
}

class PreviewBoundary extends Component<{ fallback: ReactNode; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError(): { failed: boolean } { return { failed: true }; }
  componentDidCatch(error: Error, info: ErrorInfo): void {
    // A WebGL that cannot be created is a stated fallback, not a silent one.
    console.warn("[society] studio preview fell back to text:", error.message, info.componentStack);
  }
  render(): ReactNode { return this.state.failed ? this.props.fallback : this.props.children; }
}
