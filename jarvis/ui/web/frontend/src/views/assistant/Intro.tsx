/**
 * The opening of the assistant's profile: the assistant itself, large, and
 * the one sentence of its own vibe from SOUL.md set as the page's lead.
 * Under it a single line of plain facts — whether it is learning right now,
 * how much it remembers, when it last changed. No boxes, no figures in tiles.
 */
import { PetMark } from "@/components/pets/PetMark";
import { useT, useUiLanguage } from "@/i18n";
import { cn } from "@/lib/utils";
import { fileOf, type SoulProfile } from "@/views/assistant/api";
import { newest, plural, relativeDay } from "@/views/assistant/format";

/** "**Vibe:** Helpful but…" from the character rows, without its label. */
export function vibeOf(profile: SoulProfile): string | null {
  const who = fileOf(profile, "soul")?.character.who ?? [];
  const row = who.find((r) => isVibe(r.label));
  return row?.text.trim() || null;
}

export function isVibe(label: string): boolean {
  return /^(vibe|stimmung|vibra)$/i.test(label.trim());
}

export function Intro({ profile }: { profile: SoulProfile }) {
  const t = useT();
  const lang = useUiLanguage();
  const vibe = vibeOf(profile);
  const remembered = fileOf(profile, "memory")?.entries.length ?? 0;
  const changed = relativeDay(newest(profile.files.map((f) => f.updated_ms)), lang);

  const facts = [
    plural(t, "assistant_view.fact_memory", remembered, lang),
    changed ? t("assistant_view.fact_changed").replace("{0}", changed) : null,
  ].filter(Boolean) as string[];

  return (
    <div data-testid="assistant-intro" className="flex items-center gap-8 pt-1">
      <div className="shrink-0">
        <PetMark size={96} reactive label={profile.name} />
      </div>
      <div className="min-w-0 flex-1">
        <p
          data-testid="assistant-vibe"
          className={cn(
            "text-xl font-medium leading-8 tracking-tight",
            vibe ? "text-foreground-strong" : "text-muted-foreground",
          )}
        >
          {vibe ?? t("assistant_view.vibe_missing")}
        </p>
        <p className="mt-3 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-sm text-muted-foreground">
          <span
            data-testid="assistant-learning"
            title={t(profile.learning ? "assistant_view.learning_on_hint" : "assistant_view.learning_off_hint")}
            className="inline-flex items-center gap-1.5"
          >
            <span
              aria-hidden
              className={cn("h-1.5 w-1.5 rounded-full", profile.learning ? "bg-success" : "bg-foreground-faint")}
            />
            {t(profile.learning ? "assistant_view.learning_on" : "assistant_view.learning_off")}
          </span>
          {facts.map((fact) => (
            <span key={fact} className="inline-flex items-center gap-2.5">
              <span aria-hidden className="text-foreground-faint">·</span>
              {fact}
            </span>
          ))}
        </p>
      </div>
    </div>
  );
}
