/**
 * The flat/space switch that sits on top of both memory maps.
 *
 * Two segments rather than a checkbox, because "off" is not what the other
 * state is: 2D and 3D are two ways of reading the same network, and a switch
 * labelled only "3D" makes the flat map look like the absence of a feature.
 * Both labels are always visible, so the current state is readable without
 * hovering anything.
 *
 * When the machine cannot give a WebGL context the 3D segment stays visible
 * but disabled and says why. Hiding it would leave the user guessing whether
 * the feature exists; letting them press it would paint a black rectangle.
 */
import { Box, Square } from "lucide-react";

import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { useGraphDimension, type GraphDimension } from "@/lib/graphDimension";

export interface GraphDimensionToggleProps {
  /** Placement + surface styling; the host owns where this floats. */
  className?: string;
}

export function GraphDimensionToggle({
  className,
}: GraphDimensionToggleProps): JSX.Element {
  const t = useT();
  const { dimension, setDimension, webglSupported } = useGraphDimension();

  const segment = (value: GraphDimension) => {
    const active = dimension === value;
    const blocked = value === "3d" && !webglSupported;
    const title = blocked
      ? t("wiki_graph.dimension_unavailable")
      : t(value === "3d" ? "wiki_graph.dimension_3d_title" : "wiki_graph.dimension_2d_title");
    const Icon = value === "3d" ? Box : Square;
    return (
      <button
        type="button"
        role="radio"
        aria-checked={active}
        aria-label={title}
        title={title}
        disabled={blocked}
        data-testid={`graph-dimension-${value}`}
        data-active={active ? "true" : "false"}
        onClick={() => setDimension(value)}
        className={cn(
          "inline-flex items-center gap-1 rounded px-2 py-1 text-[11px] font-medium tabular-nums transition-colors",
          active
            ? "bg-primary/15 text-foreground"
            : "text-muted-foreground hover:text-foreground",
          blocked && "cursor-not-allowed opacity-40 hover:text-muted-foreground",
        )}
      >
        <Icon className="h-3 w-3" aria-hidden />
        {t(value === "3d" ? "wiki_graph.dimension_3d" : "wiki_graph.dimension_2d")}
      </button>
    );
  };

  return (
    <div
      role="radiogroup"
      aria-label={t("wiki_graph.dimension_label")}
      data-testid="graph-dimension-toggle"
      data-dimension={dimension}
      data-webgl={webglSupported ? "true" : "false"}
      className={cn(
        "inline-flex items-center gap-0.5 rounded-md border border-border bg-card/80 p-0.5 backdrop-blur",
        className,
      )}
    >
      {segment("2d")}
      {segment("3d")}
    </div>
  );
}

export default GraphDimensionToggle;
