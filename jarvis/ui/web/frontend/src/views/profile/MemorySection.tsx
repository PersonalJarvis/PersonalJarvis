/**
 * Memory and privacy — where the rest of what the assistant knows lives, and
 * what it will never keep.
 *
 * Four rows, each pointing at a real source:
 *   - your standing rules ({name}.md), edited in their own section;
 *   - the wiki's long-form page about you, when the vault has one (the slug
 *     comes from your name, so a nameless profile asks for nothing);
 *   - the profile file itself, USER.md, which opens in place;
 *   - the `## Do Not Record` categories, quoted from that file rather than
 *     restated in code, so the page can never promise more than the file says.
 */
import { useQuery } from "@tanstack/react-query";
import { ChevronRight, FileText, NotebookPen, ScrollText, ShieldCheck } from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { useAgentInstructions } from "@/hooks/useAgentInstructions";
import { useI18nStore, useT } from "@/i18n";
import { localeForUiLanguage } from "@/components/runs/format";
import { useEventStore } from "@/store/events";
import { ProfileGroup, SettingRow } from "@/views/profile/ProfileGroup";
import { parseDoNotRecord, shortenCategory } from "@/views/profile/provenance";

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
    .replace(/[̀-ͯ]/g, "") // combining marks, after NFD
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

function RowLabel({ icon, children }: { icon: ReactNode; children: ReactNode }) {
  return (
    <span className="flex items-center gap-2.5">
      <span aria-hidden className="text-muted-foreground [&>svg]:h-4 [&>svg]:w-4">
        {icon}
      </span>
      {children}
    </span>
  );
}

/** Hints line up under the label text, past the icon. */
const HINT_INDENT = "pl-[1.625rem]";

export function MemorySection({
  name,
  raw,
  fileUpdatedMs,
  onOpenSource,
  bare = false,
}: {
  name: string | null;
  raw: string | null;
  fileUpdatedMs: number | null;
  onOpenSource: () => void;
  /** Rendered inside a panel that already carries the section's title. */
  bare?: boolean;
}) {
  const t = useT();
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const rules = useAgentInstructions();
  const slug = wikiSlug(name);
  const categories = parseDoNotRecord(raw);

  const wiki = useQuery<WikiPage, Error>({
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

  const rulesContent = rules.config?.content?.trim() ?? "";
  const hasRules = !!rules.config?.exists && rulesContent.length > 0;
  const ruleLines = hasRules ? rulesContent.split("\n").filter((l) => l.trim()).length : 0;
  const rulesFile = rules.config?.filename ?? "";

  const page = wiki.data?.ok ? wiki.data : null;
  const facts = page ? countFacts(page.body_md ?? "") : 0;
  const wikiHint = page
    ? [
        `${page.slug}.md`,
        facts > 0 ? t("profile_view.wiki_facts").replace("{0}", String(facts)) : null,
        page.wikilinks.length > 0
          ? t("profile_view.wiki_links").replace("{0}", String(page.wikilinks.length))
          : null,
      ]
        .filter(Boolean)
        .join(" · ")
    : slug
      ? t("profile_view.wiki_empty_body")
      : t("profile_view.wiki_needs_name");

  const fileDate = fileUpdatedMs
    ? new Date(fileUpdatedMs).toLocaleDateString(localeForUiLanguage(useI18nStore.getState().ui), {
        day: "numeric",
        month: "short",
        year: "numeric",
      })
    : null;

  return (
    <ProfileGroup
      testId="memory"
      title={t("profile_view.memory_title")}
      description={t("profile_view.memory_description")}
      bare={bare}
    >
      <SettingRow
        testId="rules-row"
        label={<RowLabel icon={<ScrollText />}>{t("profile_view.rules_title")}</RowLabel>}
        hint={
          <span className={HINT_INDENT}>
            {rules.loading
              ? "…"
              : hasRules
                ? t("profile_view.rules_open").replace("{0}", String(ruleLines)).replace("{1}", rulesFile)
                : t("profile_view.rules_empty_body")}
          </span>
        }
        control={
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => setActiveSection("agent-instructions")}
          >
            {hasRules ? t("profile_view.open") : t("profile_view.rules_write")}
          </Button>
        }
      />

      <SettingRow
        testId="wiki-row"
        label={<RowLabel icon={<NotebookPen />}>{t("profile_view.wiki_title")}</RowLabel>}
        hint={
          <span className={HINT_INDENT} data-testid={page ? "wiki-found" : "wiki-empty"}>
            {wiki.isLoading ? "…" : wikiHint}
          </span>
        }
        control={
          page ? (
            <Button type="button" size="sm" variant="outline" onClick={() => setActiveSection("memory")}>
              {t("profile_view.open")}
            </Button>
          ) : undefined
        }
      />

      <SettingRow
        testId="source-row"
        label={<RowLabel icon={<FileText />}>{t("profile_view.file_title")}</RowLabel>}
        hint={
          <span className={HINT_INDENT}>
            {fileDate
              ? t("profile_view.file_hint_dated").replace("{0}", fileDate)
              : t("profile_view.file_hint")}
          </span>
        }
        control={
          <Button type="button" size="sm" variant="outline" onClick={onOpenSource}>
            {t("profile_view.file_open")}
            <ChevronRight aria-hidden />
          </Button>
        }
      />

      {categories.length > 0 && (
        <SettingRow
          label={<RowLabel icon={<ShieldCheck />}>{t("profile_view.never_title")}</RowLabel>}
          hint={<span className={HINT_INDENT}>{t("profile_view.never_body")}</span>}
        >
          <ul data-testid="never-stored" className={`mt-3 flex flex-wrap gap-1.5 ${HINT_INDENT}`}>
            {categories.map((c) => (
              <li
                key={c}
                title={c}
                className="rounded-md border border-border bg-secondary px-2 py-0.5 text-sm text-foreground"
              >
                {shortenCategory(c)}
              </li>
            ))}
          </ul>
        </SettingRow>
      )}
    </ProfileGroup>
  );
}
