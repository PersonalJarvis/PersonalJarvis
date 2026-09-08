/** Three modular residential silhouettes, baked into one draw call each. */
import { useEffect, useMemo } from "react";
import { BoxGeometry, Color, Float32BufferAttribute, Matrix4, MeshToonMaterial } from "three";
import { mergeGeometries } from "three/examples/jsm/utils/BufferGeometryUtils.js";
import type { HousePlot } from "./islandLayout";
import { BUILDING } from "./worldPalette";
import { createToonRamp } from "./worldMaterials";

export function HouseMesh({ plot }: { plot: HousePlot }) {
  const built = useMemo(() => {
    const geometries: BoxGeometry[] = [];
    const w = plot.w * 2, d = plot.d * 2, h = 3.4;
    const box = (size: [number, number, number], at: [number, number, number], hex: string) => {
      const geo = new BoxGeometry(...size).toNonIndexed() as BoxGeometry;
      geo.applyMatrix4(new Matrix4().makeTranslation(...at));
      const n = geo.getAttribute("normal"), colors: number[] = [], c = new Color(hex);
      for (let i = 0; i < n.count; i++) {
        const shade = n.getY(i) > .5 ? 1 : n.getZ(i) > .5 ? .94 : n.getX(i) > .5 ? .80 : .58;
        colors.push(c.r * shade, c.g * shade, c.b * shade);
      }
      geo.setAttribute("color", new Float32BufferAttribute(colors, 3)); geometries.push(geo);
    };
    const front = d / 2 - .24;
    box([w, .2, d], [0, .1, 0], BUILDING.trim);
    box([w - .4, h, d - .4], [0, h / 2 + .2, 0], plot.seed > .5 ? BUILDING.wall : BUILDING.wallShade);
    box([w - .3, .28, d - .3], [0, h + .27, 0], BUILDING.solar);
    box([1.9, 2.55, .10], [0, 1.48, front], BUILDING.solar);
    box([1.62, 2.28, .12], [0, 1.43, front + .03], BUILDING.glass);
    box([.10, 2.28, .13], [0, 1.43, front + .05], BUILDING.solar);
    for (const side of [-1, 1]) {
      box([1.2, 1.15, .10], [side * (w / 2 - 1.1), 2.1, front], BUILDING.glass);
      box([1.4, .15, .16], [side * (w / 2 - 1.1), 1.48, front], BUILDING.solarLine);
      box([.10, 1.25, 1.6], [side * (w / 2 - .17), 2.1, 0], BUILDING.glass);
      box([1.3, 1.1, .10], [side * w * .23, 2.1, -front], BUILDING.glass);
    }
    box([2.3, .20, .42], [0, 2.85, front - .05], BUILDING.wood);
    if (plot.variant === "garden-roof") {
      box([w - .7, .30, d - .7], [0, h + .53, 0], BUILDING.gardenRoof);
      for (const side of [-1, 1]) box([1.0, .65, 1.25], [side * w * .26, h + .85, 0], BUILDING.gardenRoofBush);
    } else if (plot.variant === "glass-loft") {
      box([w - 1.6, 1.45, d - 1.3], [0, h + 1.05, 0], BUILDING.glass);
      box([w - 1.2, .25, d - 1.0], [0, h + 1.9, 0], BUILDING.solar);
    } else {
      for (let i = 0; i < 3; i++) box([w - .7, .35, (d - .8) / 3], [0, h + .60 - Math.abs(i - 1) * .1, (i - 1) * (d - .7) / 3], BUILDING.solar);
      box([w - .65, .1, .16], [0, h + .84, 0], BUILDING.solarLine);
    }
    const geometry = mergeGeometries(geometries, false)!;
    geometries.forEach(g => g.dispose());
    const ramp = createToonRamp(), material = new MeshToonMaterial({ vertexColors: true, gradientMap: ramp });
    return { geometry, material, ramp };
  }, [plot.w, plot.d, plot.variant, plot.seed]);
  useEffect(() => () => { built.geometry.dispose(); built.material.dispose(); built.ramp.dispose(); }, [built]);
  return <mesh geometry={built.geometry} material={built.material} castShadow receiveShadow />;
}
