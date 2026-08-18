/**
 * The vault Memory Map, drawn in space instead of on a plane.
 *
 * This is the SAME graph as `WikiGraph`'s flat canvas — same request, same
 * nodes, same colours, same size encoding out of `lib/wikiGraph.ts`. Only the
 * projection changes, which is the whole point: a dense wikilink network runs
 * out of room in two dimensions long before it runs out of structure, and
 * clusters that overlap into a hairball on a plane pull apart when the layout
 * has a third axis to spend.
 *
 * It is loaded lazily and only ever mounted while the 2D/3D switch says 3D, so
 * the WebGL renderer (the largest dependency in the app by a wide margin) is
 * never downloaded by someone who reads the flat map.
 *
 * Two things the flat map does that space cannot, and what replaces them:
 *  - Dashed lines for unresolved wikilinks. The 3D renderer draws solid tubes
 *    only, so a broken link keeps its rose colour and loses its arrow instead.
 *  - Always-on labels. Text in a 3D scene is a textured sprite, one draw call
 *    each, and a thousand of them turn a smooth orbit into a slideshow — so
 *    labels go to the nodes that carry the network plus whatever is selected.
 */
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type RefObject,
} from "react";
import ForceGraph3D from "react-force-graph-3d";
import type { ForceGraphMethods, NodeObject } from "react-force-graph-3d";
import type { Object3D } from "three";
import SpriteText from "three-spritetext";

import {
  BROKEN_EDGE_COLOUR,
  NODE_COLOUR,
  endpointId,
  nodeSizeScore,
  type RenderEdge,
  type RenderNode,
} from "@/lib/wikiGraph";
import {
  CENTRING_STRENGTH,
  createCentringForce,
  createLivelinessForce,
  type LivelyNode,
} from "@/lib/graphForces";
import { carryOverPositions, pinPivotAtOrigin } from "@/lib/graphContinuity";
import type { Vec3 } from "@/lib/graphCamera";
import { useGraphOrbit, type GraphCameraApi } from "@/hooks/useGraphOrbit";

/** The hub is nailed here; the camera looks here. Never a live, moving node. */
const PINNED_ORIGIN: Vec3 = { x: 0, y: 0, z: 0 };

/** Sphere radius in graph units for a node whose size score is 1.0. */
const NODE_REL_SIZE = 3;

/**
 * Above this many nodes, only hubs and the selection get a text sprite.
 * Below it every node is labelled, the way the flat map does it.
 */
const LABEL_ALL_BELOW = 220;

/** A hub, once the scene is too crowded to label everything. */
const HUB_BACKLINKS = 2;

/**
 * How many links carry a travelling light at rest.
 *
 * The particles are what make the map look alive rather than photographed,
 * but each one is geometry the GPU draws every frame, and a vault with a
 * thousand wikilinks would spend the whole frame budget on decoration. So they
 * go to the busiest links — the ones that carry the structure — and the rest
 * stay quiet until you hover them.
 */
const AMBIENT_PARTICLE_LINKS = 90;

/** Colours for the focus state. Everything not near the pointer recedes. */
const LINK_REST = "rgba(106, 169, 255, 0.5)";
const LINK_FOCUS = "#9ecbff";
const LINK_FADED = "rgba(106, 169, 255, 0.06)";
const NODE_FADED = "#2c3340";
const PARTICLE_COLOUR = "#cfe4ff";

/** Let the shared glass stage and desktop artwork remain visible through WebGL. */
const SPACE_COLOUR = "rgba(0,0,0,0)";

export interface WikiGraph3DProps {
  graphData: { nodes: RenderNode[]; links: RenderEdge[] };
  width: number;
  height: number;
  highlightSlug?: string;
  /**
   * The page the ambient orbit turns around — the user's own entity page when
   * the vault has one (`hub` in the graph payload). Absent, the camera turns
   * around the network's middle as before.
   */
  pivotSlug?: string | null;
  onNodeClick: (slug: string) => void;
  /** Any change re-frames the camera; the host's Center button bumps it. */
  resetSignal: number;
  /** Tooltip text for a node — same wording as the flat map's. */
  nodeLabel: (node: NodeObject<RenderNode>) => string;
  /** Tooltip text for an edge. */
  linkLabel: (link: RenderEdge) => string;
}

export function WikiGraph3D({
  graphData,
  width,
  height,
  highlightSlug,
  pivotSlug = null,
  onNodeClick,
  resetSignal,
  nodeLabel,
  linkLabel,
}: WikiGraph3DProps): JSX.Element {
  const graphRef = useRef<ForceGraphMethods<RenderNode, RenderEdge> | undefined>(
    undefined,
  );

  // Read through a ref for the same reason the flat map does: swapping the
  // accessor functions makes the library re-ingest its whole accessor set, and
  // a selection change only needs the next painted frame to read a new value.
  const highlightRef = useRef(highlightSlug);
  highlightRef.current = highlightSlug;

  const labelEverything = graphData.nodes.length <= LABEL_ALL_BELOW;

  // Pointing at a node dims everything it has nothing to do with. On a network
  // this dense that is not a flourish — it is the only way to read a single
  // page's neighbourhood out of a thousand crossing lines.
  const [hoverId, setHoverId] = useState<string | null>(null);
  const neighbours = useMemo(() => {
    const map = new Map<string, Set<string>>();
    const add = (from: string, to: string) => {
      const set = map.get(from) ?? new Set<string>();
      set.add(to);
      map.set(from, set);
    };
    for (const link of graphData.links) {
      const source = endpointId(link.source);
      const target = endpointId(link.target);
      if (!source || !target) continue;
      add(source, target);
      add(target, source);
    }
    return map;
  }, [graphData.links]);

  const isNearHover = useCallback(
    (id: string): boolean =>
      hoverId === null ||
      id === hoverId ||
      (neighbours.get(hoverId)?.has(id) ?? false),
    [hoverId, neighbours],
  );

  const touchesHover = useCallback(
    (link: RenderEdge): boolean =>
      hoverId !== null &&
      (endpointId(link.source) === hoverId || endpointId(link.target) === hoverId),
    [hoverId],
  );

  // The links that carry a travelling light while nothing is hovered: the
  // busiest ones, capped, so the effect scales to a vault of any size.
  const ambientParticleLinks = useMemo(() => {
    const weight = new Map<string, number>();
    for (const node of graphData.nodes) {
      weight.set(node.id, node.backlinkCount ?? 0);
    }
    return new Set(
      [...graphData.links]
        .filter((link) => !link.broken)
        .sort(
          (a, b) =>
            (weight.get(endpointId(b.target)) ?? 0) -
            (weight.get(endpointId(a.target)) ?? 0),
        )
        .slice(0, AMBIENT_PARTICLE_LINKS),
    );
  }, [graphData]);

  // Forces are set once per data generation, never per tick — d3's setters
  // re-initialise the force over every node and every link, so calling them
  // per frame is an O(n) tax on the simulation. Same discipline as the flat
  // map's `configureForces`.
  const forcesConfiguredRef = useRef(false);
  useEffect(() => {
    forcesConfiguredRef.current = false;
  }, [graphData]);
  const pivotSlugRef = useRef(pivotSlug);
  pivotSlugRef.current = pivotSlug;
  /** The live pivot node object (set below, once the data is known). */
  const pivotNodeRef = useRef<Partial<Vec3> | null>(null);
  const reducedMotion = useMemo(
    () =>
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches,
    [],
  );

  /**
   * A third axis does not spread a dense graph on its own — the FORCES have to
   * want the room.
   *
   * The flat map tunes its own (charge −180, link 55) and those numbers live
   * inside the 2D renderer, so a 3D scene left on the library defaults
   * (charge −30, link 30) collapsed a vault of a thousand wikilinks into one
   * marble. Space costs repulsion: a stronger charge and a longer, slacker
   * link are what turn the marble back into a network you can fly through.
   */
  const configureForces = useCallback((): void => {
    if (forcesConfiguredRef.current) return;
    const ref = graphRef.current;
    if (!ref) return;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const anyRef = ref as any;
    const charge = anyRef.d3Force?.("charge");
    if (charge && typeof charge.strength === "function") {
      charge.strength(-420);
      // Same reach as the flat map's. A larger radius keeps pushing a node
      // that is already outside the cluster, which is how a single unlinked
      // page ends up a long way from everything and drags the camera with it.
      if (typeof charge.distanceMax === "function") charge.distanceMax(220);
    }
    const link = anyRef.d3Force?.("link");
    if (link && typeof link.distance === "function") {
      link.distance(85);
      // Slacker than the flat map's 0.85: in three dimensions a stiff link
      // pulls the cluster back into the ball the repulsion just opened. A hub
      // with fifty children would otherwise drag them all into its own pixel.
      if (typeof link.strength === "function") link.strength(0.16);
    }
    // Bounds the world. Without it a single unlinked page drifts until it is
    // out of everyone's reach, and `zoomToFit` — which frames every node —
    // renders the entire vault as a marble in an empty room.
    anyRef.d3Force?.("centreGravity", createCentringForce(CENTRING_STRENGTH));
    // Every page but the pivot keeps moving on its own — a bob and a small
    // loop, each with its own rhythm — so the map is not a rigid body turning
    // (maintainer, 2026-08-18). The pivot is read through a ref at tick time,
    // so a change of pivot pins the new page without rebuilding the forces.
    // Left out under prefers-reduced-motion, same as the ambient turn.
    if (!reducedMotion) {
      anyRef.d3Force?.(
        "liveliness",
        createLivelinessForce({
          now: () => performance.now(),
          isPinned: (node: LivelyNode) => node.id === pivotSlugRef.current,
          // The network breathes from the pivot page, wherever the layout
          // has it this tick; from the origin when the vault has no pivot.
          centre: () => {
            const pivot = pivotNodeRef.current;
            return pivot && Number.isFinite(pivot.x)
              ? { x: pivot.x ?? 0, y: pivot.y ?? 0, z: pivot.z ?? 0 }
              : { x: 0, y: 0, z: 0 };
          },
        }),
      );
    }
    forcesConfiguredRef.current = true;
  }, [reducedMotion]);

  // Framing and the slow drift are one piece of state, so one hook owns both
  // (see hooks/useGraphOrbit.ts). It re-frames whenever this counter changes:
  // new data, a settled layout, or the host's Center button.
  const hostRef = useRef<HTMLDivElement | null>(null);
  const [frameSignal, setFrameSignal] = useState(0);
  const reframe = useCallback(() => setFrameSignal((tick) => tick + 1), []);

  // The live node object for the hub — liveliness and drag-lock read it.
  // The camera does not: it looks at the origin the hub is pinned to, so a
  // settling layout cannot walk the main page off the middle of the panel.
  const pivotNode = useMemo(
    () => (pivotSlug ? (graphData.nodes.find((n) => n.id === pivotSlug) ?? null) : null),
    [graphData.nodes, pivotSlug],
  );
  pivotNodeRef.current = pivotNode as Partial<Vec3> | null;

  useGraphOrbit({
    graphRef: graphRef as RefObject<GraphCameraApi | undefined>,
    hostRef,
    nodes: graphData.nodes as Array<Partial<Vec3>>,
    pivot: pivotSlug ? PINNED_ORIGIN : null,
    frameSignal,
  });

  /*
   * The host asked for a reset, or the data changed under us — and framing has
   * to be repeated rather than done once. The camera is placed from where the
   * nodes ARE, and for the first few seconds after new data they are still
   * flying apart; framing once, too early, parks the camera at the radius of a
   * cloud that then grows around it. The window size is a dependency for the
   * same reason: going full-window changes the aspect ratio the distance was
   * computed against.
   */
  useEffect(() => {
    if (graphData.nodes.length === 0) return;
    reframe();
    // The last one stands in for the engine-stop re-frame there used to be:
    // the layout now ticks for good (the pages keep moving), so "settled" is
    // a matter of time, and by then alpha has long since gone cold.
    const timers = [900, 2600, 5000, 8000, 12_000].map((delay) =>
      window.setTimeout(reframe, delay),
    );
    return () => timers.forEach(window.clearTimeout);
  }, [graphData, resetSignal, reframe, width, height]);

  const handleNodeClick = useCallback(
    (node: NodeObject<RenderNode>): void => {
      const id = (node.id as string | undefined) ?? "";
      if (id) onNodeClick(id);
    },
    [onNodeClick],
  );

  const nodeVal = useCallback((node: NodeObject<RenderNode>): number => {
    const score = nodeSizeScore(
      (node as RenderNode).backlinkCount ?? 0,
      node.id === highlightRef.current,
    );
    // The renderer takes the CUBE ROOT of this to get a sphere radius, so
    // cubing here is what makes `nodeSizeScore` mean the same thing in both
    // maps: radius = NODE_REL_SIZE * score, exactly as on the plane.
    return score ** 3;
  }, []);

  const nodeColor = useCallback(
    (node: NodeObject<RenderNode>) => {
      const own = (node as RenderNode).colour ?? NODE_COLOUR.entity ?? "#8b95a7";
      return isNearHover(String(node.id ?? "")) ? own : NODE_FADED;
    },
    [isNearHover],
  );

  const nodeThreeObject = useCallback(
    (node: NodeObject<RenderNode>): SpriteText | null => {
      const rendered = node as RenderNode;
      const isActive = node.id === highlightRef.current;
      const backlinks = rendered.backlinkCount ?? 0;
      if (!labelEverything && !isActive && backlinks < HUB_BACKLINKS) {
        // No extra object — the sphere the renderer draws is the whole node.
        return null;
      }
      const label = rendered.title || String(node.id ?? "");
      if (!label) return null;
      const sprite = new SpriteText(label);
      sprite.color = isActive ? "#e6ecf5" : "#a8b0c0";
      // Sized against the layout, not the screen: the camera frames the whole
      // network, so a label has to be a readable fraction of the SPREAD
      // between nodes rather than a fixed number of pixels.
      sprite.textHeight = isActive ? 5.5 : 4;
      // Clear of the sphere, so a hub's label does not sit inside its own dot.
      const radius = NODE_REL_SIZE * nodeSizeScore(backlinks, isActive);
      sprite.position.set(0, -(radius + 2), 0);
      return sprite;
    },
    [labelEverything],
  );

  const linkColor = useCallback(
    (link: RenderEdge) => {
      if (hoverId !== null) {
        if (!touchesHover(link)) return LINK_FADED;
        return link.broken ? BROKEN_EDGE_COLOUR : LINK_FOCUS;
      }
      return link.broken ? BROKEN_EDGE_COLOUR : LINK_REST;
    },
    [hoverId, touchesHover],
  );

  const linkWidth = useCallback(
    (link: RenderEdge) => (touchesHover(link) ? 1.4 : 0.4),
    [touchesHover],
  );

  // Light travelling from source to target: direction you can read at a
  // glance, and the thing that makes the map look like it is running rather
  // than sitting there. Hovering floods the neighbourhood with it.
  const linkParticles = useCallback(
    (link: RenderEdge) => {
      if (hoverId !== null) return touchesHover(link) ? 4 : 0;
      return ambientParticleLinks.has(link) ? 2 : 0;
    },
    [ambientParticleLinks, hoverId, touchesHover],
  );

  const handleNodeHover = useCallback((node: NodeObject<RenderNode> | null) => {
    setHoverId(node ? String(node.id ?? "") : null);
  }, []);

  // An unresolved link points at a page that does not exist, so an arrowhead
  // claiming a destination would be the wrong story; the rose colour carries
  // it instead — the same signal the flat map's dashes carry.
  const linkArrowLength = useCallback(
    (link: RenderEdge) => (link.broken ? 0 : 3.5),
    [],
  );

  const linkArrowColor = useCallback(
    () => "rgba(106, 169, 255, 0.85)",
    [],
  );

  // New data must not explode the map. The renderer seeds every node that
  // arrives without a position from scratch, so before a new generation
  // reaches it, pages that were on the map keep the place (and the
  // liveliness offset) their previous object had, and a NEW page is seated
  // next to the pages it links to. Same objects the simulation integrates,
  // mutated once per generation, before the renderer sees them.
  const previousNodesRef = useRef<RenderNode[]>([]);
  const data = useMemo(() => {
    const previous = previousNodesRef.current;
    if (previous !== graphData.nodes) {
      const pivot = previous.find((n) => n.id === pivotSlug);
      carryOverPositions(previous, graphData.nodes, graphData.links, pivot ?? {});
      previousNodesRef.current = graphData.nodes;
    }
    // First generation, every refresh, and a late-arriving hub: the main
    // page sits at the origin and stays there. The rest of the cloud is
    // translated with it so the layout they already have is not torn up.
    pinPivotAtOrigin(graphData.nodes, pivotSlug);
    return graphData;
  }, [graphData, pivotSlug]);

  const holdHub = useCallback((node: NodeObject<RenderNode>): void => {
    if (node.id !== pivotSlugRef.current) return;
    node.fx = 0;
    node.fy = 0;
    node.fz = 0;
    node.x = 0;
    node.y = 0;
    node.z = 0;
    node.vx = 0;
    node.vy = 0;
    node.vz = 0;
  }, []);

  return (
    // The renderer paints into its own canvas; this wrapper is what the camera
    // work listens on to know the user has taken the wheel, and what carries
    // the space the network floats in (see .wiki-space in index.css).
    <div ref={hostRef} className="wiki-space relative h-full w-full">
    <ForceGraph3D<RenderNode, RenderEdge>
      ref={graphRef}
      graphData={data}
      width={width}
      height={height}
      backgroundColor={SPACE_COLOUR}
      nodeId="id"
      nodeLabel={nodeLabel}
      nodeRelSize={NODE_REL_SIZE}
      nodeVal={nodeVal}
      nodeColor={nodeColor}
      nodeOpacity={0.92}
      nodeResolution={12}
      // A falsy return means "no extra object, just draw the sphere" — the
      // documented behaviour — but the typings insist on an Object3D. The cast
      // is narrower than handing back an empty Object3D per unlabelled node,
      // which would buy a scene-graph entry and a per-frame matrix update on
      // exactly the crowded graphs where the labels were dropped for speed.
      nodeThreeObject={
        nodeThreeObject as unknown as (node: NodeObject<RenderNode>) => Object3D
      }
      nodeThreeObjectExtend
      linkLabel={linkLabel}
      linkColor={linkColor}
      linkOpacity={0.6}
      linkWidth={linkWidth}
      linkDirectionalArrowLength={linkArrowLength}
      linkDirectionalArrowRelPos={0.85}
      linkDirectionalArrowColor={linkArrowColor}
      linkDirectionalParticles={linkParticles}
      linkDirectionalParticleSpeed={0.006}
      linkDirectionalParticleWidth={1.3}
      linkDirectionalParticleColor={() => PARTICLE_COLOUR}
      onNodeClick={handleNodeClick}
      onNodeHover={handleNodeHover}
      onNodeDrag={holdHub}
      onNodeDragEnd={holdHub}
      // Drag to rotate, wheel to zoom, right-drag to pan — the mapping people
      // already know from every other 3D viewer.
      controlType="orbit"
      // The built-in hint overlay is English-only text baked into the library,
      // and this app ships in three languages. The switch's own tooltip says
      // how to steer instead.
      showNavInfo={false}
      // The engine never stops: the renderer only pushes node and link
      // positions to the scene while it ticks, and the liveliness force
      // needs that every frame. The layout itself still cools (alpha decays
      // as before, and its forces fade with it) — only the life stays on.
      // Under reduced motion the engine cools down and stops as it used to.
      cooldownTicks={reducedMotion ? 200 : Infinity}
      cooldownTime={reducedMotion ? 15_000 : Infinity}
      d3VelocityDecay={0.6}
      d3AlphaDecay={0.04}
      warmupTicks={40}
      onEngineTick={configureForces}
      onEngineStop={reframe}
    />
      <div
        className="wiki-space-vignette pointer-events-none absolute inset-0"
        aria-hidden
      />
    </div>
  );
}

export default WikiGraph3D;
