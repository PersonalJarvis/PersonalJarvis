// One portrait chooser for new and existing agents. The illustrated mode is local.
import { useEffect, useRef, useState } from "react";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { AgentSwatch } from "./AgentSwatch";
import { agentPortraitUrl, encodeAgentPortrait } from "./agentPortrait";
import type { AgentPalette } from "./data";
import type { FigureRecipe } from "./figures/figureRecipe";
import {
  BACKDROPS, FACE_DETAILS, FACE_SHAPES, HAIR_STYLES,
  newIllustratedPortrait, parseIllustratedPortrait, serializeIllustratedPortrait,
  type IllustratedPortraitRecipe,
} from "./illustratedPortrait";

export function PortraitEditor({ figure, palette, name, portrait, onChange, onBusyChange, className }: {
  figure: FigureRecipe;
  palette: AgentPalette;
  name: string;
  portrait: string | undefined;
  onChange: (portrait: string | undefined) => void;
  onBusyChange?: (busy: boolean) => void;
  className?: string;
}) {
  const t = useT();
  const inputRef = useRef<HTMLInputElement>(null);
  const [illustratedDraft, setIllustratedDraft] = useState(() =>
    parseIllustratedPortrait(portrait) ? portrait! : newIllustratedPortrait());
  const [imageDraft, setImageDraft] = useState<string | null>(() =>
    agentPortraitUrl(portrait) ? portrait! : null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const illustrated = parseIllustratedPortrait(portrait);
  const image = illustrated ? null : agentPortraitUrl(portrait);
  const mode = illustrated ? "illustrated" : image ? "image" : "figure";

  useEffect(() => {
    if (parseIllustratedPortrait(portrait)) setIllustratedDraft(portrait!);
    else if (agentPortraitUrl(portrait)) setImageDraft(portrait!);
  }, [portrait]);

  const edit = (patch: Partial<IllustratedPortraitRecipe>) => {
    const current = parseIllustratedPortrait(portrait) ?? parseIllustratedPortrait(illustratedDraft);
    if (!current) return;
    const next = serializeIllustratedPortrait({ ...current, ...patch });
    setIllustratedDraft(next);
    onChange(next);
  };
  const shuffle = () => {
    const next = newIllustratedPortrait();
    setIllustratedDraft(next);
    onChange(next);
  };
  const upload = async (file: File) => {
    setBusy(true);
    onBusyChange?.(true);
    setError(null);
    try {
      const next = await encodeAgentPortrait(file);
      setImageDraft(next);
      onChange(next);
    } catch (cause) {
      const key = cause instanceof Error && cause.message === "portrait_too_large"
        ? "portrait_too_large" : "portrait_invalid_file";
      setError(t(`society.card.${key}`));
    } finally {
      setBusy(false);
      onBusyChange?.(false);
    }
  };
  const preview = { figure: { ...figure, portrait }, palette, name: name || t("society.portrait.preview_name") };
  const button = (selected: boolean) => cn(
    "rounded-md border px-2 py-1 text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
    selected ? "border-border-strong bg-secondary text-foreground" : "border-border text-muted-foreground hover:bg-secondary hover:text-foreground",
  );

  return <section className={cn("rounded-lg border border-border bg-card/70 p-3", className)} data-testid="agent-portrait-editor">
    <input ref={inputRef} type="file" accept="image/png,image/jpeg,image/webp" className="hidden"
      aria-label={t("society.portrait.upload")}
      onChange={(event) => {
        const file = event.target.files?.[0];
        event.target.value = "";
        if (file) void upload(file);
      }}
    />
    <div className="flex items-start gap-3">
      <AgentSwatch agent={preview} size={88} />
      <div className="min-w-0 flex-1">
        <h3 className="text-sm font-semibold text-foreground">{t("society.portrait.title")}</h3>
        <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">{t("society.portrait.hint")}</p>
        <div className="mt-2 flex flex-wrap gap-1">
          <button type="button" className={button(mode === "illustrated")} aria-pressed={mode === "illustrated"}
            onClick={() => onChange(illustratedDraft)} data-testid="portrait-mode-illustrated">
            {t("society.portrait.illustrated")}
          </button>
          <button type="button" className={button(mode === "figure")} aria-pressed={mode === "figure"}
            onClick={() => onChange("figure")} data-testid="portrait-mode-figure">
            {t("society.portrait.figure")}
          </button>
          {imageDraft ? <button type="button" className={button(mode === "image")} aria-pressed={mode === "image"}
            onClick={() => onChange(imageDraft)}>{t("society.portrait.image")}</button> : null}
          <button type="button" className={button(false)} disabled={busy}
            onClick={() => inputRef.current?.click()} data-testid="portrait-upload">
            {t("society.portrait.upload")}
          </button>
        </div>
      </div>
    </div>
    {illustrated ? <div className="mt-3 space-y-2 border-t border-border pt-3">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs text-muted-foreground">{t("society.portrait.local_hint")}</span>
        <button type="button" className={button(false)} onClick={shuffle} data-testid="portrait-shuffle">
          {t("society.portrait.shuffle")}
        </button>
      </div>
      {figure.archetype === "biped" ? <>
        <OptionRow label={t("society.portrait.face")} values={FACE_SHAPES} value={illustrated.face}
          labelFor={(value) => t(`society.portrait.face_${value}`)} onPick={(value) => edit({ face: value })} />
        <OptionRow label={t("society.portrait.hair")} values={HAIR_STYLES} value={illustrated.hair}
          labelFor={(value) => t(`society.portrait.hair_${value}`)} onPick={(value) => edit({ hair: value })} />
        <OptionRow label={t("society.portrait.detail")} values={FACE_DETAILS} value={illustrated.detail}
          labelFor={(value) => t(`society.portrait.detail_${value}`)} onPick={(value) => edit({ detail: value })} />
      </> : null}
      <OptionRow label={t("society.portrait.backdrop")} values={BACKDROPS} value={illustrated.backdrop}
        labelFor={(value) => t(`society.portrait.backdrop_${value}`)} onPick={(value) => edit({ backdrop: value })} />
      <p className="text-xs text-muted-foreground">{t("society.portrait.palette_hint")}</p>
    </div> : null}
    {error ? <p role="alert" className="mt-2 text-xs text-destructive">{error}</p> : null}
  </section>;
}

function OptionRow<T extends string>({ label, values, value, labelFor, onPick }: {
  label: string; values: readonly T[]; value: T; labelFor: (value: T) => string; onPick: (value: T) => void;
}) {
  return <div className="flex flex-wrap items-center gap-1.5">
    <span className="w-20 shrink-0 text-xs text-muted-foreground">{label}</span>
    {values.map((option) => <button key={option} type="button" aria-pressed={option === value}
      onClick={() => onPick(option)} data-testid={`portrait-option-${option}`}
      className={cn("rounded-md border px-2 py-1 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        option === value ? "border-border-strong bg-secondary text-foreground" : "border-border text-muted-foreground hover:bg-secondary hover:text-foreground")}>
      {labelFor(option)}
    </button>)}
  </div>;
}
