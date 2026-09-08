import { RotateCcw, Shuffle } from "lucide-react";
import { useT } from "@/i18n";
import { BrandedSelect } from "@/components/ui/select";
import { EDITABLE_CELLS, PALETTE_PRESETS, resolvePalette, shufflePalette, type FigureRecipe } from "../figures/figureRecipe";
import { CATALOG, catalogBaseFor, isReservedStyle, keepablePartsFor, partsForSlot, slotsWithParts } from "../figures/figureRegistry";

export function AgentStudioAppearance({ recipe, onChange, lead }: {
  recipe: FigureRecipe;
  onChange: (recipe: FigureRecipe) => void;
  lead: boolean;
}) {
  const t = useT();
  const palette = resolvePalette(recipe);
  const base = catalogBaseFor(recipe);
  const slots = recipe.model ? [] : slotsWithParts(recipe.archetype, recipe.style ?? null, base?.family ?? null, base?.fitSize ?? null);
  return (
    <div className="as-stack">
      <div className="as-note">{t("society.studio.preview_hint")}</div>
      {!lead ? <label className="as-field">
        <span>{t("society.studio.character")}</span>
        <BrandedSelect
          ariaLabel={t("society.studio.character")}
          value={recipe.model ? "imported" : base?.id ?? `${recipe.archetype}/${recipe.base}`}
          onValueChange={(value) => {
            const next = CATALOG.bases.find((entry) => entry.id === value);
            if (!next) return;
            onChange({ contract: 1, archetype: next.archetype, base: next.base, style: next.styles[0],
              heightM: next.heightM, palette: { ...next.palette },
              parts: keepablePartsFor(recipe.parts, next.archetype, next.styles[0], next.family ?? null, next.fitSize) });
          }}
          options={[
            ...(recipe.model ? [{ value: "imported", label: t("society.studio.imported") }] : []),
            ...CATALOG.bases.filter((entry) => !entry.styles.some(isReservedStyle)).map((entry) => ({
              value: entry.id,
              label: entry.label,
            })),
          ]}
        />
      </label> : null}
      <div className="as-section-title"><h3>{t("society.studio.palette")}</h3>
        <button type="button" className="as-text-button" onClick={() => onChange({ ...recipe, palette: shufflePalette() })}>
          <Shuffle size={14} aria-hidden />{t("society.studio.shuffle")}
        </button>
      </div>
      <div className="as-presets">
        {PALETTE_PRESETS.map((preset) => <button type="button" key={preset.id}
          className="as-preset" onClick={() => onChange({ ...recipe, palette: { ...preset.palette } })}>
          <span className="as-preset-colors" aria-hidden>{["primary", "secondary", "accent"].map((cell) =>
            <span key={cell} style={{ backgroundColor: preset.palette[cell as "primary"] }} />)}</span>
          {t(`society.presets.${preset.labelKey}`)}
        </button>)}
      </div>
      <div className="as-color-grid">
        {EDITABLE_CELLS.map((cell) => <label key={cell} className="as-color">
          <input type="color" value={palette[cell]} onChange={(event) => {
            const next = { ...recipe.palette, [cell]: event.target.value };
            if (cell === "skin") delete next.skin_shade;
            if (cell === "primary") delete next.primary_shade;
            if (cell === "secondary") delete next.secondary_shade;
            onChange({ ...recipe, palette: next });
          }} />
          <span>{t(`society.cell.${cell}`)}</span>
          <small>{palette[cell].toUpperCase()}</small>
        </label>)}
      </div>
      {slots.length ? <h3 className="as-section-title">{t("society.studio.wardrobe")}</h3> : null}
      <div className="as-grid">
        {slots.map((slot) => <label className="as-field" key={slot}>
          <span>{t(`society.slot.${slot}`)}</span>
          <BrandedSelect
            ariaLabel={t(`society.slot.${slot}`)}
            value={recipe.parts[slot] ?? ""}
            onValueChange={(value) => onChange({ ...recipe, parts: { ...recipe.parts, [slot]: value } })}
            options={[
              { value: "", label: t("society.studio.none") },
              ...partsForSlot(slot, recipe.archetype, recipe.style ?? null, base?.family ?? null, base?.fitSize ?? null)
                .map((part) => ({ value: part.id, label: part.label })),
            ]}
          />
        </label>)}
      </div>
      <label className="as-field"><span>{t("society.create.height")} <output>{(recipe.heightM ?? base?.heightM ?? 1.75).toFixed(2)} m</output></span>
        <input type="range" min={recipe.model ? 0.6 : (base?.heightM ?? 1.75) * 0.75}
          max={recipe.model ? 2.4 : (base?.heightM ?? 1.75) * 1.25} step="0.01"
          value={recipe.heightM ?? base?.heightM ?? 1.75} onChange={(event) => onChange({ ...recipe, heightM: Number(event.target.value) })} />
      </label>
      <button className="as-text-button" type="button" onClick={() => onChange({ ...recipe, palette: {}, heightM: base?.heightM, parts: {} })}>
        <RotateCcw size={14} aria-hidden />{t("society.studio.reset_look")}
      </button>
    </div>
  );
}
