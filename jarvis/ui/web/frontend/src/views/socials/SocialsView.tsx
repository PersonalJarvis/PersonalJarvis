import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowLeft, Loader2, Share2 } from "lucide-react";

import { ViewHeader } from "@/views/ChatsView";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { BrandIcon } from "./BrandIcon";
import { SocialTile, type SocialGroup } from "./SocialTile";
import { SocialLinkRow } from "./SocialLinkRow";
import { glyphIsWordmark, platformLabel } from "./brands";
import { listSocials, type SocialEntry } from "./api";

/**
 * The Socials section — a read-only hub of the project's social links, grouped
 * by platform. One link → a direct external-open tile; several links of the
 * same platform → a tile that opens an in-section detail page to pick the link.
 *
 * Read-only by design: this ships inside an open-source distribution, so a
 * downloader views and clicks the project's links but never manages them. The
 * links are curated in the seed (jarvis/ui/web/socials_routes.py) — a fresh
 * install seeds them automatically. Only enabled entries are shown.
 */
export function SocialsView() {
  const t = useT();
  const [entries, setEntries] = useState<SocialEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [detailPlatform, setDetailPlatform] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setEntries(await listSocials());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const groups = useMemo(() => groupByPlatform(entries), [entries]);
  const detailGroup = detailPlatform
    ? groups.find((g) => g.platform === detailPlatform) ?? null
    : null;

  // A detail page whose platform is gone (e.g. seed change) falls back to the grid.
  useEffect(() => {
    if (detailPlatform && !groups.some((g) => g.platform === detailPlatform)) {
      setDetailPlatform(null);
    }
  }, [groups, detailPlatform]);

  return (
    <div className="flex h-full flex-col overflow-y-auto scrollbar-jarvis">
      <ViewHeader
        icon={<Share2 className="h-4 w-4 text-primary" />}
        title={t("nav.socials")}
        subtitle={t("socials.subtitle")}
      />

      <div className="mx-auto w-full max-w-4xl p-6">
        {loading ? (
          <div className="flex items-center justify-center py-20 text-muted-foreground">
            <Loader2
              className="h-5 w-5 animate-spin motion-reduce:animate-none"
              aria-hidden="true"
            />
          </div>
        ) : error ? (
          <p className="py-20 text-center text-sm text-destructive">{error}</p>
        ) : detailGroup ? (
          <PlatformDetail group={detailGroup} onBack={() => setDetailPlatform(null)} />
        ) : groups.length === 0 ? (
          <div className="flex flex-col items-center gap-4 py-20 text-center">
            <Share2 className="h-8 w-8 text-muted-foreground/40" />
            <p className="text-sm text-muted-foreground">{t("socials.empty")}</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {groups.map((group) => (
              <SocialTile
                key={group.platform}
                group={group}
                onOpenDetail={(p) => setDetailPlatform(p)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * Group enabled entries by platform. Disabled entries are hidden (there is no
 * UI to toggle them — the section is read-only — so a disabled link should not
 * surface). Groups and their links keep stored `order`.
 */
function groupByPlatform(entries: SocialEntry[]): SocialGroup[] {
  const map = new Map<string, SocialEntry[]>();
  for (const e of entries) {
    if (e.enabled === false) continue;
    const key = e.platform.toLowerCase();
    const arr = map.get(key);
    if (arr) arr.push(e);
    else map.set(key, [e]);
  }
  const groups: SocialGroup[] = [];
  for (const [platform, es] of map) {
    const sorted = es.slice().sort((a, b) => a.order - b.order);
    groups.push({
      platform,
      entries: sorted,
      order: Math.min(...es.map((e) => e.order)),
    });
  }
  groups.sort((a, b) => a.order - b.order);
  return groups;
}

/** The detail page for a platform with several links — pick one to open. */
function PlatformDetail({ group, onBack }: { group: SocialGroup; onBack: () => void }) {
  const t = useT();
  return (
    <div className="space-y-5">
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={onBack}
          aria-label={t("socials.back")}
          className="rounded-lg border border-border p-2 text-muted-foreground transition-colors hover:border-primary/40 hover:text-foreground"
        >
          <ArrowLeft className="h-4 w-4" />
        </button>
        <BrandIcon platform={group.platform} size={40} />
        {/* When the brand glyph already is the wordmark (X), keep the heading
            for screen readers and document structure but hide it visually — the
            icon beside an "X" text label otherwise reads as two X logos. */}
        <h3
          className={cn(
            "font-display text-lg font-semibold tracking-tight",
            glyphIsWordmark(group.platform) && "sr-only",
          )}
        >
          {platformLabel(group.platform)}
        </h3>
      </div>
      <div className="space-y-2.5">
        {group.entries.map((entry) => (
          <SocialLinkRow key={entry.id} entry={entry} />
        ))}
      </div>
    </div>
  );
}
