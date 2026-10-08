import { cn } from "@/lib/utils";
import { GROUP_SHAPE, type KindShape, type WikiGroupId } from "@/lib/wikiModel";

/**
 * The kind of a wiki page as a small shape — the same shape the memory map
 * draws the page as, so a glance at a list row tells you what you will look
 * for on the map. Drawn in `currentColor`; the caller picks the ink.
 */
export function KindGlyph({
  group,
  className,
  title,
}: {
  group: WikiGroupId;
  className?: string;
  title?: string;
}) {
  const shape = GROUP_SHAPE[group];
  return (
    <svg
      viewBox="0 0 16 16"
      aria-hidden={title ? undefined : true}
      role={title ? "img" : undefined}
      className={cn("h-3 w-3 shrink-0", className)}
      data-kind-shape={shape}
    >
      {title && <title>{title}</title>}
      <ShapePath shape={shape} />
    </svg>
  );
}

function ShapePath({ shape }: { shape: KindShape }) {
  switch (shape) {
    case "square":
      return <rect x="2.5" y="2.5" width="11" height="11" rx="3" fill="currentColor" />;
    case "triangle":
      return <path d="M8 2 L14 13 H2 Z" fill="currentColor" strokeLinejoin="round" />;
    case "hexagon":
      return <path d="M8 1.8 L13.4 4.9 V11.1 L8 14.2 L2.6 11.1 V4.9 Z" fill="currentColor" />;
    case "diamond":
      return <path d="M8 1.5 L14.5 8 L8 14.5 L1.5 8 Z" fill="currentColor" />;
    case "ring":
      return <circle cx="8" cy="8" r="5" fill="none" stroke="currentColor" strokeWidth="2.2" />;
    case "dot":
      return <circle cx="8" cy="8" r="3.5" fill="currentColor" />;
    case "circle":
    default:
      return <circle cx="8" cy="8" r="6" fill="currentColor" />;
  }
}
