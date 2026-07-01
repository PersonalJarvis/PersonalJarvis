import { AbsoluteFill, random, useCurrentFrame, useVideoConfig } from "remotion";
import { COLORS } from "../theme";

const DOT_COUNT = 28;

/**
 * Global, continuously-animated backdrop: matte-black base, a slow drifting
 * signal-yellow glow, a faint grid and deterministic floating dots. Rendered
 * once for the whole video (uses the global frame) so motion is continuous
 * across scene cuts.
 */
export const Background: React.FC = () => {
  const frame = useCurrentFrame();
  const { width, height } = useVideoConfig();

  const gx = width * (0.5 + 0.18 * Math.sin(frame / 130));
  const gy = height * (0.42 + 0.16 * Math.cos(frame / 170));

  return (
    <AbsoluteFill style={{ backgroundColor: COLORS.bg }}>
      {/* drifting brand glow */}
      <AbsoluteFill
        style={{
          background: `radial-gradient(620px 620px at ${gx}px ${gy}px, rgba(255,214,10,0.10), rgba(255,214,10,0.0) 70%)`,
        }}
      />
      {/* faint grid */}
      <AbsoluteFill
        style={{
          backgroundImage: `linear-gradient(rgba(255,255,255,0.035) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.035) 1px, transparent 1px)`,
          backgroundSize: "64px 64px",
          maskImage:
            "radial-gradient(ellipse 90% 80% at 50% 45%, black 30%, transparent 100%)",
          WebkitMaskImage:
            "radial-gradient(ellipse 90% 80% at 50% 45%, black 30%, transparent 100%)",
        }}
      />
      {/* floating dots */}
      {new Array(DOT_COUNT).fill(0).map((_, i) => {
        const seed = `dot-${i}`;
        const baseX = random(seed + "x") * width;
        const baseY = random(seed + "y") * height;
        const speed = 0.3 + random(seed + "s") * 0.7;
        const drift = 18 + random(seed + "d") * 26;
        const x = baseX + Math.sin(frame / (90 / speed) + i) * drift;
        const y = baseY + Math.cos(frame / (110 / speed) + i) * drift;
        const size = 2 + random(seed + "z") * 3;
        const op = 0.12 + random(seed + "o") * 0.22;
        return (
          <div
            key={seed}
            style={{
              position: "absolute",
              left: x,
              top: y,
              width: size,
              height: size,
              borderRadius: "50%",
              backgroundColor: COLORS.primary,
              opacity: op,
            }}
          />
        );
      })}
      {/* vignette */}
      <AbsoluteFill
        style={{
          background:
            "radial-gradient(ellipse 75% 70% at 50% 50%, transparent 55%, rgba(0,0,0,0.55) 100%)",
        }}
      />
      {/* Fine animated grain — dithers the dark gradients so a platform encoder
          (YouTube/X) can't quantise them into visible bands/blocks. Tiny opacity,
          shifted each frame so it reads as film grain, not a static texture. */}
      <AbsoluteFill
        style={{
          opacity: 0.06,
          backgroundImage:
            "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='120' height='120'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E\")",
          backgroundRepeat: "repeat",
          backgroundSize: "120px 120px",
          backgroundPosition: `${(frame * 11) % 120}px ${(frame * 7) % 120}px`,
        }}
      />
    </AbsoluteFill>
  );
};
