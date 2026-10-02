/**
 * Who the assistant is, read straight out of SOUL.md: its role and vibe as
 * label/value rows, then its tone rules and limits as short lists. The name
 * is not repeated here — it follows the wake word and already heads the page.
 */
import { Pencil } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";
import { ProfileGroup } from "@/views/profile/ProfileGroup";
import type { CharacterFile, SoulRow } from "@/views/soul/api";

/** Inline Markdown emphasis is file syntax, not something to show. */
export function plain(text: string): string {
  return text.replace(/\*\*(.+?)\*\*/g, "$1").replace(/(^|\s)_(.+?)_(?=\s|$)/g, "$1$2").trim();
}

function RuleList({ title, rows }: { title: string; rows: SoulRow[] }) {
  return (
    <div className="px-5 py-4">
      <h3 className="text-base font-medium text-foreground">{title}</h3>
      <ul className="mt-2.5 flex flex-col gap-2">
        {rows.map((row, i) => (
          <li key={i} className="flex gap-3 text-base leading-6 text-muted-foreground">
            <span aria-hidden className="mt-2.5 h-1 w-1 shrink-0 rounded-full bg-foreground-faint" />
            <span className="min-w-0">
              {row.label && <span className="font-medium text-foreground">{plain(row.label)}: </span>}
              {plain(row.text)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function CharacterGroup({ soul, onEdit }: { soul: CharacterFile | undefined; onEdit: () => void }) {
  const t = useT();
  const who = soul?.character.who ?? [];
  const tone = soul?.character.tone ?? [];
  const limits = soul?.character.limits ?? [];
  const empty = who.length + tone.length + limits.length === 0;

  return (
    <ProfileGroup
      testId="soul-character"
      title={t("soul_view.character_title")}
      description={t("soul_view.character_description")}
      aside={
        soul?.exists ? (
          <Button type="button" size="sm" variant="outline" onClick={onEdit}>
            <Pencil aria-hidden />
            {t("soul_view.character_edit")}
          </Button>
        ) : undefined
      }
    >
      {empty ? (
        <p className="px-5 py-4 text-base text-muted-foreground">
          {t(soul?.exists ? "soul_view.character_empty" : "soul_view.character_missing")}
        </p>
      ) : (
        <>
          {who.length > 0 && (
            <dl className="px-5 py-4">
              {who.map((row, i) => (
                <div key={i} className="flex flex-col gap-0.5 py-1.5 sm:flex-row sm:gap-6">
                  <dt className="shrink-0 text-base font-medium text-foreground sm:w-28">
                    {plain(row.label) || "·"}
                  </dt>
                  <dd className="min-w-0 text-base leading-6 text-muted-foreground">{plain(row.text)}</dd>
                </div>
              ))}
            </dl>
          )}
          {tone.length > 0 && <RuleList title={t("soul_view.tone_title")} rows={tone} />}
          {limits.length > 0 && <RuleList title={t("soul_view.limits_title")} rows={limits} />}
        </>
      )}
    </ProfileGroup>
  );
}
