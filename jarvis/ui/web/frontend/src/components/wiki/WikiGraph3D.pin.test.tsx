/**
 * The hub stays nailed to the origin so the map turns around a point that
 * does not move. The camera looks at that origin; other pages orbit it.
 */
import { render } from "@testing-library/react";
import type { ForwardedRef } from "react";
import { forwardRef } from "react";
import { describe, expect, it, vi } from "vitest";

vi.mock("react-force-graph-3d", () => ({
  default: forwardRef(function ForceGraph3DStub(
    _props: Record<string, unknown>,
    _ref: ForwardedRef<unknown>,
  ) {
    return <canvas data-testid="force-graph-3d" />;
  }),
}));

vi.mock("@/hooks/useGraphOrbit", () => ({
  useGraphOrbit: vi.fn(),
}));

import { WikiGraph3D } from "@/components/wiki/WikiGraph3D";
import type { RenderNode } from "@/lib/wikiGraph";

function node(id: string, pos: { x: number; y: number; z: number }): RenderNode {
  return {
    id,
    kind: "entity",
    title: id,
    backlinkCount: 0,
    radius: 2,
    colour: "#fff",
    ...pos,
  };
}

describe("WikiGraph3D hub pin", () => {
  it("nails the hub to the origin and translates the rest with it", () => {
    const nodes = [
      node("me", { x: 40, y: -10, z: 20 }),
      node("spotify", { x: 140, y: -10, z: 20 }),
    ];

    render(
      <WikiGraph3D
        graphData={{ nodes, links: [] }}
        width={400}
        height={300}
        pivotSlug="me"
        onNodeClick={vi.fn()}
        resetSignal={0}
        nodeLabel={() => ""}
        linkLabel={() => ""}
      />,
    );

    expect(nodes[0]).toMatchObject({
      x: 0, y: 0, z: 0, fx: 0, fy: 0, fz: 0,
    });
    // The neighbour is seated on a shell, not left where the pin translated it.
    expect(Math.hypot(nodes[1].x ?? 0, nodes[1].z ?? 0)).toBeGreaterThan(50);
    expect(nodes[1].fx).toBeUndefined();
  });

  it("seats a neighbour of the sun closer in than an isolated page", () => {
    const nodes = [
      node("me", { x: 0, y: 0, z: 0 }),
      node("spotify", { x: 1, y: 0, z: 0 }),
      node("lost", { x: 2, y: 0, z: 0 }),
    ];
    const links = [
      { source: "me", target: "spotify", context: "plays", broken: false },
    ];

    render(
      <WikiGraph3D
        graphData={{ nodes, links }}
        width={400}
        height={300}
        pivotSlug="me"
        onNodeClick={vi.fn()}
        resetSignal={0}
        nodeLabel={() => ""}
        linkLabel={() => ""}
      />,
    );

    const inner = Math.hypot(nodes[1].x ?? 0, nodes[1].z ?? 0);
    const outer = Math.hypot(nodes[2].x ?? 0, nodes[2].z ?? 0);
    expect(inner).toBeGreaterThan(50);
    expect(inner).toBeLessThan(110);
    expect(outer).toBeGreaterThan(inner + 80);
  });

  it("leaves every page free when the vault has no hub", () => {
    const nodes = [node("a", { x: 8, y: 9, z: 10 })];

    render(
      <WikiGraph3D
        graphData={{ nodes, links: [] }}
        width={400}
        height={300}
        onNodeClick={vi.fn()}
        resetSignal={0}
        nodeLabel={() => ""}
        linkLabel={() => ""}
      />,
    );

    expect(nodes[0]).toMatchObject({ x: 8, y: 9, z: 10 });
    expect(nodes[0].fx).toBeUndefined();
  });
});
