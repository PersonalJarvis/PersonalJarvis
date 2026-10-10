/**
 * Wiki page header: where the page lives, its title, the one line of facts
 * that matter (kind, last change, length), the frontmatter worth showing, and
 * the hand-off to Obsidian.
 */
import { lazy, Suspense } from "react";

import { useT } from "@/i18n";
import { useRunLocale } from "@/components/runs/format";
import type { WikiKind } from "@/lib/wikiApi";
import { GROUP_LABEL_KEY, groupOfKind, relativeAge } from "@/lib/wikiModel";
import { cn } from "@/lib/utils";
import { KindGlyph } from "@/components/wiki/KindGlyph";

// Lazy so the page body paints before the Obsidian hand-off code arrives.
const ObsidianButton = lazy(() =>
  import("./ObsidianButton").then((mod) => ({
    default: mod.ObsidianButton,
  })),
);

interface PageHeaderProps {
  slug: string;
  kind: WikiKind | string;
  title: string;
  frontmatter: Record<string, string | string[]>;
  vaultRoot: string;
  vaultRelPath: string;
  /** Seconds since the epoch of the file's last change, when known. */
  mtime?: number;
  words?: number;
}

/** Frontmatter field -> i18n key of its pill label. */
const FRIENDLY_LABELS: Record<string, string> = {
  type: "page_header.field_type",
  entity_kind: "page_header.field_kind",
  status: "page_header.field_status",
  created: "page_header.field_created",
  updated: "page_header.field_updated",
  started: "page_header.field_started",
  last_activity: "page_header.field_last_activity",
};

const MAX_PILLS = 6;

export function PageHeader({
  slug,
  kind,
  title,
  frontmatter,
  vaultRoot,
  vaultRelPath,
  mtime,
  words,
}: PageHeaderProps) {
  const t = useT();
  const language = useRunLocale();
  const pills = buildPills(frontmatter);
  const breadcrumb = breadcrumbFromPath(vaultRelPath);
  const group = groupOfKind(kind);
  const changed = mtime ? relativeAge(mtime, language) : "";

  return (
    <header className="flex flex-col" data-testid="wiki-page-header" data-slug={slug}>
      <div className="flex items-center justify-between gap-4">
        <nav
          className="flex min-w-0 items-center gap-1.5 text-sm text-foreground-faint"
          data-testid="wiki-page-crumb"
          aria-label={t("wiki_ui.crumb_label")}
        >
          {breadcrumb.map((part, idx) => (
            <span key={idx} className="flex min-w-0 items-center gap-1.5">
              {idx > 0 && <span aria-hidden>/</span>}
              <span className={cn("truncate", idx === breadcrumb.length - 1 && "text-muted-foreground")}>
                {part}
              </span>
            </span>
          ))}
        </nav>
        <Suspense fallback={<ObsidianButtonPlaceholder />}>
          <ObsidianButton vaultRoot={vaultRoot} vaultRelPath={vaultRelPath} size="sm" />
        </Suspense>
      </div>

      <h1
        className="mt-5 text-display font-semibold text-foreground-strong"
        data-testid="wiki-page-title"
      >
        {title}
      </h1>

      <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground">
        <span className="inline-flex items-center gap-1.5 text-foreground-secondary">
          <KindGlyph group={group} className="h-2.5 w-2.5" />
          {t(GROUP_LABEL_KEY[group])}
        </span>
        {changed && (
          <>
            <span aria-hidden className="text-foreground-faint">·</span>
            <span>{t("wiki_ui.page_changed").replace("{0}", changed)}</span>
          </>
        )}
        {words !== undefined && words > 0 && (
          <>
            <span aria-hidden className="text-foreground-faint">·</span>
            <span>
              {t("wiki_ui.page_words").replace("{0}", new Intl.NumberFormat(language).format(words))}
            </span>
          </>
        )}
      </div>

      {/* The page kind used to be painted in one of four hardcoded hues. Hue
          belongs to status and selection only, so the frontmatter facts are
          neutral tags and the kind is told by its shape above. */}
      {pills.length > 0 && (
        <div className="mt-4 flex flex-wrap gap-1.5 text-xs" data-testid="wiki-page-pills">
          {pills.map((p) => (
            <span
              key={p.key}
              className="inline-flex h-6 items-center gap-1 rounded-sm border border-border px-2 text-muted-foreground"
              data-pill-key={p.key}
            >
              <span>{t(p.label)}</span>
              <span className="text-foreground">{p.value}</span>
            </span>
          ))}
        </div>
      )}
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
  // "entities/harald.md" → ["entities", "harald.md"]
  const parts = relPath.split(/[\\/]/).filter(Boolean);
  return parts.length > 0 ? parts : [relPath];
}

function ObsidianButtonPlaceholder() {
  return <span className="h-8 w-36 shrink-0" data-testid="obsidian-button-placeholder" aria-hidden />;
}
