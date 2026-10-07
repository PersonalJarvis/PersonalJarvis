import { ClipboardPaste, FileUp, ListPlus, Plus } from "lucide-react";
import { useT } from "@/i18n";
import { SkillSeal, skillStyle } from "./skillVisuals";

/*
 * The motion for the empty library's picture, scoped by class names nobody
 * else uses. Kept beside the picture rather than in the global stylesheet:
 * nothing outside this one illustration moves this way.
 */
const HERO_CSS = `
@keyframes jarvis-skill-float { 0%,100% { transform: translateY(0) rotate(var(--tilt)); } 50% { transform: translateY(-5px) rotate(var(--tilt)); } }
@keyframes jarvis-skill-dash { to { stroke-dashoffset: -24; } }
@keyframes jarvis-skill-caret { 0%,49% { opacity: 1; } 50%,100% { opacity: 0; } }
@keyframes jarvis-skill-fly { 0% { transform: translate(0,0) rotate(-6deg); opacity: 0; } 12% { opacity: 1; } 70% { transform: translate(108px,60px) rotate(0deg); opacity: 1; } 85%,100% { transform: translate(112px,66px) scale(.6); opacity: 0; } }
.jarvis-skill-float { animation: jarvis-skill-float 5.5s ease-in-out infinite; }
.jarvis-skill-dash { animation: jarvis-skill-dash 1.4s linear infinite; }
.jarvis-skill-caret { animation: jarvis-skill-caret 1.1s steps(1) infinite; }
.jarvis-skill-fly { animation: jarvis-skill-fly 4.2s cubic-bezier(.45,.05,.3,1) infinite; animation-delay: .8s; opacity: 0; }
@media (prefers-reduced-motion: reduce) {
  .jarvis-skill-float, .jarvis-skill-dash, .jarvis-skill-caret { animation: none; }
  .jarvis-skill-fly { display: none; }
}
`;

const FAN = [
  { hue: "violet", icon: "plan", tilt: "-6deg", x: 0, y: 2, delay: "0s", title: "Plan" },
  { hue: "amber", icon: "review", tilt: "-1deg", x: 14, y: 38, delay: "-1.8s", title: "Review" },
  { hue: "rose", icon: "bug", tilt: "5deg", x: 28, y: 74, delay: "-3.6s", title: "Fix" },
] as const;

function MiniCard({ hue, icon, title }: { hue: string; icon: string; title: string }) {
  return (
    <div style={skillStyle(hue)} className="flex w-[118px] items-center gap-2 rounded-lg border border-[hsl(var(--skill)/0.4)] bg-card p-1.5">
      <SkillSeal hue={hue} icon={icon} title={title} size="sm" />
      <div className="min-w-0 flex-1 space-y-1">
        <div className="h-1.5 w-4/5 rounded-full bg-foreground/25" />
        <div className="h-1.5 w-3/5 rounded-full bg-[hsl(var(--skill)/0.35)]" />
      </div>
    </div>
  );
}

/** The picture: three skills fanned out, one flying into a terminal prompt. */
function HeroPicture() {
  return (
    <div aria-hidden className="relative mx-auto h-[150px] w-[268px]">
      <style>{HERO_CSS}</style>
      {FAN.map((card) => (
        <div
          key={card.title}
          className="jarvis-skill-float absolute"
          style={{ left: card.x, top: card.y, ["--tilt" as string]: card.tilt, transform: `rotate(${card.tilt})`, animationDelay: card.delay }}
        >
          <MiniCard hue={card.hue} icon={card.icon} title={card.title} />
        </div>
      ))}
      <svg className="absolute left-[128px] top-[12px] h-[60px] w-[80px] text-muted-foreground/60" viewBox="0 0 80 60" fill="none">
        <path className="jarvis-skill-dash" d="M2 8 C 40 2, 66 18, 70 52" stroke="currentColor" strokeWidth="1.5" strokeDasharray="4 4" strokeLinecap="round" />
        <path d="M64 47 L70 56 L75 46" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      <div className="jarvis-skill-fly absolute left-[80px] top-[12px]">
        <div style={skillStyle("amber")} className="h-5 w-12 rounded-md border border-[hsl(var(--skill)/0.5)] bg-[hsl(var(--skill)/0.2)]" />
      </div>
      <div className="absolute bottom-0 right-0 w-[120px] overflow-hidden rounded-lg border border-border-strong bg-background shadow-[0_14px_30px_-18px_hsl(var(--foreground)/0.5)]">
        <div className="flex items-center gap-1 border-b border-border px-2 py-1.5">
          <span className="h-1.5 w-1.5 rounded-full bg-border-strong" />
          <span className="h-1.5 w-1.5 rounded-full bg-border-strong" />
          <span className="h-1.5 w-1.5 rounded-full bg-border-strong" />
        </div>
        <div className="space-y-1.5 px-2 py-2 font-mono text-xs leading-none">
          <div className="h-1.5 w-3/4 rounded-full bg-muted-foreground/25" />
          <div className="h-1.5 w-1/2 rounded-full bg-muted-foreground/20" />
          <div className="flex items-center gap-1 pt-0.5 text-accent">
            <span>&gt;</span>
            <span className="jarvis-skill-caret inline-block h-3 w-1.5 bg-accent" />
          </div>
        </div>
      </div>
    </div>
  );
}

/** The empty library: what a skill is, shown rather than told, and three ways in. */
export function SkillsHero({ onNew, onPaste, onImport, onExamples, addingExamples }: {
  onNew: () => void;
  onPaste: () => void;
  onImport: () => void;
  onExamples: () => void;
  addingExamples: boolean;
}) {
  const t = useT();
  return (
    <div data-testid="skills-empty" className="flex flex-col items-center px-5 pb-8 pt-6 text-center">
      <HeroPicture />
      <h3 className="mt-6 font-display text-lg text-foreground-strong">{t("ide_side_panel.skills.empty_title")}</h3>
      <p className="mt-1.5 max-w-[19rem] text-sm leading-6 text-muted-foreground">{t("ide_side_panel.skills.empty_body")}</p>
      <div className="mt-5 grid w-full max-w-[19rem] gap-2">
        <button
          type="button"
          data-testid="skills-empty-new"
          onClick={onNew}
          className="inline-flex h-9 items-center justify-center gap-2 rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground transition-opacity hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
        >
          <Plus className="h-4 w-4" aria-hidden />
          {t("ide_side_panel.skills.new")}
        </button>
        <div className="grid grid-cols-2 gap-2">
          <button
            type="button"
            onClick={onPaste}
            className="inline-flex h-9 items-center justify-center gap-1.5 rounded-md border border-border-strong px-2 text-sm text-foreground transition-colors hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <ClipboardPaste className="h-4 w-4 text-muted-foreground" aria-hidden />
            {t("ide_side_panel.skills.paste_short")}
          </button>
          <button
            type="button"
            onClick={onImport}
            className="inline-flex h-9 items-center justify-center gap-1.5 rounded-md border border-border-strong px-2 text-sm text-foreground transition-colors hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <FileUp className="h-4 w-4 text-muted-foreground" aria-hidden />
            {t("ide_side_panel.skills.import_short")}
          </button>
        </div>
        <button
          type="button"
          data-testid="skills-empty-examples"
          disabled={addingExamples}
          onClick={onExamples}
          className="mt-1 inline-flex h-8 items-center justify-center gap-1.5 rounded-md text-sm text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
        >
          <ListPlus className="h-3.5 w-3.5" aria-hidden />
          {t("ide_side_panel.skills.add_examples")}
        </button>
      </div>
      <p className="mt-4 text-xs text-foreground-faint">{t("ide_side_panel.skills.drop_files_hint")}</p>
    </div>
  );
}
