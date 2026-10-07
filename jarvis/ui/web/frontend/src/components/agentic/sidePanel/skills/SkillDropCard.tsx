import { CornerDownLeft, OctagonAlert } from "lucide-react";
import { fill, useT } from "@/i18n";
import type { SkillInFlight } from "@/store/ideSkills";
import { SkillSeal, skillStyle } from "./skillVisuals";

/**
 * What a terminal pane shows while a skill hovers over it.
 *
 * The pane dims, a glow of the skill's own hue rises in its middle and a
 * dashed landing frame says "here": the user sees WHICH skill lands in WHICH
 * pane before letting go. The drag itself is still sealed at this point, so
 * the skill's name comes from the store the card filled on drag start; a drag
 * from another window has none and gets the plain wording.
 *
 * `blocked`: the pane is a plain shell without bracketed paste and the skill
 * spans lines — it would run line by line, so the card says it will not land.
 */
export function SkillDropCard({ skill, target, blocked = false }: {
  skill: SkillInFlight | null;
  target: string;
  blocked?: boolean;
}) {
  const t = useT();
  const hue = skill?.hue ?? "blue";
  return (
    <div
      data-testid="pane-skill-drop"
      style={skillStyle(hue)}
      className="pointer-events-none absolute inset-0 z-40 flex items-center justify-center overflow-hidden bg-background/80 backdrop-blur-[3px] animate-in fade-in duration-150 motion-reduce:animate-none"
    >
      <div aria-hidden className="absolute left-1/2 top-1/2 h-[140%] w-[140%] -translate-x-1/2 -translate-y-1/2 bg-[radial-gradient(closest-side,hsl(var(--skill)/0.22),transparent)]" />
      <div aria-hidden className="absolute inset-3 rounded-xl border-2 border-dashed border-[hsl(var(--skill)/0.55)]" />
      <div className="relative mx-6 flex max-w-sm items-center gap-3.5 rounded-2xl border border-[hsl(var(--skill)/0.35)] bg-popover/95 px-4 py-3.5 shadow-float animate-in zoom-in-95 duration-200 motion-reduce:animate-none">
        <SkillSeal hue={hue} icon={skill?.icon ?? "auto"} title={skill?.title ?? ""} size="lg" />
        <div className="min-w-0">
          <p className="text-xs font-medium uppercase tracking-wider text-[hsl(var(--skill))]">
            {t("ide_side_panel.skills.drop_eyebrow")}
          </p>
          <p className="truncate font-display text-base text-foreground-strong">
            {skill?.title || t("ide_side_panel.skills.drop_unknown")}
          </p>
          {blocked ? (
            <p className="mt-0.5 flex items-start gap-1 text-xs text-warning">
              <OctagonAlert className="mt-0.5 h-3 w-3 shrink-0" aria-hidden />
              {fill(t("ide_side_panel.skills.drop_blocked"), { pane: target })}
            </p>
          ) : (
            <p className="mt-0.5 flex items-center gap-1 truncate text-xs text-muted-foreground">
              <CornerDownLeft className="h-3 w-3 shrink-0" aria-hidden />
              {fill(t("ide_side_panel.skills.drop_into"), { pane: target })}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
