import { COLORS } from "../theme";

/** Minimal inline-SVG icon set (stroke-based, inherits a color prop). */
export const Icon: React.FC<{
  name:
    | "mic"
    | "brain"
    | "terminal"
    | "globe"
    | "check"
    | "book"
    | "bolt"
    | "robot"
    | "cursor"
    | "phone"
    | "calendar"
    | "mail";
  size?: number;
  color?: string;
}> = ({ name, size = 28, color = COLORS.primary }) => {
  const p = { fill: "none", stroke: color, strokeWidth: 2, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };
  const paths: Record<string, React.ReactNode> = {
    mic: (
      <>
        <rect x="9" y="2" width="6" height="12" rx="3" {...p} />
        <path d="M5 11a7 7 0 0 0 14 0M12 18v3" {...p} />
      </>
    ),
    brain: (
      <path
        d="M8 4a3 3 0 0 0-3 3 3 3 0 0 0-1 5 3 3 0 0 0 2 5 3 3 0 0 0 6 0V4.5A2.5 2.5 0 0 0 8 4Zm8 0a3 3 0 0 1 3 3 3 3 0 0 1 1 5 3 3 0 0 1-2 5 3 3 0 0 1-6 0"
        {...p}
      />
    ),
    terminal: (
      <>
        <rect x="3" y="4" width="18" height="16" rx="2" {...p} />
        <path d="M7 9l3 3-3 3M13 15h4" {...p} />
      </>
    ),
    globe: (
      <>
        <circle cx="12" cy="12" r="9" {...p} />
        <path d="M3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18" {...p} />
      </>
    ),
    check: <path d="M4 12l5 5L20 6" {...p} />,
    book: (
      <path d="M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2V5ZM19 19H6" {...p} />
    ),
    bolt: <path d="M13 2 4 14h7l-1 8 9-12h-7l1-8Z" {...p} />,
    robot: (
      <>
        <rect x="4" y="8" width="16" height="11" rx="2" {...p} />
        <path d="M12 5v3M9 13h.01M15 13h.01M9 8V5h6v3" {...p} />
      </>
    ),
    cursor: <path d="M5 3l14 7-6 2-2 6-6-15Z" {...p} />,
    phone: (
      <path
        d="M5 4h3l2 5-2 1a11 11 0 0 0 5 5l1-2 5 2v3a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2Z"
        {...p}
      />
    ),
    calendar: (
      <>
        <rect x="3" y="4" width="18" height="17" rx="2" {...p} />
        <path d="M3 9h18M8 2v4M16 2v4" {...p} />
      </>
    ),
    mail: (
      <>
        <rect x="3" y="5" width="18" height="14" rx="2" {...p} />
        <path d="M4 8l8 5 8-5" {...p} />
      </>
    ),
  };
  return (
    <svg width={size} height={size} viewBox="0 0 24 24">
      {paths[name]}
    </svg>
  );
};
