import {
  Boxes,
  Cloud,
  CreditCard,
  Database,
  GitBranch,
  Rocket,
  Terminal,
  Briefcase,
} from "lucide-react";
import type { ComponentType, SVGProps } from "react";

import { CLI_VENDOR_LOGOS, cliVendor } from "@/lib/cliVendors";
import { cn } from "@/lib/utils";

export { cliVendor };

/**
 * The vendor mark on a CLI row.
 *
 * A CLI is a binary, but a user recognises the *company* — nobody scans a list
 * for "wrangler", they look for the Cloudflare cloud. So the mapping goes
 * CLI name -> VENDOR -> file, and the vendor table is the only place a new CLI
 * needs an entry. Presentation only: nothing here decides behaviour (AP-21) —
 * a CLI with no vendor row still works, it just draws its category glyph.
 *
 * Two render paths, recorded per file in `src/assets/clis/LOGOS.md`: `colour`
 * is the vendor's full-colour icon as an <img>; `mono` is a single-colour
 * glyph drawn as a CSS mask over the current text ink, so it follows the theme
 * by itself instead of needing a light and a dark asset.
 */

/** The glyph a CLI falls back to: what kind of thing it drives. */
const CATEGORY_GLYPHS: Record<string, ComponentType<SVGProps<SVGSVGElement>>> = {
  cloud: Cloud,
  paas: Rocket,
  baas: Database,
  git: GitBranch,
  payments: CreditCard,
  container: Boxes,
  workspace: Briefcase,
  self: Terminal,
  other: Terminal,
};

// Bundled at build time (hashed URLs), so a mark renders offline and never
// calls a third party. The glob keeps the folder the single source of truth:
// dropping a file in and adding a vendor row is the whole wiring.
const BUNDLED = import.meta.glob("../../assets/clis/*.svg", {
  eager: true,
  query: "?url",
  import: "default",
}) as Record<string, string>;

export function CategoryGlyph({
  category,
  className,
}: {
  category: string;
  className?: string;
}) {
  const Glyph = CATEGORY_GLYPHS[category] ?? Terminal;
  return <Glyph className={className} />;
}

export function CliLogo({
  cliName,
  category,
  className,
  size = "md",
}: {
  /** The catalog name (`gcloud`, `wrangler`), not the display name. */
  cliName: string;
  /** Drives the fallback glyph when the CLI has no vendor mark. */
  category: string;
  className?: string;
  /** `md` for table rows, `lg` for the detail page header. */
  size?: "sm" | "md" | "lg";
}) {
  const vendor = cliVendor(cliName);
  const entry = vendor ? CLI_VENDOR_LOGOS[vendor] : undefined;
  const url = entry ? BUNDLED[`../../assets/clis/${entry.file}`] : undefined;

  const tile = {
    sm: "h-7 w-7 rounded-md",
    md: "h-9 w-9 rounded-lg",
    lg: "h-12 w-12 rounded-xl",
  }[size];
  const mark = { sm: "h-4 w-4", md: "h-5 w-5", lg: "h-7 w-7" }[size];

  return (
    <span
      aria-hidden="true"
      data-testid={`cli-logo-${cliName}`}
      data-vendor={vendor ?? undefined}
      className={cn(
        // One neutral tile for every mark, so a list of CLIs reads as one table
        // rather than a row of differently shaped app icons.
        "inline-flex shrink-0 items-center justify-center overflow-hidden border border-border bg-background",
        tile,
        className,
      )}
    >
      {url && entry?.render === "mono" ? (
        <span
          className={cn("block bg-foreground/85", mark)}
          style={{
            WebkitMaskImage: `url("${url}")`,
            maskImage: `url("${url}")`,
            WebkitMaskRepeat: "no-repeat",
            maskRepeat: "no-repeat",
            WebkitMaskPosition: "center",
            maskPosition: "center",
            WebkitMaskSize: "contain",
            maskSize: "contain",
          }}
        />
      ) : url ? (
        <img src={url} alt="" className={cn("block object-contain", mark)} />
      ) : (
        <CategoryGlyph
          category={category}
          className={cn("text-muted-foreground", mark)}
        />
      )}
    </span>
  );
}
