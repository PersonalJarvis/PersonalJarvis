/**
 * A catalogue entry: icon, name, description, default schedule and the Add
 * button. When the template is already installed the card says so and links
 * to the user's card; when a required plugin is missing it carries a quiet
 * "needs Gmail" hint (Add still works — the dialog repeats the warning).
 */
import { Check, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
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

export function CatalogueCard({ template, installedTaskId, onAdd, onShowInstalled }: CatalogueCardProps) {
  const t = useT();
  const Icon = templateIcon(template.icon);
  const installed = Boolean(installedTaskId);

  return (
    <article
      data-testid={`catalogue-${template.key}`}
      className={cn("card-outline flex flex-col", installed && "border-primary/30")}
    >
      <div className="flex items-start gap-3 p-4">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-border bg-secondary/40">
          <Icon className="h-4 w-4 text-primary" />
        </div>
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-sm font-semibold">{template.name}</h3>
          <p className="mt-0.5 line-clamp-3 text-xs leading-relaxed text-muted-foreground">
            {template.description}
          </p>
        </div>
      </div>
      <div className="mt-auto flex items-center gap-2 border-t border-border/60 px-4 py-2.5">
        <div className="min-w-0 flex-1">
          <div className="truncate text-[11px] text-muted-foreground">{template.schedule_label}</div>
          {!template.ready && template.missing.length > 0 && (
            <div className="truncate text-[11px] text-amber-600 dark:text-amber-400/90">
              {fill(t("automations_view.needs"), { tools: humanizeMissing(template.missing) })}
            </div>
          )}
        </div>
        {installed && installedTaskId ? (
          <button
            type="button"
            onClick={() => onShowInstalled(installedTaskId)}
            className="inline-flex items-center gap-1 rounded-md border border-primary/40 px-2.5 py-1 text-xs text-primary transition-colors hover:bg-primary/10"
            title={t("automations_view.show_installed")}
          >
            <Check className="h-3.5 w-3.5" />
            {t("automations_view.added")}
          </button>
        ) : (
          <Button size="sm" variant="outline" className="h-7 px-2.5 text-xs" onClick={() => onAdd(template)}>
            <Plus className="mr-1 h-3.5 w-3.5" />
            {t("automations_view.add")}
          </Button>
        )}
      </div>
    </article>
  );
}
