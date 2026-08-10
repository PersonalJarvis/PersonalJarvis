import { render, screen } from "@testing-library/react";
import type { ForwardedRef } from "react";
import { forwardRef } from "react";
import { describe, expect, it, vi } from "vitest";

vi.mock("react-force-graph-3d", () => ({
  default: forwardRef(function ForceGraph3DStub(
    props: { backgroundColor?: string },
    _ref: ForwardedRef<unknown>,
  ) {
    return (
      <canvas
        data-testid="force-graph-3d"
        data-background-color={props.backgroundColor}
      />
    );
  }),
}));

vi.mock("@/hooks/useGraphOrbit", () => ({
  useGraphOrbit: vi.fn(),
}));

import { EntityGraph3D } from "@/components/ultrawiki/EntityGraph3D";
import { WikiGraph3D } from "@/components/wiki/WikiGraph3D";

describe("3D graph stage transparency", () => {
  it("lets the wallpaper show through the Wiki graph canvas", () => {
    render(
      <WikiGraph3D
        graphData={{ nodes: [], links: [] }}
        width={800}
        height={600}
        onNodeClick={vi.fn()}
        resetSignal={0}
        nodeLabel={() => ""}
        linkLabel={() => ""}
      />,
    );

    expect(
      screen
        .getByTestId("force-graph-3d")
        .getAttribute("data-background-color"),
    ).toBe("rgba(0,0,0,0)");
  });

  it("lets the wallpaper show through the UltraWiki graph canvas", () => {
    render(
      <EntityGraph3D
        graphData={{ nodes: [], links: [] }}
        width={800}
        height={600}
        selectedKey={null}
        onSelect={vi.fn()}
      />,
    );

    expect(
      screen
        .getByTestId("force-graph-3d")
        .getAttribute("data-background-color"),
    ).toBe("rgba(0,0,0,0)");
  });
});
