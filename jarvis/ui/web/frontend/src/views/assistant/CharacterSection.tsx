/**
 * Character — the assistant as SOUL.md describes it, set as a spec sheet:
 * a label column and the words beside it. Role and any other line of
 * "Who I am" come first (the vibe already leads the page and the name
 * follows the wake word, so neither repeats here), then tone and limits,
 * one rule per line, no bullets.
 */
import { useT } from "@/i18n";
import type { CharacterFile, SoulRow } from "@/views/assistant/api";
import { isVibe } from "@/views/assistant/Intro";
import { Section, SpecRow, TextAction } from "@/views/assistant/Section";

/** Inline Markdown emphasis is file syntax, not something to show. */
export function plain(text: string): string {
  return text.replace(/\*\*(.+?)\*\*/g, "$1").replace(/(^|\s)_(.+?)_(?=\s|$)/g, "$1$2").trim();
}

function Rules({ rows }: { rows: SoulRow[] }) {
  return (
    <ul className="flex flex-col gap-2.5">
      {rows.map((row, i) => (
        <li key={i}>
          {row.label && <span className="font-medium text-foreground-strong">{plain(row.label)}: </span>}
          <span className="text-foreground">{plain(row.text)}</span>
        </li>
      ))}
    </ul>
  );
}

export function CharacterSection({
  soul,
  onEdit,
}: {
  soul: CharacterFile | undefined;
  onEdit: () => void;
}) {
  const t = useT();
  const who = (soul?.character.who ?? []).filter((r) => !isVibe(r.label));
  const tone = soul?.character.tone ?? [];
  const limits = soul?.character.limits ?? [];
  const empty = who.length + tone.length + limits.length === 0;

  return (
    <Section
      testId="assistant-character"
      title={t("assistant_view.character_title")}
      action={soul?.exists ? <TextAction onClick={onEdit}>{t("assistant_view.edit")}</TextAction> : undefined}
    >
      {empty ? (
        <p className="text-base text-muted-foreground">
          {t(soul?.exists ? "assistant_view.character_empty" : "assistant_view.character_missing")}
        </p>
      ) : (
        <div className="divide-y divide-border">
          {who.map((row, i) => (
            <SpecRow key={`who-${i}`} label={plain(row.label) || t("assistant_view.who_label")}>
              {plain(row.text)}
            </SpecRow>
          ))}
          {tone.length > 0 && (
            <SpecRow label={t("assistant_view.tone_title")}>
              <Rules rows={tone} />
            </SpecRow>
          )}
          {limits.length > 0 && (
            <SpecRow label={t("assistant_view.limits_title")}>
              <Rules rows={limits} />
            </SpecRow>
          )}
        </div>
      )}
    </Section>
  );
}
