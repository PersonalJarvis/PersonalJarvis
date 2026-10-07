/**
 * The studio's live preview: the person's own figure on the hall's stage in
 * the uniform, cap and decorations of a loadout — the one they wear, or one
 * with a locked piece put on to try — and the insignia of their rank; or the
 * pet, which wears its rank on its name plate. The stage turns slowly; a
 * drag turns it by hand.
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
import type { RankId } from "../levelCatalog";
import { dressedLook, regaliaFor, type UniformId } from "../regalia/dress";

/** How fast the stage turns, rad/s. */
const TURN_SPEED = 0.18;
/** Flying pets hover this high over the stage. */
const HOVER_M = 0.32;
/** A pet is drawn this tall on the stage, so a 0.3 m teapot reads as well as the person does. */
const PET_SHOWN_M = 0.78;

export type PreviewSubject = { kind: "person"; look: ToyLook } | { kind: "pet"; pet: CompanionPet };

function PreviewScene({ subject, loadout, rank, paused, reduced, spin }: {
  subject: PreviewSubject; loadout: Loadout; rank: RankId; paused: boolean; reduced: boolean; spin: { current: number };
}) {
  const clock = useRef(0);
  const body = useRef<Group>(null);
  const turntable = useRef<Group>(null);
  const figureDrive = useRef<FigureDrive>({ mode: "idle", speed: 0 });
  const petDrive = useRef<PetDrive>({ speed: 0, mood: "idle" });
  const pet = subject.kind === "pet" ? subject.pet : null;
  const flies = pet ? companionFlies(pet) : false;
  // A pet stands on the same stage, drawn larger (its own group scales; the stage does not).
  const grow = pet ? PET_SHOWN_M / pet.heightM : 1;
  const lift = flies ? HOVER_M / grow : 0;
  const gigi = useMemo(() => ({ ...defaultCompanion("jarvis"), sizeM: GIGI_OFFICE_SIZE_M }), []);
  const person = subject.kind === "person" ? subject.look : null;
  const dressed = useMemo(() => (person ? {
    look: dressedLook(person, loadout.uniform as UniformId | undefined, rank), regalia: regaliaFor(rank, loadout),
  } : null), [person, loadout, rank]);

  useFrame((_, rawDt) => {
    const dt = paused ? 0 : Math.min(rawDt, 0.1);
    clock.current += dt;
    if (body.current) body.current.position.set(0, lift + (flies && !reduced ? Math.sin(clock.current * 2.2) * 0.04 : 0), 0);
    // The stage turns slowly; a drag adds its own turn.
    if (turntable.current) {
      if (!reduced) spin.current += dt * TURN_SPEED;
      turntable.current.rotation.y = spin.current;
    }
  });

  return (
    <>
      <hemisphereLight args={["#ffffff", "#d9d2c3", 1.25]} />
      <directionalLight position={[2.2, 4, 3.2]} intensity={1.6} color="#fff6e8" />
      <directionalLight position={[-3, 2.2, 1.5]} intensity={0.55} color="#e6eeff" />
      <pointLight position={[0, 1.6, -2]} intensity={2.2} distance={6} decay={2} color="#ffffff" />
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
          ) : dressed && (
            <ToyFigure look={dressed.look} drive={figureDrive} paused={paused} heightM={OFFICE_FIGURE_HEIGHT_M} regalia={dressed.regalia} />
          )}
        </group>
        </group>
      </group>
      <CameraFrame />
    </>
  );
}

/** Frames the figure from the knees up; a tall, narrow box stands further back. */
function CameraFrame() {
  const camera = useThree((s) => s.camera);
  const size = useThree((s) => s.size);
  useLayoutEffect(() => {
    const narrow = size.width / Math.max(1, size.height) < 0.9;
    // Close on the figure: the insignia, ribbons and cap are what the studio shows off.
    const distance = 4.1 * (narrow ? 1.2 : 1);
    camera.position.set(0, 1.45, distance);
    camera.lookAt(0, 0.62, 0);
    camera.updateProjectionMatrix();
  }, [camera, size]);
  return null;
}

export function LoadoutPreview({ subject, loadout, rank, label }: {
  subject: PreviewSubject; loadout: Loadout; rank: RankId; label: string;
}) {
  const t = useT();
  const host = useRef<HTMLDivElement>(null);
  const { generation } = useWebglSurface(host);
  const awake = useCanvasAwake(host);
  const reduced = useReducedMotion() ?? false;
  const spin = useRef(-0.3);
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
            <PreviewScene subject={subject} loadout={loadout} rank={rank} paused={!awake} reduced={reduced} spin={spin} />
          </Suspense>
        </Canvas>
      </PreviewBoundary>
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
