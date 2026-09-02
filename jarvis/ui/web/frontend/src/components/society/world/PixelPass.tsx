/**
 * The retro look, and the performance medicine in one: the scene renders into
 * a render target `pixelSize` times smaller than the canvas, nearest-filtered,
 * with thin depth/normal edge lines — three's own `RenderPixelatedPass`
 * (MASTERPLAN §4.1). Fragment cost drops by pixelSize², which is what lets the
 * island run on WebView2's integrated GPU.
 *
 * Mounted INSIDE the R3F Canvas. `useFrame` with priority 1 takes over
 * rendering from R3F, so the composer is the only thing that draws.
 */
import { useEffect, useMemo } from "react";
import { useFrame, useThree } from "@react-three/fiber";
import { EffectComposer } from "three/examples/jsm/postprocessing/EffectComposer.js";
import { OutputPass } from "three/examples/jsm/postprocessing/OutputPass.js";
import { RenderPixelatedPass } from "three/examples/jsm/postprocessing/RenderPixelatedPass.js";

interface Props {
  pixelSize: number;
}

export function PixelPass({ pixelSize }: Props) {
  const gl = useThree((s) => s.gl);
  const scene = useThree((s) => s.scene);
  const camera = useThree((s) => s.camera);
  const size = useThree((s) => s.size);

  const composer = useMemo(() => {
    const c = new EffectComposer(gl);
    const pass = new RenderPixelatedPass(pixelSize, scene, camera, {
      // Soft outlines: enough to separate a figure from the ground, not a comic ink line.
      normalEdgeStrength: 0.18,
      depthEdgeStrength: 0.28,
    });
    c.addPass(pass);
    c.addPass(new OutputPass());
    return { c, pass };
  }, [gl, scene, camera, pixelSize]);

  useEffect(() => {
    composer.c.setSize(size.width, size.height);
    composer.pass.setPixelSize(pixelSize);
  }, [composer, size.width, size.height, pixelSize]);

  useEffect(() => () => composer.c.dispose(), [composer]);

  useFrame(() => {
    composer.c.render();
  }, 1);

  return null;
}
