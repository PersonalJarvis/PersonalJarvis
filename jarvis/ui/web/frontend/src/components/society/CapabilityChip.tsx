/**
 * One capability as a chip with the service's real mark — Gmail's M, the
 * Google Calendar tile, GitHub's cat — resolved by `lib/toolBrand.ts` from
 * the bundled SVGs under `assets/brands` (nominative use, see its LOGOS.md).
 * A tool with no brand shows its monogram on the agent-neutral tile; a
 * capability the catalog reports as not connected is drawn dimmed with the
 * reason in its title, never hidden.
 */
import { cn } from "@/lib/utils";
import { resolveToolBrand } from "@/lib/toolBrand";

import type { Capability } from "./data";

/** "plugin:google-calendar" → "google-calendar"; "core:search-web" → "search-web". */
export function capabilityName(id: string): string {
  return id.replace(/^(plugin|cli|mcp|skill|core):/, "");
}

export function capabilityKind(id: string): string | null {
  const m = /^(plugin|cli|mcp|skill|core):/.exec(id);
  return m ? m[1] : null;
}

export interface CapabilityChipProps {
  /** A capability id ("plugin:gmail") or a bare tool name ("web_search"). */
  id: string;
  /** The catalog row when known — label, one-liner and connected state. */
  capability?: Capability | null;
  selected?: boolean;
  onClick?: () => void;
  size?: "sm" | "md";
  className?: string;
  /** Text for a not-connected chip's title. */
  disconnectedHint?: string;
}

export function CapabilityChip({
  id,
  capability,
  selected,
  onClick,
  size = "sm",
  className,
  disconnectedHint,
}: CapabilityChipProps) {
  const name = capabilityName(id);
  const brand = resolveToolBrand(capability?.tool_name || name);
  const label = capability?.label ?? brand.label;
  const connected = capability ? capability.connected : true;
  const kind = capabilityKind(id) ?? capability?.kind ?? null;
  const Tag = onClick ? "button" : "span";
  const tile = size === "sm" ? "h-4 w-4" : "h-6 w-6";
  return (
    <Tag
      type={onClick ? "button" : undefined}
      onClick={onClick}
      aria-pressed={onClick ? selected : undefined}
      title={[capability?.one_liner, connected ? null : disconnectedHint].filter(Boolean).join(" — ") || label}
      className={cn(
        "inline-flex max-w-full items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] leading-5",
        onClick ? "cursor-pointer transition-colors hover:bg-secondary" : "",
        selected ? "border-border-strong bg-secondary text-foreground" : "border-border bg-transparent text-foreground",
        !connected && "opacity-50",
        className,
      )}
      data-kind={kind ?? undefined}
    >
      <span
        aria-hidden
        className={cn("inline-flex shrink-0 items-center justify-center overflow-hidden rounded-[4px] bg-popover", tile)}
      >
        {brand.logoUrl ? (
          <img src={brand.logoUrl} alt="" className="h-full w-full object-contain p-[2px]" draggable={false} />
        ) : (
          <span className="font-mono text-[8px] font-semibold uppercase text-muted-foreground">{brand.monogram}</span>
        )}
      </span>
      <span className="truncate">{label}</span>
      {kind && kind !== "plugin" ? (
        <span className="shrink-0 rounded-sm bg-secondary px-1 font-mono text-[9px] uppercase text-muted-foreground">
          {kind}
        </span>
      ) : null}
    </Tag>
  );
}
