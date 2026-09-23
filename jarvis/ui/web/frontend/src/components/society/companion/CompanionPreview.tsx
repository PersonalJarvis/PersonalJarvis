import { Component, Suspense, useRef, type ReactNode } from "react";
import { Canvas } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import { useCanvasAwake } from "@/hooks/useCanvasAwake";
import { useWebglSurface } from "@/hooks/useWebglSurface";
import { useWebglSupported } from "@/lib/graphDimension";
import { CompanionModel } from "./AgentFollower";
import type { CompanionAppearance } from "./appearance";

class PreviewBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(error: Error) { console.warn("Companion preview unavailable", error); }
  render() { return this.state.failed ? null : this.props.children; }
}

export function CompanionPreview({ appearance, lead = false }: { appearance: CompanionAppearance; lead?: boolean }) {
  const host = useRef<HTMLDivElement>(null);
  const { generation } = useWebglSurface(host);
  const awake = useCanvasAwake(host);
  const supported = useWebglSupported();
  return <div ref={host} className="h-48 w-full touch-none" data-testid="companion-preview">
    {supported && awake && <PreviewBoundary key={generation}><Canvas frameloop="demand" dpr={[1, 1.5]} camera={{ position: [0.75, 0.65, 1.8], fov: 38 }} gl={{ alpha: true, antialias: true }}>
      <ambientLight intensity={1.6} /><directionalLight position={[2, 3, 4]} intensity={2.6} />
      <Suspense fallback={null}><CompanionModel appearance={appearance} lead={lead} /></Suspense>
      <OrbitControls target={[0, appearance.sizeM / 2, 0]} enablePan={false} enableZoom={false} minPolarAngle={0.25} maxPolarAngle={Math.PI * 0.65} />
    </Canvas></PreviewBoundary>}
  </div>;
}
