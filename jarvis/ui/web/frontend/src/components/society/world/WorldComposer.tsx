/**
 * The frame, assembled — successor of the V1 `PixelPass`.
 *
 * Smooth mode (grain 0, the default): the scene renders into targets `scale`
 * times smaller than the canvas, gets a soft bloom on its emissives, and is
 * upscaled with linear filtering by the output pass. Same GPU saving as the
 * pixel pass, no stair-steps — the Clash-of-Clans softness the maintainer
 * asked for (world-masterplan-v2.md §3.1).
 *
 * Grain mode (2 or 3): three's `RenderPixelatedPass` as in V1, kept as a look
 * option and as the cheap-GPU fallback.
 *
 * Mounted INSIDE the Canvas; `useFrame` priority 1 takes rendering over from R3F.
 */
import { useEffect, useMemo } from "react";
import { useFrame, useThree } from "@react-three/fiber";
import { Vector2 } from "three";
import { EffectComposer } from "three/examples/jsm/postprocessing/EffectComposer.js";
import { OutputPass } from "three/examples/jsm/postprocessing/OutputPass.js";
import { RenderPass } from "three/examples/jsm/postprocessing/RenderPass.js";
import { RenderPixelatedPass } from "three/examples/jsm/postprocessing/RenderPixelatedPass.js";
import { UnrealBloomPass } from "three/examples/jsm/postprocessing/UnrealBloomPass.js";

import { useWorldSettings } from "./worldSettings";

/**
 * Bloom tuned for lamps and beacons only. Without tone mapping a sunlit white
 * wall already sits near 1.0, so the threshold must stay above it — and the
 * effect is off by default (worldSettings): the references have no bloom.
 */
const BLOOM = { strength: 0.22, radius: 0.3, threshold: 0.97 };

export function WorldComposer() {
  const gl = useThree((s) => s.gl);
  const scene = useThree((s) => s.scene);
  const camera = useThree((s) => s.camera);
  const size = useThree((s) => s.size);
  const grain = useWorldSettings((s) => s.grain);
  const scale = useWorldSettings((s) => s.scale);
  const bloom = useWorldSettings((s) => s.bloom);

  const composer = useMemo(() => {
    const c = new EffectComposer(gl);
    let pixel: RenderPixelatedPass | null = null;
    if (grain > 0) {
      pixel = new RenderPixelatedPass(grain, scene, camera, {
        normalEdgeStrength: 0.18,
        depthEdgeStrength: 0.28,
      });
      c.addPass(pixel);
    } else {
      c.addPass(new RenderPass(scene, camera));
      if (bloom) {
        c.addPass(new UnrealBloomPass(new Vector2(256, 256), BLOOM.strength, BLOOM.radius, BLOOM.threshold));
      }
    }
    c.addPass(new OutputPass());
    return { c, pixel };
  }, [gl, scene, camera, grain, bloom]);

  useEffect(() => {
    if (composer.pixel) {
      composer.c.setSize(size.width, size.height);
      composer.pixel.setPixelSize(grain);
    } else {
      // Smaller internal targets; the output pass stretches them over the canvas.
      composer.c.setSize(Math.max(2, Math.round(size.width * scale)), Math.max(2, Math.round(size.height * scale)));
    }
  }, [composer, size.width, size.height, grain, scale]);

  useEffect(() => () => composer.c.dispose(), [composer]);

  useFrame(() => {
    composer.c.render();
  }, 1);

  return null;
}
