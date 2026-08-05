/**
 * Brand marks for UltraWiki connectors.
 *
 * The Plugins store's resolution, reused rather than reinvented: the SAME
 * bundled SVGs under `src/assets/brands`, keyed by the same id, so a mark added
 * for the store shows up here for free and a card can never disagree with the
 * store about what a service looks like.
 *
 * Two tiers, not three. The store falls back to a Simple Icons glyph fetched
 * from a CDN; a knowledge base must render on a locked-down network and on a
 * headless host, so this component goes straight from "bundled mark" to
 * "monogram" and never reaches for a third party at render time. A monogram is
 * therefore a visible signal that a mark is missing from the ledger — which is
 * the point: it should look like something to fix, not like a design.
 *
 * Built-in connectors have no vendor and must not borrow one. Their brand token
 * is one of the neutral names (`vault`, `folder`, `chat`, `wiki`, `archive`)
 * and resolves to one of our own icons.
 */
import { useState } from "react";
import {
  BookOpen,
  FileArchive,
  FolderOpen,
  MessageSquare,
  Vault,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";

const BUNDLED_BRAND_LOGOS = import.meta.glob("../../assets/brands/*.svg", {
  eager: true,
  query: "?url",
  import: "default",
}) as Record<string, string>;

/** The bundled mark for a brand key, or undefined when none is bundled. */
export function brandAssetUrl(brand: string): string | undefined {
  const key = (brand ?? "").trim();
  if (!key) return undefined;
  return BUNDLED_BRAND_LOGOS[`../../assets/brands/${key}.svg`];
}

const NEUTRAL_ICONS: Record<string, LucideIcon | undefined> = {
  vault: Vault,
  folder: FolderOpen,
  chat: MessageSquare,
  wiki: BookOpen,
  archive: FileArchive,
};

/** Which tier a mark resolved to — also exposed as `data-brand-tier`, so a
 *  test can assert the fallback rather than guess at rendered pixels. */
export type BrandTier = "asset" | "neutral" | "monogram";

export function brandTierOf(brand: string): BrandTier {
  if (brandAssetUrl(brand)) return "asset";
  if (NEUTRAL_ICONS[(brand ?? "").trim()]) return "neutral";
  return "monogram";
}

const SIZES = {
  sm: { box: "h-8 w-8", mark: "h-5 w-5", text: "text-[11px]" },
  md: { box: "h-10 w-10", mark: "h-6 w-6", text: "text-sm" },
} as const;

export function ConnectorBrandMark({
  brand,
  label,
  size = "md",
  className,
  testId,
}: {
  brand: string;
  /** Only used for the monogram and the accessible name. */
  label: string;
  size?: keyof typeof SIZES;
  className?: string;
  testId?: string;
}): JSX.Element {
  const [failed, setFailed] = useState(false);
  const dims = SIZES[size];
  const asset = brandAssetUrl(brand);
  const NeutralIcon = NEUTRAL_ICONS[(brand ?? "").trim()];
  const tier: BrandTier = failed
    ? "monogram"
    : asset
      ? "asset"
      : NeutralIcon
        ? "neutral"
        : "monogram";

  return (
    <div
      className={cn(
        "grid shrink-0 place-items-center overflow-hidden rounded-lg border",
        dims.box,
        // A full-colour vendor mark sits on a light plate (its own colours
        // carry the brand); our neutral icons and monograms sit on the card's
        // own surface, so a built-in never masquerades as a vendor tile.
        tier === "asset"
          ? "border-sheen/10 bg-sheen/[0.07]"
          : "border-border/60 bg-secondary/40",
        className,
      )}
      data-testid={testId}
      data-brand={brand || "none"}
      data-brand-tier={tier}
      title={label}
    >
      {tier === "asset" && asset ? (
        <img
          src={asset}
          alt=""
          className={dims.mark}
          loading="lazy"
          onError={() => setFailed(true)}
        />
      ) : tier === "neutral" && NeutralIcon ? (
        <NeutralIcon
          className={cn(dims.mark, "text-muted-foreground")}
          aria-hidden
        />
      ) : (
        <span className={cn("font-semibold text-foreground", dims.text)}>
          {(label || "?").slice(0, 1).toUpperCase()}
        </span>
      )}
    </div>
  );
}
