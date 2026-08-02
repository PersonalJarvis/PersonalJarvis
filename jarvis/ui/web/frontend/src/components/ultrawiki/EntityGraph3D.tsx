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
import { useCallback, useEffect, useRef } from "react";
import ForceGraph3D from "react-force-graph-3d";
import type { ForceGraphMethods, NodeObject } from "react-force-graph-3d";
import type { Object3D } from "three";
import SpriteText from "three-spritetext";

import { CENTRING_STRENGTH, createCentringForce } from "@/lib/graphForces";

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
const LABEL_RADIUS_FLOOR = 8;

/** How much larger the selected topic draws, so it is findable at a glance. */
const SELECTED_SCALE = 1.4;

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
      charge.strength(-220);
      // Bounded reach: past this distance a node is already outside the map
      // and pushing it further only costs the camera its framing.
      if (typeof charge.distanceMax === "function") charge.distanceMax(220);
    }
    const link = anyRef.d3Force?.("link");
    if (link && typeof link.distance === "function") {
      link.distance(55);
      if (typeof link.strength === "function") link.strength(0.2);
    }
    // A topic mentioned once, sharing a moment with nothing, has no link to
    // hold it. Without this it drifts out of the world and takes the camera
    // with it — see lib/graphForces.ts.
    anyRef.d3Force?.("centreGravity", createCentringForce(CENTRING_STRENGTH));
    forcesConfiguredRef.current = true;
  }, []);

  // Framed once the layout has settled; fitting a still-collapsing bounding
  // box would park the camera inside the cluster. The timer is the backstop
  // for a simulation that never reports a stop.
  const fitToView = useCallback((): void => {
    graphRef.current?.zoomToFit(600, 60);
  }, []);

  useEffect(() => {
    if (graphData.nodes.length === 0) return;
    const timer = window.setTimeout(fitToView, 2600);
    return () => window.clearTimeout(timer);
  }, [graphData, fitToView]);

  const nodeVal = useCallback((node: NodeObject<ExploreRenderNode>): number => {
    const base = (node as ExploreRenderNode).radius;
    const radius = node.id === selectedRef.current ? base * SELECTED_SCALE : base;
    // Cubed, because the renderer takes the cube root to get a sphere radius —
    // this is what makes the sphere exactly as wide as the flat map's dot.
    return radius ** 3;
  }, []);

  const nodeColor = useCallback(
    (node: NodeObject<ExploreRenderNode>) => (node as ExploreRenderNode).colour,
    [],
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
      sprite.textHeight = selected ? 4 : 3;
      const radius = selected ? topic.radius * SELECTED_SCALE : topic.radius;
      sprite.position.set(0, -(radius + 2.5), 0);
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
    (link: ExploreRenderEdge) => Math.min(0.2 + (link.weight ?? 1) * 0.08, 1.2),
    [],
  );

  return (
    <ForceGraph3D<ExploreRenderNode, ExploreRenderEdge>
      ref={graphRef}
      graphData={graphData}
      width={width || undefined}
      height={height || undefined}
      backgroundColor="rgba(0,0,0,0)"
      nodeId="id"
      nodeRelSize={1}
      nodeVal={nodeVal}
      nodeColor={nodeColor}
      nodeLabel={nodeLabel}
      nodeOpacity={0.9}
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
      linkColor={() => "rgba(255, 214, 10, 0.28)"}
      linkOpacity={0.22}
      linkWidth={linkWidth}
      onNodeClick={handleNodeClick}
      controlType="orbit"
      // English-only overlay baked into the library; this app ships in three
      // languages, and the switch's tooltip explains how to steer.
      showNavInfo={false}
      cooldownTicks={80}
      onEngineTick={configureForces}
      onEngineStop={fitToView}
    />
  );
}

export default EntityGraph3D;
