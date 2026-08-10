/**
 * The topic map of UltraWiki Explore, drawn in space.
 *
 * Same nodes, same edges, same encoding as the flat map next door: size is how
 * often a topic comes up, warmth is how recently — both straight out of
 * `lib/entityGraph.ts`, so the two projections never disagree about what a
 * pixel means. Only the layout gains an axis.
 *
 * This is where the third dimension earns the most. A co-occurrence network on
 * a real corpus is far denser than a wikilink network — hundreds of topics,
 * each tied to a dozen others — and on a plane the middle of it collapses into
 * a solid mat. Given room to spread, the clusters that were hiding inside that
 * mat come apart.
 *
 * Loaded lazily by `EntityGraph`, so the WebGL renderer only arrives when the
 * switch says 3D.
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

import { CENTRING_STRENGTH, createCentringForce } from "@/lib/graphForces";
import { endpointId as endpointKey } from "@/lib/wikiGraph";
import type { Vec3 } from "@/lib/graphCamera";
import { useGraphOrbit, type GraphCameraApi } from "@/hooks/useGraphOrbit";

/** Node shape the Explore map renders — mirrors EntityGraph's RenderNode. */
export interface ExploreRenderNode {
  id: string;
  label: string;
  mentions: number;
  radius: number;
  colour: string;
}

/** Edge shape the Explore map renders — weight is shared-moment count. */
export interface ExploreRenderEdge {
  source: string;
  target: string;
  weight: number;
}

/**
 * Labelling floor, in graph units of node radius.
 *
 * The flat map shows a label once you zoom past 1.4×, for the selection, or
 * for anything bigger than this. A 3D scene has no single zoom factor to test
 * — every node sits at its own distance from the camera — so the size rule is
 * the one that carries over, and it is the one that mattered: the topics that
 * hold the network together stay named, the long tail stays quiet.
 */
const LABEL_RADIUS_FLOOR = 6;

/** How much larger the selected topic draws, so it is findable at a glance. */
const SELECTED_SCALE = 1.4;

/**
 * How much of the flat map's dot radius a sphere keeps.
 *
 * This number is the difference between a map and a bag of marbles, and the
 * arithmetic is worth writing down. What the eye sees is the PROJECTION: N
 * discs of radius r inside a cloud of radius R cover N·r²/R² of the frame.
 * At the flat map's radii (up to 12) with 250 topics in a cloud about 200
 * across, that is ninety per cent — a solid yellow wall with the network
 * hidden somewhere behind it. Every edge, every cluster, every gap was drawn
 * and then painted over.
 *
 * At 0.5 the same scene covers a few per cent and the structure is back. The
 * ORDER is untouched — a topic mentioned a hundred times is still visibly
 * bigger than one mentioned twice — which is the part that carries meaning.
 */
const SPHERE_SCALE = 0.5;

/** Let the shared glass stage and desktop artwork remain visible through WebGL. */
const SPACE_COLOUR = "rgba(0,0,0,0)";

/** Focus colours: everything unrelated to what you point at recedes. */
const LINK_REST = "rgba(255, 214, 10, 0.16)";
const LINK_FOCUS = "#ffe680";
const LINK_FADED = "rgba(255, 214, 10, 0.03)";
const NODE_FADED = "#3a352a";
const PARTICLE_COLOUR = "#fff3c4";

export interface EntityGraph3DProps {
  graphData: { nodes: ExploreRenderNode[]; links: ExploreRenderEdge[] };
  width: number;
  height: number;
  selectedKey: string | null;
  onSelect: (key: string) => void;
}

export function EntityGraph3D({
  graphData,
  width,
  height,
  selectedKey,
  onSelect,
}: EntityGraph3DProps): JSX.Element {
  const graphRef = useRef<
    ForceGraphMethods<ExploreRenderNode, ExploreRenderEdge> | undefined
  >(undefined);

  // Accessors read the selection through a ref so choosing a topic does not
  // swap the prop functions and make the renderer re-ingest every node.
  const selectedRef = useRef(selectedKey);
  selectedRef.current = selectedKey;

  // Pointing at a topic dims everything it never shared a moment with. On a
  // map this crowded that is not decoration — it is the only way to read one
  // topic's world out of a few hundred overlapping ones.
  const [hoverId, setHoverId] = useState<string | null>(null);
  const neighbours = useMemo(() => {
    const map = new Map<string, Set<string>>();
    const add = (from: string, to: string) => {
      const set = map.get(from) ?? new Set<string>();
      set.add(to);
      map.set(from, set);
    };
    for (const link of graphData.links) {
      const source = endpointKey(link.source);
      const target = endpointKey(link.target);
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
    (link: ExploreRenderEdge): boolean =>
      hoverId !== null &&
      (endpointKey(link.source) === hoverId || endpointKey(link.target) === hoverId),
    [hoverId],
  );

  const handleNodeHover = useCallback(
    (node: NodeObject<ExploreRenderNode> | null) => {
      setHoverId(node ? String(node.id ?? "") : null);
    },
    [],
  );

  // Forces are set once per data generation, never per tick: d3's setters walk
  // every node and every link, so per-frame calls are a tax on the simulation.
  const forcesConfiguredRef = useRef(false);
  useEffect(() => {
    forcesConfiguredRef.current = false;
  }, [graphData]);

  /**
   * A co-occurrence network is the densest thing this app draws, and the third
   * axis only helps if the forces spend it. On the library defaults (charge
   * −30, link 30) hundreds of topics tied to a dozen neighbours each settle
   * into one marble — the very hairball the extra dimension was meant to open.
   */
  const configureForces = useCallback((): void => {
    if (forcesConfiguredRef.current) return;
    const ref = graphRef.current;
    if (!ref) return;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const anyRef = ref as any;
    const charge = anyRef.d3Force?.("charge");
    if (charge && typeof charge.strength === "function") {
      charge.strength(-650);
      // Wide enough to reach across a crowd of a few hundred topics. Capping
      // it below the cloud's own width means the outer nodes stop feeling any
      // push at all, and the crowd never opens up.
      if (typeof charge.distanceMax === "function") charge.distanceMax(420);
    }
    const link = anyRef.d3Force?.("link");
    if (link && typeof link.distance === "function") {
      link.distance(110);
      if (typeof link.strength === "function") link.strength(0.1);
    }
    // A topic mentioned once, sharing a moment with nothing, has no link to
    // hold it. Without this it drifts out of the world and takes the camera
    // with it — see lib/graphForces.ts.
    anyRef.d3Force?.("centreGravity", createCentringForce(CENTRING_STRENGTH));
    forcesConfiguredRef.current = true;
  }, []);

  // Framing and the slow drift are one piece of state; one hook owns both
  // (hooks/useGraphOrbit.ts), and it re-frames whenever this counter changes.
  const hostRef = useRef<HTMLDivElement | null>(null);
  const [frameSignal, setFrameSignal] = useState(0);
  const reframe = useCallback(() => setFrameSignal((tick) => tick + 1), []);

  useGraphOrbit({
    graphRef: graphRef as RefObject<GraphCameraApi | undefined>,
    hostRef,
    nodes: graphData.nodes as Array<Partial<Vec3>>,
    frameSignal,
  });

  /*
   * Framing has to be repeated, not done once.
   *
   * The camera is placed from where the nodes ARE, and for the first few
   * seconds after new data they are still flying apart. Frame once, too early,
   * and the camera is parked at the radius of a cloud that has since grown
   * around it — which is how the map ended up being viewed from the inside.
   * A short schedule costs nothing (each pass is one loop over the nodes) and
   * removes the entire class of "it depends when you looked".
   *
   * The window size is a dependency for the same reason: going full-window
   * changes the aspect ratio the distance was computed against.
   */
  useEffect(() => {
    if (graphData.nodes.length === 0) return;
    reframe();
    const timers = [900, 2600, 5000, 8000].map((delay) =>
      window.setTimeout(reframe, delay),
    );
    return () => timers.forEach(window.clearTimeout);
  }, [graphData, reframe, width, height]);

  const nodeVal = useCallback((node: NodeObject<ExploreRenderNode>): number => {
    const base = (node as ExploreRenderNode).radius * SPHERE_SCALE;
    const radius = node.id === selectedRef.current ? base * SELECTED_SCALE : base;
    // Cubed, because the renderer takes the cube root to get a sphere radius —
    // so this is exactly `radius` in graph units.
    return radius ** 3;
  }, []);

  const nodeColor = useCallback(
    (node: NodeObject<ExploreRenderNode>) => {
      const topic = node as ExploreRenderNode;
      return isNearHover(String(node.id ?? "")) ? topic.colour : NODE_FADED;
    },
    [isNearHover],
  );

  const nodeLabel = useCallback(
    (node: NodeObject<ExploreRenderNode>) => (node as ExploreRenderNode).label,
    [],
  );

  const nodeThreeObject = useCallback(
    (node: NodeObject<ExploreRenderNode>): SpriteText | null => {
      const topic = node as ExploreRenderNode;
      const selected = node.id === selectedRef.current;
      if (!selected && topic.radius <= LABEL_RADIUS_FLOOR) return null;
      const sprite = new SpriteText(topic.label);
      // The selection has no ring to wear in space, so it wears the brightest
      // label instead — white against the ash-to-yellow ramp every other node
      // is tinted with.
      sprite.color = selected ? "#ffffff" : "rgba(255,255,255,0.62)";
      // Sized against the layout's spread, not the screen: the camera frames
      // the whole network, so a fixed pixel size would shrink into nothing on
      // a big corpus.
      // Big, because the cloud is: a few hundred topics spread over a couple
      // of thousand graph units means a label of six units is a smudge.
      sprite.textHeight = selected ? 22 : 16;
      const radius =
        (selected ? topic.radius * SELECTED_SCALE : topic.radius) * SPHERE_SCALE;
      sprite.position.set(0, -(radius + 4), 0);
      return sprite;
    },
    [],
  );

  const handleNodeClick = useCallback(
    (node: NodeObject<ExploreRenderNode>): void => {
      if (node?.id !== undefined) onSelect(String(node.id));
    },
    [onSelect],
  );

  const linkWidth = useCallback(
    (link: ExploreRenderEdge) =>
      touchesHover(link)
        ? 1.6
        : Math.min(0.2 + (link.weight ?? 1) * 0.08, 1.2),
    [touchesHover],
  );

  const linkColor = useCallback(
    (link: ExploreRenderEdge) => {
      if (hoverId === null) return LINK_REST;
      return touchesHover(link) ? LINK_FOCUS : LINK_FADED;
    },
    [hoverId, touchesHover],
  );

  // Only the hovered topic's world lights up. Nothing travels at rest here:
  // a co-occurrence network has thousands of edges, and a light on each of
  // them is a frame budget spent on confetti.
  const linkParticles = useCallback(
    (link: ExploreRenderEdge) => (touchesHover(link) ? 3 : 0),
    [touchesHover],
  );

  return (
    // The wrapper is what the camera work listens on to know the user has
    // taken the wheel; the renderer paints into its own canvas inside it.
    <div ref={hostRef} className="wiki-space relative h-full w-full">
    <ForceGraph3D<ExploreRenderNode, ExploreRenderEdge>
      ref={graphRef}
      graphData={graphData}
      width={width || undefined}
      height={height || undefined}
      backgroundColor={SPACE_COLOUR}
      nodeId="id"
      nodeRelSize={1}
      nodeVal={nodeVal}
      nodeColor={nodeColor}
      nodeLabel={nodeLabel}
      nodeOpacity={0.92}
      nodeResolution={10}
      // A falsy return means "no extra object, just draw the sphere" — the
      // documented behaviour — but the typings insist on an Object3D. The cast
      // is narrower than handing back an empty Object3D per unlabelled node,
      // which on a corpus of a thousand topics is a thousand scene-graph
      // entries and a thousand per-frame matrix updates for nothing.
      nodeThreeObject={
        nodeThreeObject as unknown as (
          node: NodeObject<ExploreRenderNode>,
        ) => Object3D
      }
      nodeThreeObjectExtend
      linkColor={linkColor}
      linkOpacity={0.5}
      linkWidth={linkWidth}
      linkDirectionalParticles={linkParticles}
      linkDirectionalParticleSpeed={0.005}
      linkDirectionalParticleWidth={1.4}
      linkDirectionalParticleColor={() => PARTICLE_COLOUR}
      onNodeClick={handleNodeClick}
      onNodeHover={handleNodeHover}
      controlType="orbit"
      // English-only overlay baked into the library; this app ships in three
      // languages, and the switch's tooltip explains how to steer.
      showNavInfo={false}
      // Long enough for a few hundred topics to actually finish spreading.
      // At 80 the simulation was called done while the cloud was still
      // opening, which froze the layout half-formed AND handed the camera a
      // radius that stopped being true a second later.
      cooldownTicks={220}
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

export default EntityGraph3D;
