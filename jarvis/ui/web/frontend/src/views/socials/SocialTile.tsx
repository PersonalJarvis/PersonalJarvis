import { ChevronRight, ExternalLink } from "lucide-react";

import { cn } from "@/lib/utils";
import { useT } from "@/i18n";
import { BrandIcon } from "./BrandIcon";
import { BRANDS, platformLabel } from "./brands";
import type { SocialEntry } from "./api";

export interface SocialGroup {
  platform: string;
  entries: SocialEntry[];
  order: number;
}

/**
 * A read-only grid tile for one platform group, with the brand colour as a soft
 * corner glow. One link → the whole tile is a direct external anchor (single
 * tap). Several links → the tile is a button that opens the platform's detail
 * page (tap the platform, then tap the specific link there). The section is
 * curated via the seed, so there are no edit/delete affordances here.
 */
export function SocialTile({
  group,
  onOpenDetail,
}: {
  group: SocialGroup;
  onOpenDetail: (platform: string) => void;
}) {
  const t = useT();
  const hex = BRANDS[group.platform.toLowerCase()]?.hex ?? "#3f3f46";
  const name = platformLabel(group.platform);
  const single = group.entries.length === 1 ? group.entries[0] : null;
  const subtitle = single ? safeHost(single.url) : `${group.entries.length} ${t("socials.links")}`;

  const glow = (
    <div
      aria-hidden="true"
      className="pointer-events-none absolute -right-10 -top-10 h-28 w-28 rounded-full opacity-25 blur-2xl transition-opacity duration-300 group-hover:opacity-60"
      style={{ background: hex }}
    />
  );

  const inner = (
    <>
      <BrandIcon platform={group.platform} size={52} />
      <span className="flex min-w-0 flex-1 flex-col">
        <span className="truncate font-display text-base font-semibold text-foreground">
          {name}
        </span>
        <span className="truncate text-xs text-muted-foreground">{subtitle}</span>
      </span>
    </>
  );

  const shell =
    "group relative flex items-center gap-4 overflow-hidden rounded-2xl border border-border bg-card/50 p-5 transition-all hover:border-primary/30 hover:bg-card/70";

  if (single) {
    return (
      <a
        href={single.url}
        target="_blank"
        rel="noopener noreferrer"
        className={cn(shell, "focus:outline-none")}
      >
        {glow}
        {inner}
        <ExternalLink className="h-4 w-4 shrink-0 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
      </a>
    );
  }

  return (
    <button
      type="button"
      onClick={() => onOpenDetail(group.platform)}
      aria-label={`${name}, ${group.entries.length} ${t("socials.links")}`}
      className={cn(shell, "text-left")}
    >
      {glow}
      {inner}
      <ChevronRight className="h-5 w-5 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5 group-hover:text-foreground" />
    </button>
  );
}

function safeHost(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}
