import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useFrame, useThree } from "@react-three/fiber";
import type * as THREE from "three";
import { CSS3DObject, CSS3DRenderer } from "three/examples/jsm/renderers/CSS3DRenderer.js";
import { PANEL_SCALE, type RoomPanel } from "@/lib/deckRoom";

/**
 * The instruments as panels in the room — real DOM, placed in 3D.
 *
 * The cards (log, terminals, the memory map, …) are the SAME React
 * components the flat board uses, with their stores, queries and
 * translations. They cannot be painted into WebGL, and drei's `<Html>`
 * would mount them in a second React root, cut off from every provider
 * above the Canvas (QueryClient, i18n, theme). And a DOM portal cannot be
 * opened from INSIDE the Canvas either — the scene is rendered by R3F's
 * own reconciler, which knows meshes, not divs.
 *
 * So the panels live in two halves that share one CSS3DRenderer:
 *
 *  - `RoomPanelsDom`, rendered OUTSIDE the Canvas in the ordinary DOM tree,
 *    mounts the renderer's overlay next to the canvas, makes one
 *    `CSS3DObject` per panel (position, turn, px→world scale), adds them to
 *    the scene it is handed, and fills each panel's element through a
 *    react-dom portal — same React root, every provider intact.
 *  - `CssTick`, rendered INSIDE the Canvas, asks the renderer to project the
 *    panels once per frame with the very camera the WebGL scene renders
 *    with — so a panel turned 0.5 rad toward the viewer at z=2.2 is exactly
 *    where its light on the floor says it is.
 *
 * Size: a panel's DOM box is `width × height` px; `PANEL_SCALE` turns px
 * into world units, so a 430 px panel stands 4.3 units wide in the room.
 * The overlay ignores the pointer; the panels themselves take it, so a
 * click lands on the card and a click beside it lands on the scene (the
 * mascot).
 */
export function useCss3dRenderer(): CSS3DRenderer {
  return useMemo(() => new CSS3DRenderer(), []);
}

/** Inside the Canvas: one projection per frame, after the WebGL render. */
export function CssTick({ renderer }: { renderer: CSS3DRenderer }) {
  const { scene, camera, size } = useThree();
  useEffect(() => {
    renderer.setSize(size.width, size.height);
  }, [renderer, size.width, size.height]);
  useFrame(() => {
    renderer.render(scene, camera);
  }, 1);
  return null;
}

/** Outside the Canvas: the overlay, the objects, the portals. */
export function RoomPanelsDom({
  renderer,
  scene,
  host,
  panels,
  render,
}: {
  renderer: CSS3DRenderer;
  /** The Canvas's scene, once it exists (from `onCreated`). */
  scene: THREE.Scene | null;
  /** The element the canvas sits in — the overlay is appended to it. */
  host: HTMLElement | null;
  panels: readonly RoomPanel[];
  /** What goes into each panel's DOM box. */
  render: (panel: RoomPanel) => ReactNode;
}) {
  // One DOM element per panel, made once, owned by its CSS3DObject. The
  // inner body is React's portal target so three's matrix never lands on a
  // node React reconciles.
  const objects = useMemo(
    () =>
      panels.map((p) => {
        const el = document.createElement("div");
        el.className = "deck-room-panel";
        el.style.width = `${p.width}px`;
        el.style.height = `${p.height}px`;
        el.dataset.panel = p.id;
        const body = document.createElement("div");
        body.className = "deck-room-panel-body";
        el.appendChild(body);
        const obj = new CSS3DObject(el);
        obj.position.set(p.position[0], p.position[1], p.position[2]);
        obj.rotation.y = p.rotationY;
        obj.scale.setScalar(PANEL_SCALE);
        return { panel: p, body, obj };
      }),
    [panels],
  );

  useLayoutEffect(() => {
    if (!host) return;
    const dom = renderer.domElement;
    dom.className = "deck-room-css3d";
    host.appendChild(dom);
    return () => {
      dom.remove();
    };
  }, [host, renderer]);

  useLayoutEffect(() => {
    if (!scene) return;
    for (const { obj } of objects) scene.add(obj);
    return () => {
      for (const { obj } of objects) scene.remove(obj);
    };
  }, [scene, objects]);

  return (
    <>
      {objects.map(({ panel, body }) => (
        <PortalInto key={panel.id} el={body}>
          {render(panel)}
        </PortalInto>
      ))}
    </>
  );
}

function PortalInto({ el, children }: { el: HTMLElement; children: ReactNode }) {
  return createPortal(children, el);
}

/**
 * The Canvas's scene, captured from `onCreated` for the DOM half. A state
 * (not a ref) on purpose: the panels must re-render once the scene exists.
 */
export function useSceneHandle(): [THREE.Scene | null, (s: THREE.Scene) => void] {
  const [scene, setScene] = useState<THREE.Scene | null>(null);
  const ref = useRef(setScene);
  return [scene, ref.current];
}
