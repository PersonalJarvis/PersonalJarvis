/**
 * One capability as a tile on the agent card — the large sibling of
 * `CapabilityChip`.
 *
 * The chip is for a dense row; this is for the card's HANDS block, where the
 * point is that you recognise what the agent does before you read a word:
 * Gmail's M, the Calendar tile, GitHub's cat, at 56px.
 *
 * Both resolve their mark through the same `lib/toolBrand.ts`, so a new SVG
 * dropped into `assets/brands/` lights up the chip, the tile, the plugins
 * store and the turn view at once — nothing to register.
 *
 * When a tool has no brand, the monogram sits on the AGENT's own colour, not
 * on a neutral disc: "never a grey placeholder, never a monogram on a neutral
 * disc" is the rule the palette doc and `assets/brands/LOGOS.md` both set.
 */
import { resolveToolBrand } from "@/lib/toolBrand";

import { capabilityName } from "../CapabilityChip";
import type { AgentPalette, Capability } from "../data";

/**
 * Readable ink for a coloured tile — WCAG relative luminance, the same test
 * the plugins store makes before it drops a glyph on a brand colour.
 */
function inkOn(hex: string): string {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return "#1f2a3a";
  const n = parseInt(m[1], 16);
  const channel = (c: number) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  const l =
    0.2126 * channel((n >> 16) & 255) +
    0.7152 * channel((n >> 8) & 255) +
    0.0722 * channel(n & 255);
  return l > 0.45 ? "#1f2a3a" : "#fffcf5";
}

export interface CapabilityTileProps {
  /** A capability id ("plugin:gmail") or a bare tool name. */
  id: string;
  /** The catalog row when it is known — label, one-liner, risk tier. */
  capability?: Capability | null;
  /** The agent's own colours, for the tile behind a monogram. */
  palette: AgentPalette;
  /** Text appended to the title when the catalog reports the tool as absent. */
  disconnectedHint?: string;
}

export function CapabilityTile({ id, capability, palette, disconnectedHint }: CapabilityTileProps) {
  const brand = resolveToolBrand(capability?.tool_name || capabilityName(id));
  // A matched brand names itself better than the catalog does: the catalog's
  // label for `plugin:gmail` is the raw registry name "gmail", the brand's is
  // "Gmail". Everything unbranded keeps the catalog's wording.
  const label = brand.brandId ? brand.label : (capability?.label ?? brand.label);
  const connected = capability ? capability.connected : true;
  const risk = capability?.risk_tier;
  const title =
    [capability?.one_liner, connected ? null : disconnectedHint].filter(Boolean).join(" — ") || label;

  return (
    <li
      className="ac-tile"
      title={title}
      data-testid={`capability-tile-${id}`}
      style={connected ? undefined : { opacity: 0.5 }}
    >
      <span
        className="ac-tile-mark"
        style={
          brand.logoUrl
            ? undefined
            : { background: palette.primary, color: inkOn(palette.primary), borderColor: "transparent" }
        }
      >
        {brand.logoUrl ? (
          <img src={brand.logoUrl} alt="" draggable={false} />
        ) : (
          <span className="ac-tile-monogram">{brand.monogram}</span>
        )}
        {risk ? <span className="ac-tile-risk" data-risk={risk} aria-hidden /> : null}
      </span>
      <span className="ac-tile-label">{label}</span>
    </li>
  );
}
