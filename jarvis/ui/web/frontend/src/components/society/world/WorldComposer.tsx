/** Pixel rendering has one geometry pass; palette shading is baked into assets. */
import { useEffect, useMemo } from "react";
import { useFrame, useThree } from "@react-three/fiber";
import { EffectComposer } from "three/examples/jsm/postprocessing/EffectComposer.js";
import { OutputPass } from "three/examples/jsm/postprocessing/OutputPass.js";
import { PixelScenePass } from "./PixelScenePass";
import { useWorldSettings } from "./worldSettings";
import { diagnosticsEnabled, recordWorldFrame } from "./worldDiagnostics";

export function WorldComposer() {
  const { gl, scene, camera, size } = useThree();
  const grain = useWorldSettings(s => s.grain);
  const composer = useMemo(() => {
    const c = new EffectComposer(gl);
    c.addPass(new PixelScenePass(scene, camera, grain || 2));
    c.addPass(new OutputPass());
    return c;
  }, [gl, scene, camera, grain]);
  useEffect(() => { composer.setSize(size.width, size.height); }, [composer, size.width, size.height]);
  useEffect(() => () => { for (const pass of composer.passes) pass.dispose(); composer.dispose(); }, [composer]);
  useFrame((_, dt) => {
    const measure = diagnosticsEnabled();
    if (measure) { gl.info.autoReset = false; gl.info.reset(); }
    composer.render();
    if (measure) { recordWorldFrame(dt * 1000, gl.info.render.calls, gl.info.render.triangles); gl.info.autoReset = true; }
  }, 1);
  return null;
}
