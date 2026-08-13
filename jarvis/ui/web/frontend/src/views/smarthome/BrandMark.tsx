/**
 * A brand's mark, on the brand's own colour.
 *
 * This replaces the single flat grey the ecosystem cards used to draw. One
 * uniform grey across twenty cards is what made the section read as unfinished:
 * nothing was recognisable at a glance, so every card had to be read as text.
 * A white glyph on the brand's colour is recognisable before the name is.
 *
 * Same three tiers the marketplace already uses (`PluginsView.resolveLogoUrl`),
 * and for the same reasons:
 *
 *   1. a full-colour SVG bundled in the app — offline-safe, no third party;
 *   2. the Simple Icons glyph, tinted for legibility, on the brand tile;
 *   3. a monogram on the brand tile, when the network or the CDN cannot help.
 *
 * Tier 3 is not a failure state that looks like one. A lettered tile in the
 * brand's colour still reads as a deliberate card, which is exactly what a
 * headless server or a locked-down network needs it to do.
 */
import { useState } from "react";

import { cn } from "@/lib/utils";

const BUNDLED_BRAND_LOGOS = import.meta.glob("../../assets/brands/*.svg", {
  eager: true,
  query: "?url",
  import: "default",
}) as Record<string, string>;

function bundledLogo(id: string): string | undefined {
  return BUNDLED_BRAND_LOGOS[`../../assets/brands/${id}.svg`];
}

const DEFAULT_TILE = "#3F3F46";

export function brandTile(logoColor: string | null | undefined): string {
  const raw = (logoColor ?? "").trim();
  if (!raw) return DEFAULT_TILE;
  return raw.startsWith("#") ? raw : `#${raw}`;
}

/**
 * A glyph colour that survives the tile behind it.
 *
 * A few brand colours are near-white and a couple are near-black, so a fixed
 * white glyph would vanish on Alexa's cyan and a fixed black one on Sonos.
 * Relative luminance decides, the same way the marketplace does it.
 */
export function glyphColor(tileHex: string): string {
  const hex = tileHex.replace("#", "");
  if (hex.length !== 6) return "ffffff";
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  const lin = (c: number) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
  const luminance = 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
  return luminance > 0.6 ? "111111" : "ffffff";
}

export interface BrandMarkProps {
  id: string;
  name: string;
  logoSlug: string;
  logoColor?: string | null;
  size?: "sm" | "md" | "lg";
  /** Drawn muted, for an ecosystem that cannot be reached at all. */
  dimmed?: boolean;
  className?: string;
}

const SIZES = {
  sm: { box: "h-9 w-9 rounded-lg", glyph: 18, text: "text-xs" },
  md: { box: "h-11 w-11 rounded-xl", glyph: 22, text: "text-sm" },
  lg: { box: "h-14 w-14 rounded-2xl", glyph: 28, text: "text-lg" },
} as const;

export function BrandMark({
  id,
  name,
  logoSlug,
  logoColor,
  size = "md",
  dimmed = false,
  className,
}: BrandMarkProps) {
  const [remoteFailed, setRemoteFailed] = useState(false);
  const bundled = bundledLogo(id);
  const tile = brandTile(logoColor);
  const glyph = glyphColor(tile);
  const dims = SIZES[size];

  // A bundled mark carries its own colours, so it sits on a neutral tile
  // rather than on the brand colour — brand-on-brand would erase it.
  if (bundled) {
    return (
      <span
        data-testid={`brand-mark-${id}`}
        className={cn(
          "flex shrink-0 items-center justify-center border border-border bg-secondary/60",
          dims.box,
          dimmed && "opacity-50 grayscale",
          className,
        )}
      >
        <img src={bundled} alt="" width={dims.glyph} height={dims.glyph} />
      </span>
    );
  }

  return (
    <span
      data-testid={`brand-mark-${id}`}
      style={{ backgroundColor: tile }}
      className={cn(
        "flex shrink-0 items-center justify-center",
        dims.box,
        dimmed && "opacity-45 grayscale",
        className,
      )}
    >
      {remoteFailed ? (
        <span
          style={{ color: `#${glyph}` }}
          className={cn("font-display font-semibold", dims.text)}
        >
          {name.slice(0, 1).toUpperCase()}
        </span>
      ) : (
        <img
          src={`https://cdn.simpleicons.org/${logoSlug}/${glyph}`}
          alt=""
          width={dims.glyph}
          height={dims.glyph}
          onError={() => setRemoteFailed(true)}
        />
      )}
    </span>
  );
}
