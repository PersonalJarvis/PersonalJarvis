import type { SVGProps } from "react";

// The header's split buttons don't divide anything — they OPEN another
// terminal beside or below this one. So the icons show exactly that: the
// current pane as an outline, and a plus where the new pane will appear.
// (The stock divided-rectangle glyphs also read as another editor's brand.)
// Drawn on lucide's 24-unit grid with its stroke voice so they sit
// indistinguishably next to the stock icons in the pane header.

const strokeProps = {
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 2,
  strokeLinecap: "round",
  strokeLinejoin: "round",
} as const;

/** Current pane on the left, a plus marking the new pane's spot beside it. */
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
      <rect x="3" y="5" width="10" height="14" rx="2" />
      <path d="M18.5 9.5v5" />
      <path d="M16 12h5" />
    </svg>
  );
}

/** Current pane on top, a plus marking the new pane's spot below it. */
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
      <rect x="5" y="3" width="14" height="10" rx="2" />
      <path d="M12 16v5" />
      <path d="M9.5 18.5h5" />
    </svg>
  );
}
