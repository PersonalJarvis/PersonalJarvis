import type { SVGProps } from "react";

// The header's split buttons open another terminal beside or below this one.
// The icons say it as "Half + Arrow": the pane already divided, with an arrow
// in the fresh half pointing the way the layout grows — picked as design #17
// from the twenty-candidate gallery. Drawn on lucide's 24-unit grid with its
// stroke voice so they sit indistinguishably next to the stock icons in the
// pane header.

const strokeProps = {
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 2,
  strokeLinecap: "round",
  strokeLinejoin: "round",
} as const;

/** Divided pane; the arrow in the right half points where the new pane opens. */
export function SplitRightIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg
      viewBox="0 0 24 24"
      width="24"
      height="24"
      aria-hidden="true"
      {...strokeProps}
      {...props}
    >
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="M10 4v16" />
      <path d="M13 12h5" />
      <path d="m15.5 9.5 2.5 2.5-2.5 2.5" />
    </svg>
  );
}

/** Divided pane; the arrow in the lower half points where the new pane opens. */
export function SplitBelowIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg
      viewBox="0 0 24 24"
      width="24"
      height="24"
      aria-hidden="true"
      {...strokeProps}
      {...props}
    >
      <rect x="4" y="3" width="16" height="18" rx="2" />
      <path d="M4 10h16" />
      <path d="M12 13v5" />
      <path d="m9.5 15.5 2.5 2.5 2.5-2.5" />
    </svg>
  );
}
