/**
 * The top of the SOUL.md page: the assistant itself.
 *
 * Its mark (the pet the person chose, alive and following the voice), its
 * name, what it is, and the one line of its own vibe from SOUL.md. Below,
 * four figures from the real files. Nothing here is invented: a missing
 * vibe is left out, and a figure without a source shows a dash.
 */
import { PetMark } from "@/components/pets/PetMark";
import { Badge } from "@/components/ui/badge";
import { useT, useUiLanguage } from "@/i18n";
import { fileOf, type SoulProfile } from "@/views/soul/api";
import { newest, relativeDay } from "@/views/soul/format";

function Stat({ label, value, sub }: { label: string; value: string; sub?: string | null }) {
  return (
    <div className="min-w-0 px-5 py-4">
      <p className="truncate text-sm text-muted-foreground">{label}</p>
      <p className="mt-0.5 truncate text-lg font-semibold tabular-nums text-foreground-strong">{value}</p>
      {sub && <p className="truncate text-sm text-muted-foreground">{sub}</p>}
    </div>
  );
}

/** "**Vibe:** Helpful but…" from the character rows, without its label. */
function vibeOf(profile: SoulProfile): string | null {
  const who = fileOf(profile, "soul")?.character.who ?? [];
  const row = who.find((r) => /^(vibe|stimmung|vibra)$/i.test(r.label));
  return row?.text.trim() || null;
}

export function SoulHero({ profile }: { profile: SoulProfile }) {
  const t = useT();
  const lang = useUiLanguage();

  const soul = fileOf(profile, "soul");
  const memory = fileOf(profile, "memory");
  const vibe = vibeOf(profile);
  const lastChange = relativeDay(newest(profile.files.map((f) => f.updated_ms)), lang);
  const learned = soul?.learned.length ?? 0;
  const remembered = memory?.entries.length ?? 0;

  return (
    <section
      data-testid="soul-hero"
      aria-label={profile.name}
      className="overflow-hidden rounded-xl border border-border bg-card"
    >
      <div className="flex items-center gap-5 p-6">
        <div className="flex h-20 w-20 shrink-0 items-center justify-center rounded-2xl border border-border bg-secondary">
          <PetMark size={56} reactive label={profile.name} />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 items-center gap-2.5">
            <h2 className="truncate text-2xl font-semibold text-foreground-strong">{profile.name}</h2>
            <Badge
              variant={profile.learning ? "success" : "secondary"}
              title={t(profile.learning ? "soul_view.learning_on_hint" : "soul_view.learning_off_hint")}
              data-testid="soul-learning"
            >
              {t(profile.learning ? "soul_view.learning_on" : "soul_view.learning_off")}
            </Badge>
          </div>
          <p className="mt-0.5 truncate text-base text-muted-foreground">
            {t(profile.named ? "soul_view.hero_role" : "soul_view.hero_role_unnamed").replace(
              "{0}",
              profile.product,
            )}
          </p>
          {vibe && (
            <p data-testid="soul-vibe" className="mt-2 text-base leading-6 text-foreground">
              {vibe}
            </p>
          )}
        </div>
      </div>

      <div className="grid grid-cols-2 border-t border-border sm:grid-cols-4 [&>*]:border-border [&>*:nth-child(even)]:border-l sm:[&>*:not(:first-child)]:border-l [&>*:nth-child(n+3)]:border-t sm:[&>*:nth-child(n+3)]:border-t-0">
        <Stat
          label={t("soul_view.stat_wake")}
          value={profile.wake_phrase || "–"}
          sub={profile.wake_phrase ? t("soul_view.stat_wake_sub") : null}
        />
        <Stat label={t("soul_view.stat_self")} value={String(learned)} sub={t("soul_view.stat_notes")} />
        <Stat label={t("soul_view.stat_memory")} value={String(remembered)} sub={t("soul_view.stat_notes")} />
        <Stat label={t("soul_view.stat_updated")} value={lastChange ?? "–"} />
      </div>
    </section>
  );
}
