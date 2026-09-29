import type { SVGProps } from "react";

/**
 * The fork control's mark: one trunk with a single branch leaving it in a soft
 * elbow, each end a small open ring. Deliberately lighter than lucide's
 * `GitFork` — three filled-looking circles on a Y read as a blob at the 14 px a
 * pane header draws — so it is drawn on the 24-unit grid with a thinner stroke
 * and rings small enough to stay open at header size.
 */
export function BranchIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg
      viewBox="0 0 24 24"
      width="24"
      height="24"
      aria-hidden="true"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.6}
      strokeLinecap="round"
      strokeLinejoin="round"
      {...props}
    >
      <circle cx="7" cy="5" r="2" />
      <circle cx="7" cy="19" r="2" />
      <circle cx="17" cy="5" r="2" />
      <path d="M7 7v10" />
      <path d="M17 7v1.5a3.5 3.5 0 0 1-3.5 3.5h-3A3.5 3.5 0 0 0 7 15.5" />
    </svg>
  );
}
