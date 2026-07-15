import { ExternalLink } from "lucide-react";

import { openExternalUrl } from "@/lib/openExternal";
import type { SocialEntry } from "./api";

/**
 * One read-only link row inside a platform's detail page. The label + host open
 * the URL externally. No edit/delete — the section is curated via the seed.
 */
export function SocialLinkRow({ entry }: { entry: SocialEntry }) {
  const host = safeHost(entry.url);

  return (
    <a
      href={entry.url}
      onClick={(event) => {
        event.preventDefault();
        void openExternalUrl(entry.url);
      }}
      rel="noopener noreferrer"
      className="group flex items-center gap-4 rounded-xl border border-border bg-card/40 p-4 transition-all hover:border-primary/40 hover:bg-card/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <span className="flex min-w-0 flex-1 flex-col">
        <span className="flex items-center gap-1.5 truncate font-medium text-foreground">
          {entry.label}
          <ExternalLink className="h-3 w-3 shrink-0 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
        </span>
        <span className="truncate text-xs text-muted-foreground">{host}</span>
      </span>
    </a>
  );
}

function safeHost(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}
