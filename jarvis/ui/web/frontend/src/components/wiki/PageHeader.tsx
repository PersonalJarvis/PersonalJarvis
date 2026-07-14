/**
 * Wiki page header: breadcrumb + title + frontmatter pills + Obsidian button.
 *
 * The Obsidian button itself is owned by Agent D; this header attempts to
 * import that component lazily and falls back to a disabled placeholder
 * when it does not yet exist (parallel-build contract).
 */
import { lazy, Suspense } from "react";

import { useT } from "@/i18n";
import type { WikiKind } from "@/lib/wikiApi";
import { cn } from "@/lib/utils";

// Lazy import — Agent D owns the real "Open in Obsidian" button. A
// placeholder file ships in this branch; Agent D's real implementation
// replaces it during Wave 2.
const ObsidianButton = lazy(() =>
  import("./ObsidianButton").then((mod) => ({
    default: mod.ObsidianButton,
  })),
);

interface PageHeaderProps {
  slug: string;
  kind: WikiKind;
  title: string;
  frontmatter: Record<string, string | string[]>;
  vaultRoot: string;
  vaultRelPath: string;
}

const FRIENDLY_LABELS: Record<string, string> = {
  type: "type",
  entity_kind: "kind",
  status: "status",
  created: "created",
  updated: "updated",
  started: "started",
  last_activity: "last activity",
};

const TYPE_COLOR: Record<WikiKind, string> = {
  entity: "text-[#6aa9ff]",
  concept: "text-[#b48cf2]",
  project: "text-[#ffb84d]",
  session: "text-[#5bd4a4]",
};

const MAX_PILLS = 6;

export function PageHeader({
  slug,
  kind,
  title,
  frontmatter,
  vaultRoot,
  vaultRelPath,
}: PageHeaderProps) {
  const pills = buildPills(frontmatter);
  const breadcrumb = breadcrumbFromPath(vaultRelPath);
  const typeColor = TYPE_COLOR[kind] ?? "text-foreground";

  return (
    <header
      className="flex flex-col gap-3 border-b border-border px-7 py-5 md:flex-row md:items-start md:justify-between"
      data-testid="wiki-page-header"
      data-slug={slug}
    >
      <div className="min-w-0 flex-1">
        <div className="mb-1.5 text-xs text-muted-foreground" data-testid="wiki-page-crumb">
          {breadcrumb.map((part, idx) => (
            <span key={idx}>
              {idx > 0 && <span className="mx-1.5 text-border">/</span>}
              {part}
            </span>
          ))}
        </div>
        <h1 className="text-[22px] font-semibold tracking-tight" data-testid="wiki-page-title">
          {title}
        </h1>
        {pills.length > 0 && (
          <div className="mt-2.5 flex flex-wrap gap-2 text-xs" data-testid="wiki-page-pills">
            {pills.map((p) => (
              <span
                key={p.key}
                className="rounded-md border border-border bg-secondary/50 px-2.5 py-0.5 text-muted-foreground"
                data-pill-key={p.key}
              >
                <span className="mr-1.5 text-foreground">{p.label}:</span>
                <span className={cn(p.key === "type" && typeColor)}>{p.value}</span>
              </span>
            ))}
          </div>
        )}
      </div>

      <Suspense
        fallback={
          <ObsidianButtonPlaceholder vaultRelPath={vaultRelPath} />
        }
      >
        <ObsidianButton vaultRoot={vaultRoot} vaultRelPath={vaultRelPath} />
      </Suspense>
    </header>
  );
}

interface Pill {
  key: string;
  label: string;
  value: string;
}

function buildPills(fm: Record<string, string | string[]>): Pill[] {
  const out: Pill[] = [];
  for (const [key, raw] of Object.entries(fm)) {
    if (key === "slug" || key === "aliases") continue;
    if (!(key in FRIENDLY_LABELS)) continue;
    const value = Array.isArray(raw) ? raw.join(", ") : raw;
    if (!value || value.trim() === "") continue;
    out.push({ key, label: FRIENDLY_LABELS[key], value });
    if (out.length >= MAX_PILLS) break;
  }
  return out;
}

function breadcrumbFromPath(relPath: string): string[] {
  // "entities/sam.md" → ["entities", "sam.md"]
  const parts = relPath.split(/[\\/]/).filter(Boolean);
  return parts.length > 0 ? parts : [relPath];
}

function ObsidianButtonPlaceholder({ vaultRelPath: _vaultRelPath }: { vaultRelPath: string }) {
  const t = useT();
  return (
    <button
      type="button"
      disabled
      className={cn(
        "inline-flex shrink-0 items-center gap-2 rounded-md border border-border bg-secondary/40",
        "px-3.5 py-2 text-sm text-muted-foreground opacity-60",
      )}
      data-testid="obsidian-button-placeholder"
      title={t("page_header.placeholder_title")}
    >
      <span className="grid h-4 w-4 place-items-center rounded-sm bg-[#b48cf2] text-[10px] font-bold text-background">
        O
      </span>
      {t("page_header.open_in_obsidian")}
    </button>
  );
}
