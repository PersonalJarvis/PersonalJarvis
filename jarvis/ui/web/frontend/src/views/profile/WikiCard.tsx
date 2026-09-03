/**
 * The long-form page the wiki keeps about you.
 *
 * The profile front matter holds eighteen short fields; the vault holds a
 * written entity page — summary, facts, relationships, and dated sources for
 * each one — built by the wiki consolidator from actual conversations. It is
 * by a wide margin the richest thing the system knows about the reader, and
 * the profile section has never linked to it.
 *
 * Best-effort by design: the slug is derived from the user's name, so a
 * profile with no name asks for nothing and a vault with no such page shows
 * the invitation instead of an error. A 404 here is a state, not a fault.
 */
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, NotebookPen } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useT } from "@/i18n";
import { useEventStore } from "@/store/events";

interface WikiPage {
  ok: boolean;
  slug: string;
  title: string;
  body_md: string;
  wikilinks: string[];
  stats?: { words?: number };
}

/**
 * The vault's own slug rules: lowercase, diacritics folded, everything that is
 * not a letter or digit becomes a dash. "Renee Dubois" -> "renee-dubois".
 */
export function wikiSlug(name: string | null | undefined): string | null {
  if (!name) return null;
  const slug = name
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")  // combining marks, after NFD
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return slug || null;
}

/** Bullet lines under a `## Facts` heading — what the page actually asserts. */
function countFacts(bodyMd: string): number {
  const at = bodyMd.search(/^##\s+Facts\s*$/im);
  if (at < 0) return 0;
  const after = bodyMd.slice(at);
  const next = after.search(/\n##\s+/);
  const section = next > 0 ? after.slice(0, next) : after;
  return section.split("\n").filter((l) => /^-\s+\S/.test(l.trim())).length;
}

export function WikiCard({ name }: { name: string | null }) {
  const t = useT();
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const slug = wikiSlug(name);

  const page = useQuery<WikiPage, Error>({
    queryKey: ["profile", "wiki-page", slug],
    enabled: !!slug,
    retry: false,
    staleTime: 60_000,
    queryFn: async () => {
      const res = await fetch(`/api/wiki/page/${encodeURIComponent(slug as string)}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json();
    },
  });

  const found = page.data?.ok ? page.data : null;
  const facts = found ? countFacts(found.body_md ?? "") : 0;

  return (
    <Card className="profile-rise">
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2">
          <NotebookPen aria-hidden className="h-4 w-4 text-muted-foreground" />
          {t("profile_view.wiki_title")}
        </CardTitle>
      </CardHeader>

      <CardContent>
        {page.isLoading ? (
          <div role="status" aria-busy="true" className="flex flex-col gap-2">
            <div className="h-3 w-2/3 animate-pulse rounded-full bg-sheen/[0.06]" />
            <div className="h-3 w-1/2 animate-pulse rounded-full bg-sheen/[0.06]" />
          </div>
        ) : found ? (
          <>
            <p className="font-mono text-meta text-foreground" data-testid="wiki-slug">
              {found.slug}.md
            </p>
            <ul className="mt-2 flex flex-col gap-1 text-meta text-muted-foreground">
              {facts > 0 && (
                <li className="tabular-nums">
                  {t("profile_view.wiki_facts").replace("{0}", String(facts))}
                </li>
              )}
              {found.wikilinks.length > 0 && (
                <li className="tabular-nums">
                  {t("profile_view.wiki_links").replace("{0}", String(found.wikilinks.length))}
                </li>
              )}
              {!!found.stats?.words && (
                <li className="tabular-nums">
                  {t("profile_view.wiki_words").replace("{0}", String(found.stats.words))}
                </li>
              )}
            </ul>
            <Button
              type="button"
              variant="link"
              className="mt-3 px-0"
              onClick={() => setActiveSection("memory")}
            >
              {t("profile_view.wiki_open")}
              <ArrowRight />
            </Button>
          </>
        ) : (
          <p data-testid="wiki-empty" className="text-body text-muted-foreground">
            {slug ? t("profile_view.wiki_empty_body") : t("profile_view.wiki_needs_name")}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
