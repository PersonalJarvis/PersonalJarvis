/**
 * A catalogue entry: icon, name, description, default schedule and Add.
 *
 * The card carries a minimum height so a one-sentence template and a
 * three-sentence one line up in the same row instead of stair-stepping — the
 * ragged grid was most of what made the section look thrown together. When
 * the template is already installed the card says so and links to the user's
 * row; when a required plugin is missing it carries a quiet "needs Gmail"
 * line (Add still works — the dialog repeats the warning).
 */
import { Check, Clock, Plus } from "lucide-react";
import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { templateIcon } from "./automationIcons";
import { humanizeMissing, type AutomationTemplate } from "./automationsModel";

export interface CatalogueCardProps {
  template: AutomationTemplate;
  /** Id of the user's task created from this template, if installed. */
  installedTaskId?: string;
  onAdd: (template: AutomationTemplate) => void;
  onShowInstalled: (taskId: string) => void;
}

export function CatalogueCard({
  template,
  installedTaskId,
  onAdd,
  onShowInstalled,
}: CatalogueCardProps) {
  const t = useT();
  const Icon = templateIcon(template.icon);
  const installed = Boolean(installedTaskId);
  const needsSomething = !template.ready && template.missing.length > 0;

  return (
    <article
      data-testid={`catalogue-${template.key}`}
      className={cn(
        "card-outline flex min-h-[148px] flex-col p-4",
        installed && "border-primary/40",
      )}
    >
      <div className="flex items-start gap-3">
        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg border border-border bg-secondary/40">
          <Icon className="h-4 w-4 text-primary" />
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-sm font-semibold text-foreground">{template.name}</h3>
          <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-muted-foreground">
            {template.description}
          </p>
        </div>
      </div>

      {needsSomething && (
        <p className="mt-2 truncate text-[11px] text-amber-600 dark:text-amber-400/90">
          {fill(t("automations_view.needs"), { tools: humanizeMissing(template.missing) })}
        </p>
      )}

      <div className="mt-auto flex items-center gap-2 pt-3">
        <span className="inline-flex min-w-0 items-center gap-1.5 text-[11px] text-muted-foreground">
          <Clock className="h-3 w-3 shrink-0" />
          <span className="truncate">{template.schedule_label}</span>
        </span>
        <span className="ml-auto shrink-0">
          {installed && installedTaskId ? (
            <button
              type="button"
              onClick={() => onShowInstalled(installedTaskId)}
              title={t("automations_view.show_installed")}
              className="inline-flex h-7 items-center gap-1.5 rounded-md border border-primary/40 px-2.5 text-xs font-medium text-primary transition-colors hover:bg-primary/10"
            >
              <Check className="h-3.5 w-3.5" />
              {t("automations_view.added")}
            </button>
          ) : (
            <button
              type="button"
              onClick={() => onAdd(template)}
              className="inline-flex h-7 items-center gap-1.5 rounded-md bg-sheen/[0.08] px-2.5 text-xs font-medium text-foreground transition-colors hover:bg-sheen/[0.16]"
            >
              <Plus className="h-3.5 w-3.5" />
              {t("automations_view.add")}
            </button>
          )}
        </span>
      </div>
    </article>
  );
}
