/**
 * Half-ring gauge for speaking speed. The ring runs from 0 to a scale that
 * always leaves room above the value; a tick marks the typing reference so the
 * comparison is drawn, not only written.
 */
export function SpeedGauge({
  wpm,
  typingWpm,
  centerTop,
  centerBottom,
}: {
  wpm: number;
  typingWpm: number;
  centerTop: string;
  centerBottom: string;
}) {
  const scale = Math.max(200, Math.ceil((wpm * 1.15) / 50) * 50);
  const r = 70;
  const cx = 90;
  const cy = 86;
  const point = (fraction: number, radius = r) => {
    const angle = Math.PI * (1 - Math.min(1, Math.max(0, fraction)));
    return { x: cx + radius * Math.cos(angle), y: cy - radius * Math.sin(angle) };
  };
  const arc = (fraction: number) => {
    const end = point(fraction);
    return `M ${cx - r} ${cy} A ${r} ${r} 0 0 1 ${end.x.toFixed(2)} ${end.y.toFixed(2)}`;
  };
  const tickInner = point(typingWpm / scale, r - 13);
  const tickOuter = point(typingWpm / scale, r + 13);

  return (
    <svg viewBox="0 0 180 100" className="w-full max-w-[240px]" role="img" aria-label={`${centerTop} ${centerBottom}`}>
      <path
        d={arc(1)}
        fill="none"
        stroke="rgb(var(--sheen-rgb) / 0.08)"
        strokeWidth={16}
        strokeLinecap="round"
      />
      {wpm > 0 && (
        <path
          d={arc(wpm / scale)}
          fill="none"
          stroke="hsl(var(--accent))"
          strokeWidth={16}
          strokeLinecap="round"
        />
      )}
      <line
        x1={tickInner.x}
        y1={tickInner.y}
        x2={tickOuter.x}
        y2={tickOuter.y}
        stroke="hsl(var(--foreground))"
        strokeOpacity={0.55}
        strokeWidth={1.5}
      />
      <text x={cx} y={cy - 18} textAnchor="middle" className="fill-foreground-strong" fontSize={20} fontWeight={600}>
        {centerTop}
      </text>
      <text x={cx} y={cy - 2} textAnchor="middle" className="fill-muted-foreground" fontSize={10}>
        {centerBottom}
      </text>
    </svg>
  );
}
