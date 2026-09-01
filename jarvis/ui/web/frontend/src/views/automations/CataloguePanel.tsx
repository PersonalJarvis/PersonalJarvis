/**
 * The catalogue of ready-made automations.
 *
 * The old layout printed one grid per category down the page, so a category
 * holding a single template left two thirds of a row empty and the page read
 * as mostly whitespace. Categories are a filter now, not five separate grids:
 * one chip row, one grid, three columns inside the section's content width.
 *
 * The first tile is deliberately not a template — it is "build your own",
 * because the catalogue is where someone looks when they want an automation,
 * and the only way to make a custom one used to be a small button in the far
 * corner of the header.
 */
import { useMemo, useState } from "react";
import { Plus, Wand2 } from "lucide-react";
import { SegmentedFilter } from "@/components/extensions/primitives";
import { useT } from "@/i18n";
import { CatalogueCard } from "./CatalogueCard";
import {
  TEMPLATE_CATEGORIES,
  type AutomationTemplate,
  type TemplateCategory,
} from "./automationsModel";

type CategoryFilter = TemplateCategory | "all";

export interface CataloguePanelProps {
  templates: AutomationTemplate[];
  /** Template key → the user's task created from it, when installed. */
  installedByKey: Map<string, string>;
  onAdd: (template: AutomationTemplate) => void;
  onShowInstalled: (taskId: string) => void;
  onCreateCustom: () => void;
}

export function CataloguePanel({
  templates,
  installedByKey,
  onAdd,
  onShowInstalled,
  onCreateCustom,
}: CataloguePanelProps) {
  const t = useT();
  const [category, setCategory] = useState<CategoryFilter>("all");

  // Only categories that actually hold something become chips — an empty
  // "Finance" chip that filters to nothing is a dead end, not a feature.
  const options = useMemo(() => {
    const present = TEMPLATE_CATEGORIES.filter((c) =>
      templates.some((tpl) => tpl.category === c),
    );
    return [
      { id: "all" as CategoryFilter, label: t("automations_view.category_all"), count: templates.length },
      ...present.map((c) => ({
        id: c as CategoryFilter,
        label: t(`automations_view.category.${c}`),
        count: templates.filter((tpl) => tpl.category === c).length,
      })),
    ];
  }, [templates, t]);

  const shown = useMemo(
    () => (category === "all" ? templates : templates.filter((tpl) => tpl.category === category)),
    [templates, category],
  );

  return (
    <div className="space-y-group">
      <SegmentedFilter<CategoryFilter>
        label={t("automations_view.category_label")}
        value={category}
        onChange={setCategory}
        options={options}
      />
      <div className="grid grid-cols-1 gap-stack sm:grid-cols-2 xl:grid-cols-3">
        {category === "all" && <CustomTile onClick={onCreateCustom} />}
        {shown.map((tpl) => (
          <CatalogueCard
            key={tpl.key}
            template={tpl}
            installedTaskId={installedByKey.get(tpl.key)}
            onAdd={onAdd}
            onShowInstalled={onShowInstalled}
          />
        ))}
      </div>
    </div>
  );
}

/**
 * "Build your own" — the same size as a template card, drawn as an offer.
 *
 * A real card with a dashed rim (the one thing that says "not a template
 * yet"), lifting on hover like every other row in the app. The wand used to
 * be painted --primary inside a --primary-tinted tile, which made the ONE
 * tile in the grid that holds no automation the brightest object in it.
 */
function CustomTile({ onClick }: { onClick: () => void }) {
  const t = useT();
  return (
    <button
      type="button"
      onClick={onClick}
      data-testid="catalogue-custom"
      className="flex min-h-[148px] flex-col items-start gap-2 rounded-lg border border-dashed border-border-strong bg-card p-4 text-left shadow-rim transition-colors hover:bg-secondary"
    >
      <span className="grid h-9 w-9 place-items-center rounded-md bg-secondary">
        <Wand2 className="h-4 w-4 text-muted-foreground" />
      </span>
      <span className="text-title font-semibold text-foreground-strong">
        {t("automations_view.custom_title")}
      </span>
      <span className="text-meta text-muted-foreground">
        {t("automations_view.custom_description")}
      </span>
      <span className="mt-auto inline-flex items-center gap-1 text-meta font-medium text-foreground">
        <Plus className="h-3.5 w-3.5" />
        {t("automations_view.new_button")}
      </span>
    </button>
  );
}
