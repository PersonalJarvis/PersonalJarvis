import { useT } from "@/i18n";
import type { ModelAccess } from "@/lib/modelAccess";
import { cn } from "@/lib/utils";

/** A separate, labelled choice of how the selected provider is accessed. */
export function ModelAccessSwitch({ options, value, onChange, disabled = false }: {
  options: { kind: ModelAccess; disabled?: boolean }[];
  value: ModelAccess;
  onChange: (kind: ModelAccess) => void;
  disabled?: boolean;
}) {
  const t = useT();
  if (options.length < 2) return null;
  return <div className="shrink-0 border-b border-border px-3 py-2" data-model-access-switch
    onKeyDown={(event) => {
      // The surrounding model list must not consume the radio group's keys.
      if (event.key !== "Escape") event.stopPropagation();
    }}>
    <div className="mb-1.5 text-[11px] font-medium text-muted-foreground">{t("agent_chat.model_access")}</div>
    <div role="radiogroup" aria-label={t("agent_chat.model_access")} className="flex gap-1 rounded-md bg-secondary/60 p-0.5">
      {options.map((option) => <button key={option.kind} type="button" role="radio"
        aria-checked={option.kind === value} disabled={disabled || option.disabled}
        tabIndex={option.kind === value ? 0 : -1}
        onClick={() => onChange(option.kind)}
        onKeyDown={(event) => {
          if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) return;
          event.preventDefault();
          const available = options.filter((entry) => !entry.disabled);
          const index = available.findIndex((entry) => entry.kind === option.kind);
          const next = event.key === "Home" ? 0 : event.key === "End" ? available.length - 1
            : (index + (["ArrowLeft", "ArrowUp"].includes(event.key) ? -1 : 1) + available.length) % available.length;
          const choice = available[next];
          if (choice) {
            onChange(choice.kind);
            event.currentTarget.parentElement?.querySelector<HTMLButtonElement>(`[data-access="${choice.kind}"]`)?.focus();
          }
        }} data-access={option.kind}
        className={cn("min-w-0 flex-1 rounded px-2 py-1 text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-45",
          option.kind === value ? "bg-popover font-medium text-popover-foreground shadow-sm" : "text-muted-foreground hover:text-foreground")}>
        {t(`agent_chat.access_${option.kind}`)}
      </button>)}
    </div>
  </div>;
}
