import { Globe } from "lucide-react";

import { cn } from "@/lib/utils";
import { BRANDS } from "./brands";

/**
 * A rounded "app-icon" tile for a social platform: the brand colour as the tile
 * background, the official glyph in white on top. White-on-colour keeps every
 * logo legible on the dark UI even when the brand colour is near-black
 * (GitHub/X/TikTok). Unknown platforms fall back to a neutral globe tile.
 *
 * The glyph is decorative (the surrounding card carries the accessible label),
 * so the SVG is ``aria-hidden``.
 */
export function BrandIcon({
  platform,
  size = 40,
  className,
}: {
  platform: string;
  size?: number;
  className?: string;
}) {
  const brand = BRANDS[platform.toLowerCase()];
  const glyph = Math.round(size * 0.55);

  if (!brand) {
    return (
      <div
        className={cn(
          "flex shrink-0 items-center justify-center rounded-xl ring-1 ring-white/20",
          className,
        )}
        style={{ width: size, height: size, backgroundColor: "#3f3f46" }}
      >
        <Globe style={{ width: glyph, height: glyph }} className="text-white" aria-hidden="true" />
      </div>
    );
  }

  return (
    <div
      className={cn(
        "flex shrink-0 items-center justify-center rounded-xl ring-1 ring-white/20",
        className,
      )}
      style={{ width: size, height: size, backgroundColor: brand.hex }}
    >
      <svg
        viewBox="0 0 24 24"
        fill="#ffffff"
        style={{ width: glyph, height: glyph }}
        aria-hidden="true"
        focusable="false"
      >
        <path d={brand.path} />
      </svg>
    </div>
  );
}
